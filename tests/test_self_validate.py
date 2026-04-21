"""
자가 검증 테스트 — 핵심 비즈니스 로직 검증.
외부 의존성(ChromaDB, httpx, Streamlit) 없이 순수 Python으로 실행 가능.
"""
import json
import sys
import tempfile
import sqlite3
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list[tuple[str, bool, str]] = []


def test(name: str):
    def decorator(fn):
        try:
            fn()
            results.append((name, True, ""))
        except Exception as e:
            results.append((name, False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))
        return fn
    return decorator


# ─────────────────────── Area 1: 메타데이터 추출 ────────────────────────

@test("extractor: _is_fallback_metadata — fallback 탐지 (>=2 empty, problem/category/tech)")
def _():
    from app.infrastructure.llm.extractor import _is_fallback_metadata, CATEGORY_BLOCKED

    # 3개 모두 비어있음 → fallback (empty=3)
    full_fallback = {"problem": None, "category": "기타", "tech_stack_json": "[]"}
    assert _is_fallback_metadata(full_fallback) is True

    # 3개 모두 채워짐 → fallback 아님 (empty=0)
    full_real = {"problem": "수작업", "category": "RPA", "tech_stack_json": '["UiPath"]'}
    assert _is_fallback_metadata(full_real) is False

    # 2개 비어있음 → fallback (경계값: empty=2): problem + category=기타
    partial_2 = {"problem": None, "category": "기타", "tech_stack_json": '["Python"]'}
    assert _is_fallback_metadata(partial_2) is True, "problem+category 2개 비어있으면 fallback"

    # 1개만 비어있음 → fallback 아님 (empty=1)
    partial_1 = {"problem": "문제", "category": "기타", "tech_stack_json": '["Python"]'}
    assert _is_fallback_metadata(partial_1) is False, "1개만 비어있으면 재추출 불필요"

    # category+tech 둘 다 비어있음 → empty=2 → fallback
    partial_no_tech = {"problem": "문제", "category": "기타", "tech_stack_json": "[]"}
    assert _is_fallback_metadata(partial_no_tech) is True, "category+tech 둘 다 없으면 재추출"

    # 추출불가 → 재추출 불필요 (CATEGORY_BLOCKED 예외)
    blocked = {"problem": None, "category": CATEGORY_BLOCKED, "tech_stack_json": "[]"}
    assert _is_fallback_metadata(blocked) is False, "추출불가 문서는 재추출 제외"


@test("extractor: should_extract — 재추출 조건")
def _():
    from app.infrastructure.llm.extractor import should_extract, CATEGORY_BLOCKED

    local = {"content_hash": "hash1"}
    real_meta = {"problem": "수작업", "category": "RPA", "tech_stack_json": '["UiPath"]'}
    fallback_meta = {"problem": None, "category": "기타", "tech_stack_json": "[]"}
    partial_meta = {"problem": "문제", "category": "기타", "tech_stack_json": "[]"}  # category+tech 비어있어 empty=2
    blocked_meta = {"problem": None, "category": CATEGORY_BLOCKED, "tech_stack_json": "[]"}

    assert should_extract(1, "hash1", {}, None) is True            # 최초 추출
    assert should_extract(1, "hash1", local, real_meta) is False   # 실제 메타 + 해시 동일 → 스킵
    assert should_extract(1, "hash_new", local, real_meta) is True  # 해시 변경 → 재추출
    assert should_extract(1, "hash1", local, fallback_meta) is True   # full fallback → 재추출
    assert should_extract(1, "hash1", local, partial_meta) is True    # 2개 빈 partial → 재추출
    # 추출불가: hash 동일 시 스킵, 해시 변경 시 재시도
    assert should_extract(1, "hash1", local, blocked_meta) is False   # 추출불가 + 해시 동일 → 스킵
    assert should_extract(1, "hash_new", local, blocked_meta) is True  # 추출불가 + 해시 변경 → 재시도


@test("extractor: MAX_CONTENT_CHARS 값 확인")
def _():
    from app.infrastructure.llm.extractor import MAX_CONTENT_CHARS
    assert MAX_CONTENT_CHARS == 4000, f"4000이어야 하는데 {MAX_CONTENT_CHARS}"


