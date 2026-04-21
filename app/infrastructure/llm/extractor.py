"""
LLM 기반 문서 메타데이터 자동 추출.

비용 절감 전략:
  1. content_hash 미변경 시 추출 스킵 (이미 추출된 문서 재처리 안 함)
  2. cleaned_body를 MAX_CONTENT_CHARS 로 잘라서 입력
  3. 최소 길이 미만 문서는 LLM 호출 없이 fallback
  4. LLM 거부 응답(민감정보 등) → 즉시 "추출불가" 마킹, 재시도 없음
  5. LLM 실패 시 sync 중단 없이 fallback 저장

출력 JSON schema:
  {
    "agent_name":       string,          # 에이전트명 (없으면 문서 제목)
    "one_line_summary": string,          # 한 줄 요약 (50자 이내)
    "problem":          string | null,   # 해결하려는 문제
    "solution":         string | null,   # 해결 방법 요약
    "tech_stack":       string[],        # 사용 기술 목록
    "effects":          string[],        # 기대효과/성과 목록
    "keywords":         string[],        # 핵심 키워드
    "category":         string           # RPA|챗봇|분석|자동화|데이터|기타
  }
"""

import json
import re
import time
from typing import Optional

from app.infrastructure.llm.base import LLMProviderBase
from app.shared.logger import get_logger

logger = get_logger()

MAX_CONTENT_CHARS = 4000   # 약 1000~1200 토큰
MIN_CONTENT_CHARS = 80     # 이 미만은 LLM 호출 건너뜀

VALID_CATEGORIES = {"RPA", "챗봇", "분석", "자동화", "데이터", "기타"}
CATEGORY_BLOCKED = "추출불가"  # LLM 거부 응답으로 추출 불가 처리된 문서

# LLM 거부 응답 감지 패턴 (InHouse LLM이 보안·민감정보 이유로 처리 거부하는 경우)
_REFUSAL_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"민감(한)?\s*(정보|데이터|내용)",
        r"개인\s*정보",
        r"보안\s*(상|관련|정책|이유)",
        r"(답변|처리|제공|응답)\s*(드리기|하기)?\s*(어렵|불가|할\s*수\s*없)",
        r"죄송.{0,30}(제공|처리|답변)",
        r"공개\s*(할|하기)\s*(어렵|불가|수\s*없)",
        r"부적절",
        r"I\s*cannot\s*(help|process|provide|answer)",
        r"I\s*am\s*(sorry|unable)",
        r"cannot\s*(be\s*processed|provide)",
    ]
]

_SYSTEM_PROMPT = """\
당신은 사내 AI 에이전트 소개 문서를 분석하는 전문가입니다.
아래 문서에서 메타데이터를 추출해 JSON으로만 답하세요.
설명, 마크다운 코드블록, 추가 텍스트 없이 순수 JSON 객체만 출력하세요.
내용이 불분명해도 최대한 추론해서 채우세요. null은 정말 알 수 없을 때만 사용하세요.\
"""

_USER_PROMPT = """\
## 문서 제목
{title}

## 문서 내용 (일부)
{content}

## 추출 항목 (JSON으로만 출력)
{{
  "agent_name": "에이전트명 또는 시스템명. 없으면 문서 제목 그대로.",
  "one_line_summary": "이 에이전트가 무엇을 하는지 한 줄로 (50자 이내). 예: 'SAP 구매 데이터를 자동 분석해 이상 징후를 탐지하는 AI 에이전트'",
  "problem": "어떤 업무 문제를 해결하는가. 예: '수작업 데이터 입력으로 인한 오류 및 시간 낭비'. 명확히 없으면 null.",
  "solution": "어떻게 해결하는가. 예: 'RPA로 시스템 간 데이터 이관 자동화 및 GPT 기반 이상 탐지'. 명확히 없으면 null.",
  "tech_stack": ["사용된 기술·도구·플랫폼. 예: Python, UiPath, GPT-4, SAP, Power BI, LangChain"],
  "effects": ["기대효과 또는 성과. 예: 업무시간 70% 절감, 오류율 90% 감소, 월 40시간 절약"],
  "keywords": ["핵심 키워드 3~5개. 예: 자동화, 이상탐지, 구매관리"],
  "category": "아래 6개 중 가장 적합한 것 하나만: RPA(반복업무자동화), 챗봇(대화형AI), 분석(데이터분석/인사이트), 자동화(RPA외 자동화), 데이터(데이터파이프라인/ETL), 기타"
}}\
"""


