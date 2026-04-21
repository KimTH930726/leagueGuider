import json
import re
import threading
from collections import Counter
from datetime import datetime, timedelta
from app.shared.text_utils import now_kst, now_kst_str
from typing import Optional

from app.shared.config import AppConfig
from app.shared.exceptions import ReportError
from app.shared.logger import get_logger
from app.infrastructure.db.connection import db_session
from app.infrastructure.db.document_repository import DocumentRepository
from app.infrastructure.db.report_repository import ReportRepository

logger = get_logger()

_report_lock = threading.Lock()


_PLACEHOLDER_URL_RE = re.compile(
    r"""
    \(                          # 여는 괄호
    \s*                         # 선행 공백
    (?:                         # 플레이스홀더 패턴들
        |                       # 빈 값
        \#|                     # #
        URL|                    # 'URL'
        실제[_\s]?Confluence[_\s]?URL|   # 한국어 플레이스홀더
        실제[_\s]?URL|
        실제[_\s]?에이전트[_\s]?URL|
        Confluence[_\s]?URL|
        YOUR[_\s]?URL|
        INSERT[_\s]?URL
    )
    \s*                         # 후행 공백
    \)                          # 닫는 괄호
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _inject_missing_urls(text: str, url_map: dict[str, str]) -> str:
    """
    LLM 출력에서 URL이 빠지거나 플레이스홀더로 채워진 마크다운 링크를 url_map으로 보완.

    처리 대상:
      1. [에이전트명]()                    → 빈 괄호
      2. [에이전트명](실제_Confluence_URL) → 한국어 플레이스홀더
      3. [에이전트명](URL), [에이전트명](#) → 영문 플레이스홀더
      4. 헤딩 줄에 링크 없이 이름만 있는 경우
    """
    if not url_map:
        return text

    # 1 & 2 & 3: [이름](<플레이스홀더>) 형태 — 이름으로 url_map 조회 후 교체
    def fix_bad_link(m: re.Match) -> str:
        name = m.group(1)
        url = url_map.get(name, "")
        return f"[{name}]({url})" if url else f"[{name}]()"

    # 빈 괄호 + 플레이스홀더 패턴을 하나의 정규식으로 처리
    bad_link_re = re.compile(
        r"\[([^\[\]]+)\]" + _PLACEHOLDER_URL_RE.pattern,
        re.VERBOSE | re.IGNORECASE,
    )
    text = bad_link_re.sub(fix_bad_link, text)

    # 4. 헤딩(## ~ ####)에서 실제 URL 없이 이름만 텍스트로 있는 경우 보완
    lines = text.splitlines()
    result = []
    for line in lines:
        if re.match(r"^#{2,4}\s", line):
            for name, url in url_map.items():
                if name in line and f"]({url})" not in line and f"](" not in line:
                    line = line.replace(name, f"[{name}]({url})", 1)
                    break
        result.append(line)

    return "\n".join(result)

# ─────────────────────────── 공통 입력 데이터 섹션 ───────────────────────────
_INPUT_SECTION = """\
**분석 기간**: {period_start} ~ {period_end}
**3기간 에이전트 현황**: 전전기({prev_prev_period_label}) {prev_prev_count}건 → 전기({prev_period_label}) {prev_count}건 → **이번 {doc_count}건** | 전체 누적 {total_count}건

⚠️ **데이터 품질 (분석 신뢰도 참고)**: 메타 추출 {dq_pct_meta}% | 기술스택 {dq_pct_tech}% | 카테고리 {dq_pct_cat}%
(품질이 낮은 항목은 문서 본문에서 직접 추출하고, 리포트 내 신뢰도 평가 시 이 수치를 반영하세요.)

### 전기 리포트 핵심 요약 (연속성 참고 — 전기 리스크 해소 여부를 이번 분석에서 검토하세요)
{prev_report_excerpt}

### 3기간 카테고리 추이
(▲▲=지속 성장, ▼▼=지속 감소, ▲=이번 증가, ▼=이번 감소, ─=변화 없음)
{category_comparison}

### 3기간 기술스택 Top 5 추이
{tech_trend}

### 에이전트 목록 및 본문 요약 (최대 25건)
(제목·URL·요약·문제·본문 포함 — 에이전트 언급 시 반드시 [에이전트명](URL) 마크다운 링크 포함)
{doc_list}

### 해결하는 문제 유형 (problem 필드)
{problem_summary}

### 기술스택 빈도 — 이번 기간
{tech_freq}

### 기대효과 빈도 — 이번 기간
{effects_freq}

### 작성자별 기여도 (상위 10명)
{author_freq}"""

# ─────────────────────────── 리더십 관점 프롬프트 ────────────────────────────
_LEADERSHIP_PROMPT = """\
당신은 기업 AI 도입 현황을 분석하는 전략 전문가입니다.
아래 데이터를 분석해 **경영진/리더십용 AI 도입 의사결정 지원 리포트**를 작성하세요.
반드시 **마크다운(Markdown) 형식**으로 작성하고 표(table)를 적극 활용하세요.

⚠️ 핵심 지침:
1. 데이터가 있는 항목만 작성하세요. 빈 값(—, 없음, 미분류)만 나오는 섹션은 통째로 생략하세요.
2. 메타데이터(카테고리/기술스택/기대효과)가 없으면 아래 **문서 본문**을 직접 분석해 채우세요.
3. 문서 본문에서도 파악 불가한 섹션은 생략하세요. 빈 표를 절대 출력하지 마세요.
4. 숫자와 근거를 구체적으로 작성하세요.
5. 3기간 추이(전전기→전기→이번)를 분석해 가속/감속/역전 패턴을 반드시 언급하세요.
6. 특정 에이전트를 언급할 때는 반드시 **[에이전트명](URL)** 마크다운 링크를 포함하세요. URL은 아래 에이전트 목록의 "URL:" 항목에서 그대로 복사해 사용하세요. URL이 없으면 에이전트명만 작성하세요.
7. 리스크는 과소/과대 표현 없이 데이터에 근거해 객관적으로 작성하세요.

---
## 입력 데이터

""" + _INPUT_SECTION + """

---
## 출력 형식

# {period_label} AI 도입 현황 리포트 — 리더십 관점

> **기간**: {period_start} ~ {period_end} | **이번**: {doc_count}건 | **3기간 추이**: {prev_prev_count}건 → {prev_count}건 → **{doc_count}건** | **누적**: {total_count}건

---

## 💡 핵심 인사이트 (Top 3)

> 단순 현황이 아닌 **해석 중심**으로 작성. 리더십이 읽으면 바로 판단 가능해야 함.

1. **[인사이트 제목]**: 구체적 해석 + 수치 근거 (예: "RPA 편중 지속 — 3기간 연속 전체의 50% 이상 차지")
2. **[인사이트 제목]**: 증가/감소/편중 패턴 + 근거 수치
3. **[인사이트 제목]**: 기술 또는 카테고리 흐름 변화 + 조직적 의미

반드시 아래 3개 관점 중 각 1개 이상 포함:
- **편중 현상**: 특정 카테고리·기술에 쏠림이 있는가?
- **증감 패턴**: 3기간에 걸쳐 성장/감소하는 영역은?
- **기술 흐름**: 새로 등장하거나 소멸한 기술스택의 의미는?

---

## 1. 핵심 요약

이번 기간 핵심 변화를 **3~4문장**으로 작성. 숫자 반드시 포함.
3기간 추이(전전기→전기→이번)에서 가속·감속·역전이 감지되면 반드시 언급.

---

## 2. AI 도입 현황 — 3기간 추이 분석

(데이터가 있는 표만 작성. 빈 표 절대 출력 금지.)

### 카테고리별 3기간 추이

위 "3기간 카테고리 추이" 표를 그대로 활용하되, 비중(%) 열을 추가하고 주목할 패턴(▲▲ 지속 성장, ▼▼ 감소세 등)을 한 줄 설명 추가.

### 기술스택 TOP 5 — 3기간 변화

위 "3기간 기술스택 Top 5 추이" 표를 활용. 신규 등장 기술과 감소 기술을 별도 명시.

### 기대효과 TOP 5

| 순위 | 기대효과 | 언급 건수 |
|:---:|:--------|--------:|
| 1 | (실제 데이터) | N |

---

## 3. 우리 조직이 AI로 해결하려는 문제

문서 본문·problem 필드를 분석해 어떤 업무 문제를 AI로 해결하려 하는지 **2~3가지 패턴**으로 분류.
각 패턴마다 대표 에이전트 사례 1~2건을 [에이전트명](URL) 링크와 함께 구체적으로 언급.

---

## 4. 주목할 에이전트 사례 (최대 3건)

> **선정 기준**: 메타데이터 풍부도(요약·문제·기술스택 모두 기재) + 기대효과 명확성 + 기술 복합도
> 에이전트명과 URL은 위 에이전트 목록에서 그대로 가져오세요.

### 🤖 [실제_에이전트명](실제_Confluence_URL)

| 항목 | 내용 |
|:----|:----|
| **링크** | [Confluence 페이지 바로가기](실제_Confluence_URL) |
| **해결 문제** | (problem 또는 본문에서 추출) |
| **기술스택** | (tech_stack 또는 본문에서 추출) |
| **기대효과** | (effects 또는 본문에서 추출) |
| **주목 이유** | (1~2문장) |

---

## 5. ⚠️ 주요 리스크

데이터 기반으로 리스크 **최소 2~3개**를 명시. 각 리스크는 구체적 수치 근거 포함.

| 리스크 | 근거 | 영향도 |
|:------|:----|:-----:|
| (예: 카테고리 편중) | 특정 카테고리가 전체의 N% 차지 | 🔴 높음 |
| (예: 특정 기술 의존) | 단일 기술스택 집중 | 🟡 중간 |
| (예: 성과 계량 부족) | 기대효과 데이터 보유율 낮음 | 🟡 중간 |

점검 대상 리스크 유형:
- 카테고리 편중(특정 영역이 전체 독점)
- 특정 기술 의존(단일 스택 집중으로 인한 취약성)
- 성과 계량 부족(기대효과 데이터 없이 확장 결정 곤란)
- PoC 정체(탐색 단계에서 확산 단계로 미전환)

---

## 6. AI 도입 성숙도 진단

| 항목 | 내용 |
|:----|:----|
| **현재 단계** | 탐색 / 도입 / 확산 / 최적화 중 선택 |
| **판단 근거** | 3기간 추이 + 카테고리 분포 + 기술 다양성 기반 |
| **강점** | ... |
| **개선 과제** | ... |
| **다음 단계 진입 조건** | ... |

---

## 7. 다음 기간 실행 권고

> 추상적 제언 금지. **정량적·구체적 행동** 중심으로 작성.

| 우선순위 | 실행 항목 | 목표 | 기한 |
|:-------:|:---------|:----|:----|
| 🔴 1순위 | (예: 고객응대 챗봇 PoC 3건 실행) | 편중 영역 외 확장 | 다음 기간 내 |
| 🟡 2순위 | (예: 외부 LLM 1건 도입 테스트) | 기술 다양성 확보 | ... |
| 🟢 3순위 | (예: 전사 AI 활용 현황 공유회 1회) | 확산 기반 조성 | ... |

---

## 8. 리더십을 위한 전략 제언

| 제언 | 배경 및 근거 | 우선순위 |
|:----|:----------|:-------:|
| ... | ... | 🔴 높음 |
| ... | ... | 🟡 중간 |

---
*본 리포트는 AI가 자동 분석한 결과입니다. 의사결정 참고용으로 활용하세요.*
"""

# ─────────────────────────── 실무자 관점 프롬프트 ────────────────────────────
_PRACTITIONER_PROMPT = """\
당신은 AI 에이전트 개발 경험이 풍부한 기술 전문가입니다.
아래 데이터를 분석해 **현업 실무자/개발자용 AI 에이전트 기술 동향 리포트**를 작성하세요.
반드시 **마크다운(Markdown) 형식**으로 작성하고 표(table)를 적극 활용하세요.

⚠️ 핵심 지침:
1. 기술 구현 방법, 사용 도구, 통합 지점에 집중하세요.
2. 데이터가 있는 항목만 작성하세요. 빈 섹션은 통째로 생략하세요.
3. 메타데이터가 없으면 본문을 직접 분석해 기술적 세부사항을 추출하세요.
4. 수치와 구체적인 도구명을 중심으로 작성하세요.
5. 3기간 추이(전전기→전기→이번)에서 기술 변화 패턴을 분석하세요.
6. 특정 에이전트를 언급할 때는 반드시 **[에이전트명](URL)** 마크다운 링크를 포함하세요. URL은 아래 에이전트 목록의 "URL:" 항목에서 그대로 복사해 사용하세요. URL이 없으면 에이전트명만 작성하세요.
7. 유사 사례를 도입하려는 팀이 참고할 수 있도록 실용적으로 작성하세요.

---
## 입력 데이터

""" + _INPUT_SECTION + """

---
## 출력 형식

# {period_label} AI 에이전트 기술 동향 — 실무자 관점

> **기간**: {period_start} ~ {period_end} | **이번**: {doc_count}건 | **3기간 추이**: {prev_prev_count}건 → {prev_count}건 → **{doc_count}건** | **누적**: {total_count}건

---

## 💡 핵심 기술 인사이트 (Top 3)

> 데이터에서 읽히는 **기술적 해석** 중심. "무엇이 일어나고 있는가"를 명확히.

1. **[인사이트 제목]**: 기술스택 관점의 구체적 발견 + 수치 근거
2. **[인사이트 제목]**: 3기간 기술 변화 패턴 (신규 등장 / 소멸 / 급증)
3. **[인사이트 제목]**: 구현 패턴 집중 현상 또는 다양화 여부

---

## 1. 이번 기간 기술 요약

3기간 추이를 포함한 기술 트렌드 **3~4문장**. 새로 등장하거나 반복적으로 쓰이는 기술스택 언급.

---

## 2. 3기간 기술스택 추이 분석

(실제 데이터가 있는 표만 작성.)

### 기술스택 3기간 변화

위 "3기간 기술스택 Top 5 추이" 표를 그대로 활용. 신규 등장 기술(전기까지 0건이던 기술)과 감소 기술을 주석으로 추가.

### 이번 기간 새로 등장한 기술 (전기 미사용)

새로 등장한 기술스택이 있으면 명시. 없으면 섹션 생략.

---

## 3. 구현 패턴 분석

### 주요 구현 패턴 우선순위 (Top 3)

가장 많이 사용된 구현 패턴 3가지를 선정하고, **왜 많이 선택되었는지** 이유를 분석하세요.

| 순위 | 패턴명 | 활용 건수 | 선택 이유 |
|:---:|:------|--------:|:---------|
| 1 | (예: RPA + LLM 조합) | N | (예: 기존 RPA 자산 재활용 + LLM으로 판단 자동화) |
| 2 | ... | ... | ... |
| 3 | ... | ... | ... |

### 재사용 가능 패턴 (조직 내 확산 권장)

아래 기준으로 조직 내 확산 가능한 구조를 선정하세요:
- 구현 복잡도가 낮아 팀 단위 도입 가능
- 여러 부서에 적용 가능한 범용 구조
- 기존 인프라(DevX MCP, n8n, RPA 플랫폼 등) 재사용 가능

| 패턴 | 확산 가능 부서 | 전제 조건 | 예상 기간 |
|:----|:------------|:--------|:--------|
| (예: DevX + MCP API 패턴) | 개발 전 부서 | DevX 계정 보유 | 1~2주 |

---

## 4. 벤치마킹 추천 사례 (최대 3건)

> **선정 기준**: 메타데이터 풍부도(요약·기술스택 완비) + 기술 복합도(2개 이상 스택 조합) + 확산 가능성
> 에이전트명과 URL은 위 에이전트 목록에서 그대로 가져오세요.

### 🔧 [실제_에이전트명](실제_Confluence_URL)

| 항목 | 내용 |
|:----|:----|
| **링크** | [Confluence 페이지 바로가기](실제_Confluence_URL) |
| **해결 문제** | ... |
| **기술스택** | ... |
| **구현 방식** | (solution 또는 본문에서 추출한 구현 세부사항) |
| **기대효과** | ... |
| **도입 난이도** | 낮음 / 중간 / 높음 (기술 복잡도 기준) |
| **재사용 포인트** | 다른 팀이 바로 가져갈 수 있는 핵심 구조 |

---

## 5. 도입 시 고려사항

이번 기간 에이전트들에서 공통적으로 발견되는 기술적 도전 과제나 전제조건.

| 고려사항 | 관련 에이전트 사례 | 권장 대응 |
|:--------|:----------------|:--------|
| ... | ... | ... |

---

## 6. ⚠️ 기술 리스크

데이터 기반으로 기술 리스크 **최소 2~3개** 명시.

| 리스크 | 근거 | 심각도 |
|:------|:----|:-----:|
| (예: 특정 기술 의존 과도) | LLM API 단일 공급사 집중 | 🔴 높음 |
| (예: 구현 패턴 문서화 부족) | solution 필드 기재율 낮음 | 🟡 중간 |
| (예: 스킬 병목) | 특정 기술 보유자 편중 | 🟡 중간 |

---

## 7. 다음 기간 실행 권고

> 팀이 바로 실행할 수 있는 **구체적 행동** 중심.

| 우선순위 | 실행 항목 | 담당 범위 | 기간 |
|:-------:|:---------|:--------|:----|
| 🔴 1순위 | (예: 재사용 가능 패턴 템플릿 1건 문서화) | 개발팀 | 다음 기간 내 |
| 🟡 2순위 | (예: 신규 기술스택 PoC 1건 실행) | ... | ... |
| 🟢 3순위 | (예: 기술 리뷰 세션 1회) | ... | ... |

---

## 8. 다음 기간 주목할 기술 방향

데이터 기반으로 조직 내 AI 기술 발전 방향 2~3가지 제안.

---
*본 리포트는 AI가 자동 분석한 결과입니다. 구현 참고용으로 활용하세요.*
"""

PERSPECTIVES = {
    "leadership": ("리더십 관점", _LEADERSHIP_PROMPT),
    "practitioner": ("실무자 관점", _PRACTITIONER_PROMPT),
}


class ReportService:
    def __init__(
        self,
        doc_repo: DocumentRepository,
        report_repo: ReportRepository,
        llm,
        db_path: str,
    ):
        self.doc_repo = doc_repo
        self.report_repo = report_repo
        self.llm = llm
        self.db_path = db_path

    @classmethod
    def from_config(cls, config: AppConfig) -> "ReportService":
        if not config.is_llm_configured:
            raise ReportError("LLM API Key가 설정되지 않았습니다.")
        # 리포트는 긴 LLM 호출이므로 fresh 클라이언트 사용 (싱글톤 공유 시 연결 재사용 문제 방지)
        from app.infrastructure.llm.factory import create_llm_provider
        llm = create_llm_provider(config)
        return cls(
            doc_repo=DocumentRepository(config.db_path),
            report_repo=ReportRepository(config.db_path),
            llm=llm,
            db_path=config.db_path,
        )

    @staticmethod
    def _db_key(period_key: str, perspective: str) -> str:
        return f"{period_key}:{perspective}"

    def get_report_list(self, report_type: str, perspective: str = "leadership") -> list[dict]:
        return self.report_repo.get_by_type(report_type, perspective)

    def get_or_generate(self, report_type: str, period_key: str, perspective: str = "leadership") -> dict:
        db_key = self._db_key(period_key, perspective)
        existing = self.report_repo.get_by_period_key(db_key)
        if existing:
            existing["_period_key"] = period_key
            return existing
        return self.generate(report_type, period_key, perspective)

    def generate(self, report_type: str, period_key: str, perspective: str = "leadership") -> dict:
        if not _report_lock.acquire(blocking=False):
            raise ReportError(
                "리포트 생성이 진행 중입니다. 완료 후 다시 시도하거나 다른 관점/기간을 선택하세요."
            )
        try:
            return self._generate_impl(report_type, period_key, perspective)
        finally:
            _report_lock.release()

    def _generate_impl(self, report_type: str, period_key: str, perspective: str = "leadership") -> dict:
        period_start, period_end = self._resolve_period(report_type, period_key)
        perspective_label, prompt_template = PERSPECTIVES.get(perspective, PERSPECTIVES["leadership"])
        period_label = ("주간 리포트" if report_type == "weekly" else "월간 리포트") + f" — {perspective_label}"

        # ── 3기간 데이터 수집 ─────────────────────────────────────────
        docs = self.doc_repo.get_by_period(period_start, period_end)
        doc_count = len(docs)

        prev_start, prev_end = self._prev_period(report_type, period_start)
        prev_docs = self.doc_repo.get_by_period(prev_start, prev_end)
        prev_count = len(prev_docs)

        prev_prev_start, prev_prev_end = self._prev_period(report_type, prev_start)
        prev_prev_docs = self.doc_repo.get_by_period(prev_prev_start, prev_prev_end)
        prev_prev_count = len(prev_prev_docs)

        prev_period_label = self._period_label(report_type, prev_start)
        prev_prev_period_label = self._period_label(report_type, prev_prev_start)

        # ── 증감 문자열 ────────────────────────────────────────────────
        if prev_count == 0:
            trend_str = "전기 데이터 없음"
        else:
            diff = doc_count - prev_count
            pct = round(diff / prev_count * 100)
            trend_str = f"{'+' if diff >= 0 else ''}{diff}건 ({'+' if pct >= 0 else ''}{pct}%)"

        # ── 3기간 카테고리 비교표 ──────────────────────────────────────
        pp_cat = dict(self._count_plain_field(prev_prev_docs, "category"))
        p_cat  = dict(self._count_plain_field(prev_docs, "category"))
        c_cat  = dict(self._count_plain_field(docs, "category"))
        all_cats = sorted(set(pp_cat) | set(p_cat) | set(c_cat))

        if all_cats:
            header = (
                f"| 카테고리 | 전전기({prev_prev_period_label}) "
                f"| 전기({prev_period_label}) | 이번 | 증감 | 추세 |"
            )
            rows = [header, "|:--|--:|--:|--:|--:|:--:|"]
            for cat in all_cats:
                pp = pp_cat.get(cat, 0)
                p  = p_cat.get(cat, 0)
                c  = c_cat.get(cat, 0)
                diff = c - p
                diff_str = f"+{diff}" if diff > 0 else str(diff)
                if p > pp and c > p:
                    trend = "▲▲"
                elif p < pp and c < p:
                    trend = "▼▼"
                elif c > p:
                    trend = "▲"
                elif c < p:
                    trend = "▼"
                else:
                    trend = "─"
                rows.append(f"| {cat} | {pp} | {p} | {c} | {diff_str} | {trend} |")
            category_comparison = "\n".join(rows)
        else:
            category_comparison = "(카테고리 데이터 없음 — 본문에서 직접 분류하세요)"

        # ── 3기간 기술스택 추이표 ──────────────────────────────────────
        tech_trend = self._tech_trend_table(
            prev_prev_docs, prev_docs, docs,
            prev_prev_period_label, prev_period_label,
        )

        # ── DB 집계 (단일 세션) ────────────────────────────────────────
        with db_session(self.db_path) as conn:
            total_count = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE is_deleted = 0"
            ).fetchone()[0]
            author_rows = conn.execute(
                "SELECT author, COUNT(*) as cnt FROM documents "
                "WHERE is_deleted=0 AND author IS NOT NULL AND author != '' "
                "GROUP BY author ORDER BY cnt DESC LIMIT 10"
            ).fetchall()
            author_freq = [[r[0], r[1]] for r in author_rows]

            dq_row = conn.execute(
                """SELECT COUNT(*) as total,
                       SUM(CASE WHEN dm.document_id IS NOT NULL THEN 1 ELSE 0 END) as with_meta,
                       SUM(CASE WHEN dm.tech_stack_json IS NOT NULL
                                     AND dm.tech_stack_json NOT IN ('null', '[]', '')
                                THEN 1 ELSE 0 END) as with_tech,
                       SUM(CASE WHEN dm.category IS NOT NULL AND dm.category != ''
                                THEN 1 ELSE 0 END) as with_cat
                   FROM documents d
                   LEFT JOIN document_metadata dm ON d.id = dm.document_id
                   WHERE d.is_deleted = 0"""
            ).fetchone()

        dq_total = dq_row["total"] or 1
        dq_pct_meta = round((dq_row["with_meta"] or 0) / dq_total * 100)
        dq_pct_tech = round((dq_row["with_tech"] or 0) / dq_total * 100)
        dq_pct_cat  = round((dq_row["with_cat"]  or 0) / dq_total * 100)

        # ── 전기 리포트 발췌 (연속성 컨텍스트) ────────────────────────
        prev_period_key_str = self._prev_period_key(report_type, period_key)
        prev_db_key = self._db_key(prev_period_key_str, perspective)
        prev_report_row = self.report_repo.get_by_period_key(prev_db_key)
        if prev_report_row:
            prev_text = prev_report_row.get("summary_text", "")
            prev_report_excerpt = prev_text[:600] + ("…" if len(prev_text) > 600 else "")
        else:
            prev_report_excerpt = "(전기 리포트 없음 — 이번이 첫 분석이거나 전기 리포트가 생성되지 않았습니다)"

        tech_freq    = self._freq_from_json_field(docs, "tech_stack_json")
        effects_freq = self._freq_from_json_field(docs, "effects_json")

        problems = [
            d.get("problem") for d in docs
            if d.get("problem") and len(d["problem"]) > 10
        ][:20]
        problem_summary = "\n".join(f"- {p[:150]}" for p in problems) or "(없음 — 본문 분석 활용)"

        # ── 문서 목록 (richness ≥ 3 우선, 나머지로 25건 채움) ─────────
        scored = sorted(
            ((d, self._doc_richness_score(d)) for d in docs),
            key=lambda x: x[1], reverse=True,
        )
        rich_docs   = [d for d, s in scored if s >= 3]
        sparse_docs = [d for d, s in scored if s < 3]
        selected_docs = (rich_docs + sparse_docs)[:25]

        doc_lines = []
        for d in selected_docs:
            name     = d.get("agent_name") or d.get("title", "")
            url      = d.get("url", "")
            summary  = d.get("one_line_summary", "")
            problem  = d.get("problem", "")
            category = d.get("category", "")
            solution = d.get("solution", "")
            body     = (d.get("cleaned_body") or "")[:300].replace("\n", " ").strip()

            heading = f"[{name}]({url})" if url else name
            line = f"### {heading}"
            if category:
                line += f" [{category}]"
            if url:
                line += f"\n- URL: {url}"
            if summary:
                line += f"\n- 요약: {summary}"
            if problem:
                line += f"\n- 문제: {problem[:150]}"
            if solution:
                line += f"\n- 해결: {solution[:150]}"
            if not summary and not problem and body:
                line += f"\n- 본문: {body}"
            doc_lines.append(line)

        doc_list_str = "\n\n".join(doc_lines) or "(문서 없음)"

        # ── 프롬프트 구성 ──────────────────────────────────────────────
        fmt = dict(
            period_label=period_label,
            period_start=period_start[:10],
            period_end=period_end[:10],
            doc_count=doc_count,
            prev_count=prev_count,
            prev_prev_count=prev_prev_count,
            prev_period_label=prev_period_label,
            prev_prev_period_label=prev_prev_period_label,
            trend_str=trend_str,
            total_count=total_count,
            doc_list=doc_list_str,
            category_comparison=category_comparison,
            tech_trend=tech_trend,
            problem_summary=problem_summary,
            tech_freq=json.dumps(tech_freq[:10], ensure_ascii=False) if tech_freq else "(없음 — 본문에서 추출)",
            effects_freq=json.dumps(effects_freq[:10], ensure_ascii=False) if effects_freq else "(없음 — 본문에서 추출)",
            author_freq=json.dumps(author_freq[:10], ensure_ascii=False),
            dq_pct_meta=dq_pct_meta,
            dq_pct_tech=dq_pct_tech,
            dq_pct_cat=dq_pct_cat,
            prev_report_excerpt=prev_report_excerpt,
        )
        prompt = prompt_template.format(**fmt)

        # 에이전트 이름 → URL 조회 테이블 (후처리용)
        url_map: dict[str, str] = {}
        for d in selected_docs:
            name = (d.get("agent_name") or d.get("title", "")).strip()
            url  = (d.get("url") or "").strip()
            if name and url:
                url_map[name] = url

        logger.info(f"리포트 생성 중: {period_key} [{perspective}]")
        summary_text = self.llm.generate(prompt)

        # LLM 출력 후처리
        summary_text = re.sub(r"<br\s*/?>", " ", summary_text)  # HTML br 태그 제거
        summary_text = _inject_missing_urls(summary_text, url_map)

        db_key = self._db_key(period_key, perspective)
        report = {
            "report_type": report_type,
            "period_key": db_key,
            "period_start": period_start,
            "period_end": period_end,
            "based_on_document_count": doc_count,
            "summary_text": summary_text,
            "highlights_json": {
                "top_tech_stacks": tech_freq[:10],
                "top_effects": effects_freq[:10],
                "top_authors": author_freq[:10],
                "perspective": perspective,
            },
            "created_at": now_kst_str(),
        }
        self.report_repo.save(report)
        report["_period_key"] = period_key
        logger.info(f"리포트 저장 완료: {db_key}")
        return report

    # ── 정적 헬퍼 ─────────────────────────────────────────────────────

    @staticmethod
    def _period_label(report_type: str, period_start: str) -> str:
        dt = datetime.fromisoformat(period_start)
        if report_type == "weekly":
            return f"{dt.year}년 {int(dt.strftime('%W'))}주"
        return f"{dt.year}년 {dt.month}월"

    @staticmethod
    def _prev_period_key(report_type: str, period_key: str) -> str:
        """period_key의 직전 기간 키 반환 (weekly: -W, monthly: -MM)."""
        if report_type == "weekly":
            year, week = period_key.split("-W")
            w, y = int(week) - 1, int(year)
            if w < 1:
                y -= 1
                # 전년도 마지막 ISO 주차를 정확히 계산 (52 또는 53)
                import datetime as _dt
                dec28 = _dt.date(y, 12, 28)  # 12/28은 항상 해당 연도의 마지막 주에 속함
                w = int(dec28.strftime("%W"))
            return f"{y}-W{w:02d}"
        else:
            year, month = period_key.split("-")
            m, y = int(month) - 1, int(year)
            if m < 1:
                m, y = 12, y - 1
            return f"{y}-{m:02d}"

    @staticmethod
    def _prev_period(report_type: str, period_start: str) -> tuple[str, str]:
        start_dt = datetime.fromisoformat(period_start)
        if report_type == "weekly":
            prev_end   = start_dt - timedelta(seconds=1)
            prev_start = prev_end - timedelta(days=6, hours=23, minutes=59, seconds=59)
            return prev_start.isoformat(), prev_end.isoformat()
        else:
            first_of_month = start_dt.replace(day=1)
            prev_end   = first_of_month - timedelta(seconds=1)
            prev_start = prev_end.replace(day=1, hour=0, minute=0, second=0)
            return prev_start.isoformat(), prev_end.isoformat()

    @staticmethod
    def _resolve_period(report_type: str, period_key: str) -> tuple[str, str]:
        if report_type == "weekly":
            year, week = period_key.split("-W")
            w_int = int(week)
            if w_int == 0:
                # W00: strptime이 ValueError 없이 전년도 말일 반환하므로 명시적 분기
                monday = datetime(int(year), 1, 1)
            else:
                try:
                    monday = datetime.strptime(f"{year}-W{w_int:02d}-1", "%Y-W%W-%w")
                    # 파싱 결과가 다른 연도를 반환하면 (W53 등 경계값) 해당 연도 마지막 월요일로 보정
                    if monday.year != int(year):
                        monday = datetime(int(year), 12, 28) - timedelta(
                            days=datetime(int(year), 12, 28).weekday()
                        )
                except ValueError:
                    monday = datetime(int(year), 1, 1)
            sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
            return monday.isoformat(), sunday.isoformat()
        else:
            year, month = period_key.split("-")
            start = datetime(int(year), int(month), 1)
            if int(month) == 12:
                end = datetime(int(year) + 1, 1, 1) - timedelta(seconds=1)
            else:
                end = datetime(int(year), int(month) + 1, 1) - timedelta(seconds=1)
            return start.isoformat(), end.isoformat()

    @staticmethod
    def current_week_key() -> str:
        now = now_kst()
        return f"{now.year}-W{now.strftime('%W')}"

    @staticmethod
    def current_month_key() -> str:
        return now_kst().strftime("%Y-%m")

    @staticmethod
    def _doc_richness_score(doc: dict) -> int:
        score = 0
        if doc.get("one_line_summary"):
            score += 3
        if doc.get("problem"):
            score += 2
        if doc.get("solution"):
            score += 2
        try:
            if json.loads(doc.get("tech_stack_json") or "[]"):
                score += 2
        except (json.JSONDecodeError, TypeError):
            pass
        if doc.get("category") and doc.get("category") != "기타":
            score += 1
        return score

    @staticmethod
    def _freq_from_json_field(docs: list[dict], field: str) -> list[list]:
        counter: Counter = Counter()
        for doc in docs:
            try:
                items = json.loads(doc.get(field) or "[]")
                if isinstance(items, list):
                    counter.update(items)
            except (json.JSONDecodeError, TypeError):
                pass
        return [[k, v] for k, v in counter.most_common()]

    @staticmethod
    def _count_plain_field(docs: list[dict], field: str) -> list[list]:
        counter: Counter = Counter()
        for doc in docs:
            val = doc.get(field)
            if val:
                counter[val] += 1
        return [[k, v] for k, v in counter.most_common()]

    def _tech_trend_table(
        self,
        prev_prev_docs: list[dict],
        prev_docs: list[dict],
        curr_docs: list[dict],
        prev_prev_label: str,
        prev_label: str,
        top_n: int = 5,
    ) -> str:
        """3기간 기술스택 빈도 비교표 생성."""
        pp_freq = {k: v for k, v in self._freq_from_json_field(prev_prev_docs, "tech_stack_json")}
        p_freq  = {k: v for k, v in self._freq_from_json_field(prev_docs, "tech_stack_json")}
        c_freq_list = self._freq_from_json_field(curr_docs, "tech_stack_json")

        top_techs = [t for t, _ in c_freq_list[:top_n]]
        if not top_techs:
            return "(기술스택 데이터 없음 — 본문에서 직접 추출)"

        rows = [
            f"| 기술스택 | 전전기({prev_prev_label}) | 전기({prev_label}) | 이번 |",
            "|:--------|--:|--:|--:|",
        ]
        c_freq = {k: v for k, v in c_freq_list}
        for tech in top_techs:
            rows.append(
                f"| {tech} | {pp_freq.get(tech, 0)} "
                f"| {p_freq.get(tech, 0)} | {c_freq.get(tech, 0)} |"
            )
        return "\n".join(rows)