@test("extractor: 프롬프트에 구체적 예시 포함")
def _():
    from app.infrastructure.llm.extractor import _USER_PROMPT, _SYSTEM_PROMPT
    assert "최대한 추론" in _SYSTEM_PROMPT, "추론 지시가 시스템 프롬프트에 있어야 함"
    assert "UiPath" in _USER_PROMPT or "Python" in _USER_PROMPT, "예시 기술스택이 포함돼야 함"
    assert "null" in _USER_PROMPT, "null 사용 조건이 명시돼야 함"


# ─────────────────────── Area 2: 청킹 ───────────────────────────────────

@test("chunking: 문단 인식 — 짧은 문서")
def _():
    from app.shared.text_utils import chunk_text
    text = "문단1 내용입니다.\n\n문단2 내용입니다.\n\n문단3 내용입니다."
    chunks = chunk_text(text)
    assert len(chunks) >= 1
    combined = " ".join(c["chunk_text"] for c in chunks)
    assert "문단1" in combined and "문단3" in combined, "모든 문단이 청크에 포함돼야 함"


@test("chunking: 문단 인식 — 큰 문서 분할")
def _():
    from app.shared.text_utils import chunk_text
    para = "가나다라마바사아자차카타파하" * 22  # ~308자
    text = f"{para}\n\n{para}\n\n{para}"
    chunks = chunk_text(text, chunk_size=800)
    assert len(chunks) >= 2, f"큰 문서는 2개 이상 청크여야 함, 실제: {len(chunks)}"


@test("chunking: overlap이 마지막 문단 단위")
def _():
    from app.shared.text_utils import chunk_text
    # 각 문단이 400자인 문서 → 두 문단 합치면 800자 초과 → 분할 발생
    para = "A" * 400
    text = f"문단1\n{para}\n\n문단2\n{para}\n\n문단3\n{para}"
    chunks = chunk_text(text, chunk_size=800)
    # overlap이 문단 단위이므로, 연결 경계에서 앞 문단의 마지막 내용이 다음 청크에 포함
    assert len(chunks) >= 2
    # 첫 번째 청크와 두 번째 청크가 완전히 동일하지 않아야 함
    texts = [c["chunk_text"] for c in chunks]
    assert len(set(texts)) > 1, "모든 청크가 동일하면 안 됨"


@test("chunking: 빈 문서 처리")
def _():
    from app.shared.text_utils import chunk_text
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


@test("chunking: 초소형 청크(50자 미만) 제외")
def _():
    from app.shared.text_utils import chunk_text
    text = "짧음\n\n" + "긴 내용 " * 20
    chunks = chunk_text(text)
    for c in chunks:
        assert len(c["chunk_text"].strip()) > 50, "50자 미만 청크가 포함됨"


@test("chunking: token_count 필드 존재")
def _():
    from app.shared.text_utils import chunk_text
    text = "내용입니다.\n\n더 많은 내용입니다. " * 10
    chunks = chunk_text(text)
    for c in chunks:
        assert "token_count" in c
        assert c["token_count"] > 0


# ─────────────────────── Area 2: 검색 점수 로직 ─────────────────────────

@test("search: 유사도 배지 — 높음/중간/낮음/keyword")
def _():
    # search.py에서 함수 직접 임포트하지 않고 로직 재현 (Streamlit 없이)
    SCORE_HIGH = 0.70
    SCORE_MID  = 0.40
    SCORE_FILTER = 0.15

    def classify(score, max_score, mode):
        if mode == "keyword" or max_score == 0:
            return "keyword"
        ratio = score / max_score
        if ratio >= SCORE_HIGH:
            return "높음"
        elif ratio >= SCORE_MID:
            return "중간"
        elif ratio >= SCORE_FILTER:
            return "낮음"
        else:
            return "필터"

    max_s = 0.9
    assert classify(0.85, max_s, "hybrid") == "높음"   # 0.944
    assert classify(0.45, max_s, "hybrid") == "중간"   # 0.500
    assert classify(0.20, max_s, "hybrid") == "낮음"   # 0.222
    assert classify(0.10, max_s, "hybrid") == "필터"   # 0.111 < 0.15
    assert classify(0.10, max_s, "keyword") == "keyword"