def _is_refusal(raw: str) -> bool:
    """LLM 거부/차단 응답 여부 감지."""
    for pat in _REFUSAL_PATTERNS:
        if pat.search(raw):
            return True
    return False


class MetadataExtractor:
    def __init__(self, llm: LLMProviderBase):
        self.llm = llm

    def extract(self, title: str, content: str, max_retries: int = 2) -> dict:
        """
        문서 → 메타데이터 dict 반환.

        처리 흐름:
          1. 콘텐츠가 너무 짧으면 → fallback (재시도 없음)
          2. LLM 호출 후 거부 응답 감지 → 즉시 "추출불가" 마킹 (재시도 없음)
          3. JSON 파싱 실패 또는 summary 비어있으면 최대 max_retries회 재시도
          4. 재시도 후 summary 여전히 빈 경우 → 제목으로 보완
          5. 완전 실패 시 → fallback (제목 보완 포함)
        """
        if not content or len(content) < MIN_CONTENT_CHARS:
            logger.debug(f"[추출 스킵] 문서 너무 짧음: {title!r}")
            return self._fallback(title)

        truncated = content[:MAX_CONTENT_CHARS]
        prompt = _USER_PROMPT.format(title=title, content=truncated)

        partial_result = None  # JSON 파싱은 됐지만 summary가 빈 경우 보관

        for attempt in range(1, max_retries + 1):
            try:
                raw = self._call_llm(prompt)

                # ── 거부 응답 감지 ─────────────────────────────────────────
                if _is_refusal(raw):
                    logger.warning(f"[추출불가] LLM 거부 응답 감지: {title!r}")
                    return self._blocked(title)

                result = self._parse_json(raw)
                result = self._validate_and_fix(result, title)
                db_dict = self._to_db_dict(result)

                if db_dict.get("one_line_summary"):
                    logger.info(f"[추출 완료] {title!r} → category={result.get('category')}")
                    return db_dict

                # summary만 비어있는 경우: 다른 필드는 살리고 재시도
                partial_result = db_dict
                logger.warning(
                    f"[추출 재시도 {attempt}/{max_retries}] one_line_summary 비어있음: {title!r}"
                )
            except json.JSONDecodeError as e:
                logger.warning(f"[추출 JSON 파싱 실패 {attempt}/{max_retries}] {title!r}: {e}")
            except Exception as e:
                logger.warning(f"[추출 실패 {attempt}/{max_retries}] {title!r}: {e}")

            if attempt < max_retries:
                time.sleep(1)

        # 모든 재시도 소진: one_line_summary를 제목으로 보완
        if partial_result is not None:
            partial_result["one_line_summary"] = title[:100]
            logger.info(f"[추출 완료-제목 보완] {title!r}")
            return partial_result

        # JSON 파싱 자체 실패: fallback + 제목 보완
        logger.warning(f"[추출 완전 실패] {title!r} — 제목으로 요약 대체")
        return self._fallback(title)

    def _call_llm(self, user_prompt: str) -> str:
        combined = f"{_SYSTEM_PROMPT}\n\n{user_prompt}"
        return self.llm.generate(combined)

    def _parse_json(self, raw: str) -> dict:
        """응답에서 JSON 객체를 추출. ```json ... ``` 블록 허용."""
        cleaned = re.sub(r"```(?:json)?\s*", "", raw)
        cleaned = cleaned.replace("```", "").strip()

        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise json.JSONDecodeError("JSON 객체 없음", cleaned, 0)

        return json.loads(match.group())

    def _validate_and_fix(self, data: dict, title: str) -> dict:
        """필드 타입 검증 및 보정."""
        def to_str_list(val) -> list[str]:
            if isinstance(val, list):
                return [str(v) for v in val if v]
            return []

        return {
            "agent_name": str(data.get("agent_name") or title).strip()[:100],
            "one_line_summary": str(data.get("one_line_summary") or "").strip()[:100],
            "problem": str(data.get("problem") or "").strip() or None,
            "solution": str(data.get("solution") or "").strip() or None,
            "tech_stack": to_str_list(data.get("tech_stack")),
            "effects": to_str_list(data.get("effects")),
            "keywords": to_str_list(data.get("keywords"))[:10],
            "category": data.get("category", "기타") if data.get("category") in VALID_CATEGORIES else "기타",
        }

    def _to_db_dict(self, validated: dict) -> dict:
        """DB upsert_metadata 형식으로 변환."""
        return {
            "agent_name": validated["agent_name"],
            "one_line_summary": validated["one_line_summary"],
            "problem": validated.get("problem"),
            "solution": validated.get("solution"),
            "tech_stack_json": json.dumps(validated["tech_stack"], ensure_ascii=False),
            "effects_json": json.dumps(validated["effects"], ensure_ascii=False),
            "keywords_json": json.dumps(validated["keywords"], ensure_ascii=False),
            "stage": None,
            "category": validated["category"],
        }

    def _fallback(self, title: str) -> dict:
        """LLM 없이 저장할 최소 메타데이터. one_line_summary는 제목으로 보완."""
        return {
            "agent_name": title,
            "one_line_summary": title[:100],
            "problem": None,
            "solution": None,
            "tech_stack_json": "[]",
            "effects_json": "[]",
            "keywords_json": "[]",
            "stage": None,
            "category": "기타",
        }

    def _blocked(self, title: str) -> dict:
        """LLM 거부 응답으로 추출 불가 처리된 문서. 재시도 대상에서 영구 제외."""
        return {
            "agent_name": title,
            "one_line_summary": title[:100],
            "problem": None,
            "solution": None,
            "tech_stack_json": "[]",
            "effects_json": "[]",
            "keywords_json": "[]",
            "stage": None,
            "category": CATEGORY_BLOCKED,
        }


