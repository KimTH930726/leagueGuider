"""
Query Rewrite — 한국어 검색 질의 정제 및 확장.

동작 순서:
  1. Rule-based: AI 에이전트 도메인 동의어 사전 기반 확장 (항상 동작, 레이턴시 없음)
  2. LLM-based: LLM 설정 + use_llm=True 일 때만 추가 확장
     → LLM 실패 시 rule-based 결과만 사용 (graceful fallback)

원칙:
  - precision을 해치지 않는 선에서 recall 향상
  - 확장어를 원문에 덮어쓰지 않고 '추가' 검색어로만 활용
  - 원문 질의는 항상 최우선
"""
import json
import re
from dataclasses import dataclass, field

# AI 에이전트 포털 도메인 특화 동의어 사전
_SYNONYMS: dict[str, list[str]] = {
    # AI / 모델
    "챗봇":     ["챗봇", "대화형AI", "상담봇"],
    "상담":     ["상담", "고객응대", "고객지원"],
    "에이전트": ["에이전트", "AI에이전트", "봇"],
    "LLM":      ["LLM", "GPT", "언어모델"],
    "OCR":      ["OCR", "문서인식"],
    "번역":     ["번역", "다국어"],
    "추천":     ["추천", "맞춤형", "개인화"],
    "예측":     ["예측", "예측모델"],
    "분류":     ["분류", "카테고리"],
    "요약":     ["요약", "자동요약", "문서요약"],
    "분석":     ["분석", "데이터분석", "인사이트"],
    "검색":     ["검색", "탐색", "조회"],
    # 자동화 도구
    "자동화":   ["자동화", "RPA", "자동처리"],
    "RPA":      ["RPA", "자동화", "반복업무"],
    "PAD":      ["PAD", "Power Automate", "데스크탑자동화"],
    "n8n":      ["n8n", "워크플로우", "파이프라인"],
    "스케줄":   ["스케줄", "주기실행", "배치"],
    # 협업 / 알림
    "Teams":    ["Teams", "팀즈", "협업툴"],
    "알림":     ["알림", "노티", "멘션", "푸시알림"],
    "메일":     ["메일", "이메일", "Outlook", "수신함"],
    # 데이터 / 시스템
    "데이터":   ["데이터", "정보"],
    "문서":     ["문서", "보고서"],
    "ERP":      ["ERP", "레거시", "배치시스템"],
    "오류":     ["오류", "에러", "장애", "이슈"],
    "모니터링": ["모니터링", "감지", "감시", "알람"],
    "아카이빙": ["아카이빙", "보관", "이력관리"],
    "중복":     ["중복방지", "재전송방지"],
    # 비즈니스
    "고객":     ["고객", "사용자", "customer"],
    "업무":     ["업무", "프로세스", "워크플로"],
    "성과":     ["성과", "효과", "개선", "절감"],
    "이상":     ["이상감지", "이상탐지", "모니터링"],
}

_LLM_PROMPT = """\
아래 검색 질의를 사내 AI 에이전트 문서 검색에 최적화된 검색어로 확장하세요.
원문 의도를 유지하면서 동의어, 관련 기술 용어, 한국어 변형을 추가하세요.
JSON 배열로만 답하세요 (최대 4개, 설명 없이 배열만):

입력: {query}
출력: ["검색어1", "검색어2", ...]"""


@dataclass
class RewriteResult:
    original: str
    expanded: list[str] = field(default_factory=list)
    llm_used: bool = False

    @property
    def all_terms(self) -> list[str]:
        """원문 포함 전체 검색어 목록 (중복 제거, 원문 최우선)."""
        seen: set[str] = set()
        result: list[str] = []
        for t in [self.original] + self.expanded:
            if t and t not in seen:
                seen.add(t)
                result.append(t)
        return result

    @property
    def vector_query(self) -> str:
        """벡터 검색에 사용할 확장 문장 — 원문 + 상위 2개 확장어."""
        if self.expanded:
            return f"{self.original} {' '.join(self.expanded[:2])}"
        return self.original


def rewrite(query: str, llm=None) -> RewriteResult:
    """
    검색 질의 정제·확장.
    llm=None 또는 호출 실패 시 rule-based 결과만 반환.
    """
    expanded = _rule_expand(query)
    llm_used = False

    if llm is not None:
        try:
            llm_terms = _llm_expand(query, llm)
            existing = set(expanded)
            for t in llm_terms:
                if t and t not in existing and t != query:
                    expanded.append(t)
                    existing.add(t)
            llm_used = bool(llm_terms)
        except Exception:
            pass

    return RewriteResult(original=query, expanded=expanded[:5], llm_used=llm_used)


def _rule_expand(query: str) -> list[str]:
    """동의어 사전 기반 확장어 생성."""
    tokens = re.split(r"\s+", query.strip())
    candidates: list[str] = []
    seen = {query}

    for token in tokens:
        for keyword, synonyms in _SYNONYMS.items():
            if keyword in token or token in keyword:
                for syn in synonyms:
                    if syn not in seen:
                        candidates.append(syn)
                        seen.add(syn)

    return candidates[:8]


def _llm_expand(query: str, llm) -> list[str]:
    """LLM 기반 확장. JSON 파싱 실패 시 빈 리스트."""
    raw = llm.generate(_LLM_PROMPT.format(query=query))
    match = re.search(r"\[.*?\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        terms = json.loads(match.group())
        return [str(t).strip() for t in terms if isinstance(t, str) and t.strip()]
    except (json.JSONDecodeError, ValueError):
        return []