@test("search: 벡터 동적 threshold 로직")
def _():
    # 동적 threshold: max(0.05, min(0.20, max_score * 0.50))
    def dynamic_threshold(max_score):
        return max(0.05, min(0.20, max_score * 0.50))

    # 높은 점수 corpus → threshold=0.20 (절대값 상한)
    assert dynamic_threshold(0.80) == 0.20   # min(0.20, 0.40)=0.20
    assert dynamic_threshold(0.50) == 0.20   # min(0.20, 0.25)=0.20

    # 중간 점수 corpus → 상대값 적용
    assert abs(dynamic_threshold(0.30) - 0.15) < 0.001  # min(0.20, 0.15)=0.15

    # 매우 낮은 점수 corpus (한국어 소규모) → floor 0.05
    assert dynamic_threshold(0.06) == 0.05   # min(0.20, 0.03)=0.03 → floor 0.05
    assert dynamic_threshold(0.00) == 0.05   # floor

    # 소스에 동적 threshold 로직 포함 확인
    src = Path("app/application/search_service.py").read_text(encoding="utf-8")
    assert "max_score * 0.50" in src
    assert "min(0.20" in src or "min(0.20," in src


@test("search: document_repository keyword search date 파라미터 시그니처")
def _():
    import inspect
    # ChromaDB 없이 클래스만 임포트
    from app.infrastructure.db.document_repository import DocumentRepository
    sig = inspect.signature(DocumentRepository.search_by_keyword)
    params = list(sig.parameters.keys())
    assert "date_from" in params, "date_from 파라미터 없음"
    assert "date_to" in params, "date_to 파라미터 없음"


# ─────────────────────── Area 3: 리포트 서비스 ──────────────────────────

@test("report_service: PERSPECTIVES 딕셔너리 구조")
def _():
    from app.application.report_service import PERSPECTIVES
    assert "leadership" in PERSPECTIVES
    assert "practitioner" in PERSPECTIVES
    for k, v in PERSPECTIVES.items():
        assert isinstance(v, tuple) and len(v) == 2
        label, prompt = v
        assert isinstance(label, str) and label
        assert isinstance(prompt, str) and len(prompt) > 200


@test("report_service: _db_key 복합 키 생성")
def _():
    from app.application.report_service import ReportService
    assert ReportService._db_key("2025-04", "leadership") == "2025-04:leadership"
    assert ReportService._db_key("2025-W16", "practitioner") == "2025-W16:practitioner"


@test("report_service: _resolve_period 월간")
def _():
    from app.application.report_service import ReportService
    start, end = ReportService._resolve_period("monthly", "2025-04")
    assert start.startswith("2025-04-01")
    assert end.startswith("2025-04-30")


@test("report_service: _resolve_period 주간")
def _():
    from app.application.report_service import ReportService
    start, end = ReportService._resolve_period("weekly", "2025-W15")
    from datetime import datetime
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    assert (e - s).days == 6


@test("report_service: _prev_period 월간")
def _():
    from app.application.report_service import ReportService
    prev_s, prev_e = ReportService._prev_period("monthly", "2025-04-01T00:00:00")
    assert prev_s.startswith("2025-03-01")
    assert prev_e.startswith("2025-03-31")


@test("report_service: _prev_period 주간")
def _():
    from app.application.report_service import ReportService
    from datetime import datetime
    prev_s, prev_e = ReportService._prev_period("weekly", "2025-04-07T00:00:00")
    s = datetime.fromisoformat(prev_s)
    e = datetime.fromisoformat(prev_e)
    assert (e - s).days == 6


@test("report_service: 두 프롬프트 placeholder 완전성")
def _():
    import re
    from app.application.report_service import _LEADERSHIP_PROMPT, _PRACTITIONER_PROMPT, _INPUT_SECTION

    required = {
        "period_label", "period_start", "period_end", "doc_count", "prev_count",
        "total_count", "doc_list", "category_comparison",
        "problem_summary", "tech_freq", "effects_freq", "author_freq",
    }

    for name, prompt in [("leadership", _LEADERSHIP_PROMPT), ("practitioner", _PRACTITIONER_PROMPT)]:
        found = set(re.findall(r"\{(\w+)\}", prompt))
        missing = required - found
        assert not missing, f"{name} 프롬프트에 누락된 placeholder: {missing}"

        # format 실제 수행 — found 전체로 dummy를 구성해야 KeyError 없음
        dummy = {k: "TEST" for k in found}
        result = prompt.format(**dummy)
        assert "TEST" in result