def _is_fallback_metadata(meta: dict) -> bool:
    """
    LLM 없이 저장된 빈/부분 fallback 메타인지 판단.
    "추출불가" 문서는 재추출 대상에서 제외.
    tech_stack·problem·category(기타) 세 필드 중 2개 이상 비어있으면 재추출 대상.
    """
    if meta.get("category") == CATEGORY_BLOCKED:
        return False  # 거부 응답으로 확정된 문서 — 재시도 불필요
    empty = 0
    if not meta.get("problem"):
        empty += 1
    if meta.get("category", "기타") == "기타":
        empty += 1
    if meta.get("tech_stack_json", "[]") in ("[]", "", None):
        empty += 1
    return empty >= 2


def should_extract(
    doc_id: int,
    new_hash: str,
    local_meta: dict,
    existing_metadata: Optional[dict],
) -> bool:
    """
    추출 필요 여부 판단.

    추출 조건:
      1. 기존 메타데이터 없음 (최초)
      2. content_hash 변경 (내용 갱신)
      3. 기존 메타가 fallback(빈 값) — LLM 재시도 기회
    "추출불가" 문서는 content_hash가 바뀌지 않는 한 재시도하지 않음.
    """
    if existing_metadata is None:
        return True
    if existing_metadata.get("category") == CATEGORY_BLOCKED:
        # 내용이 바뀐 경우에만 재시도 (페이지 수정 후 민감정보 제거됐을 수 있음)
        old_hash = local_meta.get("content_hash", "")
        return old_hash != new_hash
    if _is_fallback_metadata(existing_metadata):
        return True
    old_hash = local_meta.get("content_hash", "")
    return old_hash != new_hash