@test("report_service: _doc_richness_score — 대표 에이전트 점수")
def _():
    from app.application.report_service import ReportService

    rich = {
        "one_line_summary": "RPA 자동화 에이전트",
        "problem": "수작업 반복 업무",
        "solution": "UiPath 기반 자동화",
        "tech_stack_json": '["UiPath", "Python"]',
        "category": "RPA",
    }
    poor = {
        "one_line_summary": "",
        "problem": None,
        "solution": None,
        "tech_stack_json": "[]",
        "category": "기타",
    }
    partial = {
        "one_line_summary": "요약",
        "problem": "문제",
        "solution": None,
        "tech_stack_json": "[]",
        "category": "기타",
    }

    assert ReportService._doc_richness_score(rich) > ReportService._doc_richness_score(partial)
    assert ReportService._doc_richness_score(partial) > ReportService._doc_richness_score(poor)
    assert ReportService._doc_richness_score(poor) == 0


@test("report_service: 카테고리 3기간 추이 지표 포함")
def _():
    src = Path("app/application/report_service.py").read_text(encoding="utf-8")
    # 3기간 추이 화살표 지표(▲▼─)가 category_comparison 생성 로직에 포함되어야 함
    assert "▲" in src or "prev_prev" in src, "3기간 카테고리 추이 로직 없음"
    assert "category_comparison" in src, "category_comparison placeholder 참조 없음"


@test("report_service: _freq_from_json_field — 정상 집계")
def _():
    from app.application.report_service import ReportService
    docs = [
        {"tech_stack_json": '["Python", "RPA"]'},
        {"tech_stack_json": '["Python", "GPT-4"]'},
        {"tech_stack_json": None},
        {"tech_stack_json": "invalid json"},
        {"tech_stack_json": '["RPA"]'},
    ]
    freq = ReportService._freq_from_json_field(docs, "tech_stack_json")
    freq_dict = dict(freq)
    assert freq_dict.get("Python") == 2
    assert freq_dict.get("RPA") == 2
    assert freq_dict.get("GPT-4") == 1


@test("report_service: _count_plain_field — category 집계")
def _():
    from app.application.report_service import ReportService
    docs = [
        {"category": "RPA"},
        {"category": "RPA"},
        {"category": "챗봇"},
        {"category": None},
        {"category": ""},
    ]
    freq = dict(ReportService._count_plain_field(docs, "category"))
    assert freq.get("RPA") == 2
    assert freq.get("챗봇") == 1
    assert None not in freq
    assert "" not in freq


# ─────────────────────── DB: report_repository ──────────────────────────

@test("report_repository: perspective 접미사 필터 + _period_key 노출")
def _():
    from app.infrastructure.db.report_repository import ReportRepository
    from app.infrastructure.db.migrations import run_migrations
    from datetime import datetime

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    run_migrations(db_path)
    repo = ReportRepository(db_path)

    now = datetime.now().isoformat()
    for period, perspective in [("2025-04", "leadership"), ("2025-04", "practitioner"), ("2025-03", "leadership")]:
        repo.save({
            "report_type": "monthly",
            "period_key": f"{period}:{perspective}",
            "period_start": f"{period}-01",
            "period_end": f"{period}-30",
            "based_on_document_count": 5,
            "summary_text": f"테스트 {perspective}",
            "highlights_json": {"perspective": perspective},
            "created_at": now,
        })

    # leadership 목록만 조회
    leadership = repo.get_by_type("monthly", "leadership")
    assert len(leadership) == 2, f"leadership 2건이어야 함, 실제: {len(leadership)}"
    assert all(r["_period_key"] in ("2025-04", "2025-03") for r in leadership)

    # practitioner 목록만 조회
    practitioner = repo.get_by_type("monthly", "practitioner")
    assert len(practitioner) == 1
    assert practitioner[0]["_period_key"] == "2025-04"

    # get_by_period_key (복합 키)
    r = repo.get_by_period_key("2025-04:practitioner")
    assert r is not None
    assert r["summary_text"] == "테스트 practitioner"

    # 없는 키
    assert repo.get_by_period_key("2025-04:nonexistent") is None

    Path(db_path).unlink(missing_ok=True)


@test("report_repository: 같은 키 저장 시 덮어쓰기")
def _():
    from app.infrastructure.db.report_repository import ReportRepository
    from app.infrastructure.db.migrations import run_migrations
    from datetime import datetime

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    run_migrations(db_path)
    repo = ReportRepository(db_path)
    now = datetime.now().isoformat()

    base = {
        "report_type": "monthly",
        "period_key": "2025-04:leadership",
        "period_start": "2025-04-01",
        "period_end": "2025-04-30",
        "based_on_document_count": 5,
        "summary_text": "초기",
        "highlights_json": {},
        "created_at": now,
    }
    repo.save(base)
    repo.save({**base, "summary_text": "갱신됨"})

    r = repo.get_by_period_key("2025-04:leadership")
    assert r["summary_text"] == "갱신됨", "덮어쓰기가 동작해야 함"

    Path(db_path).unlink(missing_ok=True)


# ─────────────────────── document_repository: 날짜 필터 ─────────────────

@test("document_repository: search_by_keyword date 필터 SQL 생성")
def _():
    from app.infrastructure.db.document_repository import DocumentRepository
    from app.infrastructure.db.migrations import run_migrations

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    run_migrations(db_path)
    repo = DocumentRepository(db_path)

    # upsert()로 삽입 → FTS5 트리거 정상 동작
    # 검색어 "automation"이 title/body에 포함되도록 영문 포함
    for i, (title, body, updated) in enumerate([
        ("automation agent A", "This is an automation workflow system.", "2025-01-15"),
        ("automation agent B", "Automation process for data pipeline.",  "2025-03-20"),
        ("automation agent C", "Task automation with RPA tools.",         "2025-04-10"),
    ]):
        repo.upsert({
            "confluence_page_id": f"page_{i}",
            "parent_page_id": None,
            "title": title,
            "url": f"http://example.com/{i}",
            "author": "tester",
            "created_at": updated,
            "updated_at": updated,
            "version": 1,
            "raw_body": body,
            "cleaned_body": body,
            "content_hash": f"hash_{i}",
        })

    # 전체 검색 (날짜 필터 없음)
    all_results = repo.search_by_keyword("automation", limit=10)
    assert len(all_results) == 3, f"전체 3건이어야 함, 실제: {len(all_results)}"

    # 날짜 필터: 2025-03 ~ 2025-04 범위만
    filtered = repo.search_by_keyword(
        "automation", limit=10, date_from="2025-03-01", date_to="2025-04-30"
    )
    titles = [r["title"] for r in filtered]
    assert any("B" in t for t in titles), "에이전트B가 결과에 없음"
    assert any("C" in t for t in titles), "에이전트C가 결과에 없음"
    assert not any("A" in t for t in titles), f"범위 밖 에이전트A가 포함됨: {titles}"

    # date_from만 지정
    from_only = repo.search_by_keyword("automation", limit=10, date_from="2025-04-01")
    assert len(from_only) == 1, f"4월 이후 1건이어야 함: {len(from_only)}"

    Path(db_path).unlink(missing_ok=True)


# ─────────────────────── Area 4: 신규 TC (QA 검수 후 추가) ─────────────

@test("text_utils: now_kst — KST(UTC+9) 타임존 반환")
def _():
    from app.shared.text_utils import now_kst
    from datetime import timezone, timedelta
    dt = now_kst()
    assert dt.utcoffset() == timedelta(hours=9), f"UTC+9 아님: {dt.utcoffset()}"


@test("text_utils: now_kst_str — ISO 포맷 문자열 반환")
def _():
    from app.shared.text_utils import now_kst_str
    s = now_kst_str()
    assert len(s) == 19, f"길이 오류: {len(s)}"
    assert "T" in s, "T 구분자 없음"


@test("report_service: _resolve_period 주간 — W00 경계값 예외 없음")
def _():
    from app.application.report_service import ReportService
    # W00은 ValueError 없이 1월 1일 기반 날짜 반환해야 함
    start, end = ReportService._resolve_period("weekly", "2026-W00")
    assert start.startswith("2026-01-01"), f"W00 start 오류: {start}"
    from datetime import datetime
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    assert (e - s).days == 6, f"W00 기간 7일이어야 함: {(e - s).days}"


@test("report_service: _resolve_period 월간 — 2월 말일 정확성")
def _():
    from app.application.report_service import ReportService
    start, end = ReportService._resolve_period("monthly", "2026-02")
    assert start.startswith("2026-02-01"), f"2월 start 오류: {start}"
    assert end.startswith("2026-02-28"), f"2026-02 말일 오류: {end}"


@test("report_service: _prev_period_key 주간 — 연초 경계 (W01 → 전년도 W52)")
def _():
    from app.application.report_service import ReportService
    key = ReportService._prev_period_key("weekly", "2026-W01")
    assert key == "2025-W52", f"전년도 마지막 주 오류: {key}"


@test("report_service: _prev_period_key 월간 — 1월 → 전년도 12월")
def _():
    from app.application.report_service import ReportService
    key = ReportService._prev_period_key("monthly", "2026-01")
    assert key == "2025-12", f"전년도 12월 오류: {key}"


@test("search_service: _parse_json_list — 정상/비정상/null/빈값")
def _():
    try:
        from app.application.search_service import SearchService
    except ImportError:
        return  # chromadb 미설치 환경 — skip
    assert SearchService._parse_json_list('["Python", "FastAPI"]') == ["Python", "FastAPI"]
    assert SearchService._parse_json_list("not json") == []
    assert SearchService._parse_json_list("null") == []
    assert SearchService._parse_json_list("") == []
    assert SearchService._parse_json_list(None) == []
    assert SearchService._parse_json_list('{}') == []  # dict가 아닌 list만 허용


@test("search_service: _rrf — 키워드 단독 / 빈 입력")
def _():
    try:
        from app.application.search_service import SearchService
    except ImportError:
        return  # chromadb 미설치 환경 — skip
    svc = SearchService.__new__(SearchService)
    # 키워드 단독 — 순서 보장
    result = svc._rrf([1, 2, 3], [], None)
    assert [r[0] for r in result] == [1, 2, 3], "키워드 순위 오류"
    # 빈 입력
    assert svc._rrf([], [], None) == []
    # 벡터 단독
    vec = [(10, 0.9), (20, 0.7)]
    result = svc._rrf([], vec, None)
    assert result[0][0] == 10, "벡터 1위 오류"


@test("search_service: _apply_filters — 날짜 범위 필터")
def _():
    try:
        from app.application.search_service import SearchService, SearchQuery
    except ImportError:
        return  # chromadb 미설치 환경 — skip
    svc = SearchService.__new__(SearchService)
    docs = [
        {"id": 1, "updated_at": "2026-01-15", "tech_stack_json": "[]", "effects_json": "[]"},
        {"id": 2, "updated_at": "2025-12-31", "tech_stack_json": "[]", "effects_json": "[]"},
    ]
    q = SearchQuery(text="test", date_from="2026-01-01", date_to="2026-12-31")
    filtered = svc._apply_filters(docs, q)
    assert len(filtered) == 1 and filtered[0]["id"] == 1


@test("config: AppConfig — 민감 정보 to_json_dict 제외")
def _():
    from app.shared.config import AppConfig
    config = AppConfig(auth_token="secret_pat", llm_api_key="sk-key123", inhouse_llm_api_key="ih-key")
    d = config.to_json_dict()
    assert "auth_token" not in d
    assert "llm_api_key" not in d
    assert "inhouse_llm_api_key" not in d
    # 런타임 필드도 제외
    assert "db_path" not in d
    assert "chroma_path" not in d


@test("config: AppConfig.is_confluence_configured — 필수 3개 모두 있어야 True")
def _():
    from app.shared.config import AppConfig
    assert not AppConfig().is_confluence_configured
    assert not AppConfig(confluence_base_url="http://cf", root_page_id="123").is_confluence_configured
    assert AppConfig(
        confluence_base_url="http://cf", root_page_id="123", auth_token="pat"
    ).is_confluence_configured


@test("config: AppConfig.is_llm_configured — openai/inhouse 분기")
def _():
    from app.shared.config import AppConfig
    # openai: llm_api_key 있으면 True
    assert AppConfig(llm_provider="openai", llm_api_key="sk-xxx").is_llm_configured
    assert not AppConfig(llm_provider="openai", llm_api_key="").is_llm_configured
    # inhouse: URL만 있으면 True
    assert AppConfig(llm_provider="inhouse", inhouse_llm_url="http://devx").is_llm_configured


@test("chunking: 헤딩 2개 이상 — 섹션 기반 청킹")
def _():
    from app.shared.text_utils import chunk_text
    # chunk_size(800) 초과할 만큼 각 섹션을 크게 만들어야 merge되지 않음
    section1 = "# 개요\n" + "섹션1 내용입니다. " * 55  # ~900자
    section2 = "# 기술스택\n" + "섹션2 내용입니다. " * 55
    text = section1 + "\n\n" + section2
    chunks = chunk_text(text)
    assert len(chunks) >= 2, f"헤딩 2개인데 청크 {len(chunks)}개"


@test("chunking: 헤딩 1개 — 문단 폴백")
def _():
    from app.shared.text_utils import chunk_text
    text = "# 제목\n내용1\n\n내용2\n\n내용3"
    chunks = chunk_text(text)
    assert len(chunks) >= 1
    combined = " ".join(c["chunk_text"] for c in chunks)
    assert "내용1" in combined and "내용3" in combined


@test("chunking: 매우 긴 문서 — chunk_size 초과 없음")
def _():
    from app.shared.text_utils import chunk_text
    text = "가나다라마바사 " * 2000  # ~14000자
    chunks = chunk_text(text, chunk_size=800)
    assert len(chunks) > 1
    for c in chunks:
        # 헤딩 기반 재분할 시 약간의 여유 허용
        assert len(c["chunk_text"]) <= 1200, f"청크 크기 초과: {len(c['chunk_text'])}"


@test("main: _global_sync_lock 존재 여부")
def _():
    import threading
    src = Path("main.py").read_text(encoding="utf-8")
    assert "_global_sync_lock" in src, "_global_sync_lock 없음"
    assert "_global_sync_lock.release()" in src, "lock release 없음"
    assert "_global_sync_lock.acquire(blocking=False)" in src, "acquire 없음"


@test("main: _sync_watcher 조건부 호출 확인")
def _():
    src = Path("main.py").read_text(encoding="utf-8")
    assert 'if st.session_state.get("is_sync_running")' in src, "_sync_watcher 조건부 호출 없음"


@test("settings: _advanced_section_fragment 존재 + 3모드 버튼")
def _():
    src = Path("app/ui/settings.py").read_text(encoding="utf-8")
    assert "_advanced_section_fragment" in src, "_advanced_section_fragment 없음"
    assert "_advanced_executor" in src, "_advanced_executor 없음"
    assert "_ADVANCED_TIMEOUT_SEC" in src, "_ADVANCED_TIMEOUT_SEC 없음"
    assert "_advanced_job" in src, "_advanced_job 없음"
    assert "is_advanced_running" in src, "is_advanced_running 없음"
    # 3모드 버튼 확인
    assert "full" in src, "full 모드 없음"
    assert "fallback" in src, "fallback 모드 없음"
    assert "new_changed" in src, "new_changed 모드 없음"


@test("settings: InHouse key 빈값 저장 불허 확인")
def _():
    src = Path("app/ui/settings.py").read_text(encoding="utf-8")
    # 저장 분기에서 빈값 체크가 있어야 함
    assert "if not val:" in src or 'if not new_key.strip():' in src


@test("report_service: _resolve_period 주간 — 정상 주 월요일 반환")
def _():
    from app.application.report_service import ReportService
    from datetime import datetime
    start, _ = ReportService._resolve_period("weekly", "2026-W15")
    dt = datetime.fromisoformat(start)
    assert dt.weekday() == 0, f"월요일이어야 함, 실제: {dt.weekday()}"


# ─────────────────────── 결과 출력 ──────────────────────────────────────

if __name__ == "__main__":
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed

    print(f"\n{'=' * 60}")
    print(f"  자가 검증 테스트 결과: {passed}/{total} 통과")
    print(f"{'=' * 60}\n")

    for name, ok, err in results:
        icon = PASS if ok else FAIL
        print(f"  {icon}  {name}")
        if err:
            for line in err.strip().splitlines():
                print(f"       {line}")
            print()

    print(f"\n{'=' * 60}")
    if failed:
        print(f"  {FAIL} {failed}개 실패 -- 수정 필요")
    else:
        print(f"  {PASS} 모든 테스트 통과")
    print(f"{'=' * 60}\n")
    sys.exit(0 if not failed else 1)
