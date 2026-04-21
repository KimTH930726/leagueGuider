import time
from concurrent.futures import ThreadPoolExecutor
import streamlit as st
from datetime import date
from app.shared.config import AppConfig
from app.application.search_service import SearchService, SearchQuery
from app.domain.models import SearchResult

_SCORE_FILTER_RATIO = 0.15
_SCORE_HIGH_RATIO   = 0.70
_SCORE_MID_RATIO    = 0.40
_SEARCH_TIMEOUT_SEC = 90
_PAGE_SIZE          = 6

# ── SearchService 프로세스 싱글톤 ────────────────────────────────────
import threading as _threading
_svc_instance: "SearchService | None" = None
_svc_lock = _threading.Lock()

# ── ThreadPoolExecutor: 동시 검색 허용 (각 Future가 독립적으로 완료 추적) ──
# threading.Thread 대신 Future를 사용해 결과를 session_state 경유 없이 전달.
# Streamlit 1.x에서 background thread → st.session_state 쓰기는
# ScriptRunContext 부재로 반영이 보장되지 않음. (로그: "missing ScriptRunContext!")
_executor = ThreadPoolExecutor(max_workers=4)


def _get_search_service(config: AppConfig) -> SearchService:
    global _svc_instance
    if _svc_instance is None:
        with _svc_lock:
            if _svc_instance is None:
                _svc_instance = SearchService.from_config(config)
    return _svc_instance


def reset_search_service() -> None:
    """설정 변경 시 호출 — 다음 검색에서 새 config로 재초기화."""
    global _svc_instance
    with _svc_lock:
        _svc_instance = None


def _can_use_vector(config: AppConfig) -> bool:
    if config.embedding_provider == "local":
        return True
    return bool(config.llm_api_key)


def _score_label(score: float, max_score: float, mode: str) -> tuple[str, str]:
    if mode == "keyword" or max_score == 0:
        return ("일치", "#6e7681")
    ratio = score / max_score
    if ratio >= _SCORE_HIGH_RATIO:
        return ("연관성 높음", "#1a7f37")
    elif ratio >= _SCORE_MID_RATIO:
        return ("연관성 중간", "#d6a100")
    return ("연관성 낮음", "#6e7681")


def _estimated_pct(elapsed: float) -> tuple[int, str]:
    if elapsed < 2:
        return max(5, int(elapsed / 2 * 20)), "검색어 처리 중"
    elif elapsed < 8:
        return 20 + int((elapsed - 2) / 6 * 40), "키워드 / 벡터 검색 중"
    elif elapsed < 25:
        return 60 + int((elapsed - 8) / 17 * 25), "결과 병합 및 분석 중"
    return 85, "최종 정렬 중..."


def _search_job(config: AppConfig, q: SearchQuery) -> tuple[list[SearchResult], list[str]]:
    """ThreadPoolExecutor에서 실행되는 검색 작업. session_state를 전혀 건드리지 않음."""
    svc = _get_search_service(config)
    return svc.search(q)  # returns (results, expanded_terms)


@st.fragment(run_every=2)
def _search_progress_fragment() -> None:
    """
    검색 진행 중에만 render_search()가 호출하는 polling fragment.
    Future.done()으로 완료를 감지하고, session_state 갱신 + st.rerun()으로 정적 렌더링 전환.
    """
    future = st.session_state.get("_search_future")
    if future is None:
        # 예외적 상황 — 상태 정리
        st.session_state["is_search_running"] = False
        st.rerun()
        return

    elapsed = time.monotonic() - st.session_state.get("ss_search_start_time", 0)

    # ── 완료 감지 ────────────────────────────────────────────────────
    if future.done():
        from app.ui._helpers import render_progress_bar
        render_progress_bar(100, "검색 완료", done=True, color="blue")
        time.sleep(0.8)
        try:
            results, terms = future.result()
            st.session_state["_search_results"] = results
            st.session_state["_expanded_terms"] = terms
            st.session_state["_search_mode_used"] = st.session_state.get("_search_effective_mode", "hybrid")
            st.session_state["search_page"] = 1
            st.session_state.pop("_search_error", None)
        except Exception as e:
            st.session_state["_search_error"] = str(e)
            st.session_state.pop("_search_results", None)
        finally:
            st.session_state["is_search_running"] = False
            st.session_state["_search_future"] = None
        st.rerun()
        return

    # ── 타임아웃 ────────────────────────────────────────────────────
    if elapsed > _SEARCH_TIMEOUT_SEC:
        future.cancel()
        st.session_state["is_search_running"] = False
        st.session_state["_search_future"] = None
        st.session_state["_search_error"] = f"검색이 {_SEARCH_TIMEOUT_SEC}초를 초과했습니다."
        st.session_state.pop("_search_results", None)
        st.rerun()
        return

    # ── 진행률 바 ────────────────────────────────────────────────────
    pct, label = _estimated_pct(elapsed)
    from app.ui._helpers import render_progress_bar
    render_progress_bar(
        pct, f"검색 진행 중 · {label}",
        sublabel=f"탭을 이동해도 검색이 계속 진행됩니다 · {int(elapsed)}초 경과",
        color="blue",
    )


def _render_search_results() -> None:
    """검색 완료 후 정적 렌더링 — fragment polling 없음."""
    results: list[SearchResult] = st.session_state.get("_search_results", [])
    used_mode = st.session_state.get("_search_mode_used", "hybrid")
    expanded_terms = st.session_state.get("_expanded_terms", [])

    if not results:
        st.info("검색 결과가 없습니다.")
        return

    if expanded_terms:
        terms_str = " · ".join(f"`{t}`" for t in expanded_terms[:5])
        st.caption(f"검색어 확장: {terms_str}")

    if used_mode != "keyword":
        max_score = results[0].score
        ratio = 0.05 if len(results) < 5 else _SCORE_FILTER_RATIO
        results = [r for r in results if r.score >= max_score * ratio]

    total_pages = max(1, (len(results) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    if "search_page" not in st.session_state:
        st.session_state["search_page"] = 1
    st.session_state["search_page"] = max(1, min(st.session_state["search_page"], total_pages))
    page = st.session_state["search_page"]

    st.markdown(f"**{len(results)}건** 검색됨")
    st.divider()

    max_score = results[0].score if results else 1.0
    page_results = results[(page - 1) * _PAGE_SIZE: page * _PAGE_SIZE]

    # 같은 행의 두 카드 높이 균일화 — Streamlit 컬럼은 기본으로 stretch 안 함
    st.markdown(
        """
        <style>
        [data-testid="column"] > div:first-child {
            display: flex; flex-direction: column; height: 100%;
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            flex: 1; display: flex; flex-direction: column;
        }
        [data-testid="stVerticalBlockBorderWrapper"] > div {
            flex: 1; display: flex; flex-direction: column;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    for i in range(0, len(page_results), 2):
        cols = st.columns(2, gap="medium")
        for j, col in enumerate(cols):
            if i + j < len(page_results):
                with col:
                    _render_result_card(page_results[i + j], max_score, used_mode)

    # 페이징 — 카드 아래 중앙 정렬
    if total_pages > 1:
        st.divider()
    from app.ui._helpers import render_pager
    prev_clicked, next_clicked = render_pager(page, total_pages, "search")
    if prev_clicked:
        st.session_state["search_page"] -= 1
        st.rerun()
    if next_clicked:
        st.session_state["search_page"] += 1
        st.rerun()


def render_search(config: AppConfig) -> None:
    st.subheader("에이전트 검색")
    st.caption(
        "사내 AI 에이전트 문서를 검색합니다. "
        "**평소엔 하이브리드 모드**를 쓰세요 — 단어가 정확히 기억 안 나도 의미로 찾아줍니다."
    )

    is_searching = st.session_state.get("is_search_running", False)
    is_sync_busy = (
        st.session_state.get("is_sync_running", False)
        or st.session_state.get("is_manual_sync_running", False)
    )
    if is_sync_busy:
        st.info("⚡ 현행화가 진행 중입니다. 이 동안 **키워드 검색**만 동작합니다.")

    with st.form("search_form", clear_on_submit=False):
        col_input, col_btn = st.columns([5, 1])
        with col_input:
            query_text = st.text_input(
                "검색어를 입력하세요",
                placeholder="예: RPA 업무 자동화, 고객 응대 챗봇...",
                label_visibility="collapsed",
                disabled=is_searching,
            )
        with col_btn:
            search_clicked = st.form_submit_button(
                "검색 중..." if is_searching else "검색",
                type="primary",
                width='stretch',
                disabled=is_searching,
            )

    with st.expander("검색 옵션", expanded=False):
        col_mode, col_k = st.columns([3, 1])
        with col_mode:
            mode = st.radio(
                "검색 모드",
                options=["hybrid", "vector", "keyword"],
                format_func=lambda x: {
                    "hybrid":  "하이브리드 (기본 추천)",
                    "vector":  "유사도",
                    "keyword": "키워드",
                }[x],
                horizontal=True,
                key="search_mode",
                help=(
                    "**하이브리드** — 키워드 일치 + 의미 유사도를 함께 계산해 가장 관련성 높은 문서를 찾습니다. 평소엔 이 모드를 쓰세요.\n\n"
                    "**유사도** — 입력한 단어와 의미·맥락이 비슷한 문서를 찾습니다. '이런 느낌의 에이전트' 처럼 개념으로 탐색할 때 유용합니다.\n\n"
                    "**키워드** — 입력한 단어가 제목·본문에 정확히 포함된 문서만 찾습니다. 특정 기술명(RPA, GPT-4 등)이나 고유명사를 정확히 알 때 사용하세요."
                ),
            )
        with col_k:
            top_k = st.number_input("결과 수", min_value=1, max_value=50, value=10, key="top_k")

        # 선택한 모드에 맞는 안내 표시
        _mode_now = st.session_state.get("search_mode", "hybrid")
        _mode_hints = {
            "hybrid":  "💡 **하이브리드** — 키워드 일치 + 의미 유사도를 함께 활용합니다. "
                       "단어가 정확히 기억 안 나거나 넓게 찾고 싶을 때 적합합니다.",
            "vector":  "💡 **유사도** — 단어 일치 없이 의미·맥락만으로 검색합니다. "
                       "'비슷한 사례', '이런 느낌의 에이전트' 처럼 개념으로 찾을 때 유용합니다.",
            "keyword": "💡 **키워드** — 입력한 단어가 제목·본문에 정확히 포함된 문서만 찾습니다. "
                       "특정 기술명(RPA, GPT-4 등)이나 고유명사를 정확히 알 때 사용하세요.",
        }
        st.caption(_mode_hints.get(_mode_now, ""))

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            date_from = st.date_input(
                "시작일 (문서 등록일 기준)", value=None,
                min_value=date(2020, 1, 1), max_value=date.today(),
                key="date_from", format="YYYY-MM-DD",
            )
        with col_d2:
            date_to = st.date_input(
                "종료일 (문서 등록일 기준)", value=None,
                min_value=date(2020, 1, 1), max_value=date.today(),
                key="date_to", format="YYYY-MM-DD",
            )
        if date_from and date_to and date_from > date_to:
            st.error("⚠️ 시작일이 종료일보다 클 수 없습니다.")

        use_llm_rewrite = False
        if config.is_llm_configured:
            use_llm_rewrite = st.checkbox(
                "검색어 자동 확장 (LLM)",
                value=False,
                key="use_llm_rewrite",
                help=(
                    "검색 결과가 너무 적거나, 단어가 정확히 기억 안 날 때 켜세요.\n\n"
                    "LLM이 입력한 검색어의 동의어·관련 용어를 자동으로 추가해 "
                    "더 많은 문서를 찾아줍니다.\n\n"
                    "예) '자동화' 검색 시 → RPA, 자동처리도 함께 검색\n"
                    "예) '고객 응대' 검색 시 → 상담봇, 고객지원도 함께 검색\n\n"
                    "⏱ 응답이 1~2초 더 걸립니다."
                ),
            )
            if use_llm_rewrite or st.session_state.get("use_llm_rewrite"):
                st.caption(
                    "🔎 **검색어 자동 확장 켜짐** — LLM이 동의어·관련 용어를 추가해 검색합니다. "
                    "결과 상단에 확장된 검색어 목록이 표시됩니다."
                )

    mode = st.session_state.get("search_mode", "hybrid")
    top_k = st.session_state.get("top_k", 10)
    date_from_val: date | None = st.session_state.get("date_from")
    date_to_val: date | None = st.session_state.get("date_to")
    use_llm_rewrite = st.session_state.get("use_llm_rewrite", False)

    if date_from_val and date_to_val and date_from_val > date_to_val:
        return

    if search_clicked and query_text.strip():
        effective_mode = mode
        if mode in ("vector", "hybrid") and not _can_use_vector(config):
            effective_mode = "keyword"
        if is_sync_busy and effective_mode in ("vector", "hybrid"):
            effective_mode = "keyword"

        q = SearchQuery(
            text=query_text.strip(),
            mode=effective_mode,
            tech_stack=[],
            date_from=date_from_val.isoformat() if date_from_val else None,
            date_to=date_to_val.isoformat() if date_to_val else None,
            top_k=int(top_k),
            use_llm_rewrite=use_llm_rewrite,
        )
        future = _executor.submit(_search_job, config, q)

        st.session_state["is_search_running"] = True
        st.session_state["ss_search_start_time"] = time.monotonic()
        st.session_state["_search_future"] = future
        st.session_state["_search_effective_mode"] = effective_mode
        st.session_state.pop("_search_error", None)
        st.session_state.pop("_search_results", None)
        st.session_state["search_page"] = 1
        st.rerun()

    # ── 상태별 분기: 검색 중 → polling fragment / 완료 → 정적 렌더링 ──
    if is_searching:
        _search_progress_fragment()
    elif "_search_results" in st.session_state:
        _render_search_results()
    elif err := st.session_state.get("_search_error"):
        st.error(f"검색 오류: {err}")
    else:
        st.caption("검색어를 입력하고 검색 버튼을 눌러주세요.")


def _render_result_card(r: SearchResult, max_score: float, mode: str) -> None:
    with st.container(border=True):
        title = r.agent_name or r.title
        label_text, label_color = _score_label(r.score, max_score, mode)

        # ── 제목 + 연관성 배지 ──────────────────────────────────────────
        col_title, col_badge = st.columns([3, 1])
        with col_title:
            if r.url:
                st.markdown(f"**[{title}]({r.url})**")
            else:
                st.markdown(f"**{title}**")
        with col_badge:
            st.markdown(
                f'<div style="text-align:right;margin-top:4px">'
                f'<span style="background:{label_color};color:white;padding:3px 8px;'
                f'border-radius:10px;font-size:0.75em;font-weight:600">{label_text}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── 요약 (없으면 고정 높이 placeholder — 행 높이 균일화) ──────
        summary = (r.one_line_summary or "").strip()
        if summary:
            st.caption(summary[:85] + "…" if len(summary) > 85 else summary)
        else:
            st.markdown('<div style="height:1.2rem"></div>', unsafe_allow_html=True)

        # ── 기술스택 배지 (없으면 고정 높이 placeholder) ───────────────
        if r.tech_stack:
            badges = " ".join(
                f'<span style="background:#1a2f4a;color:#7ab3e0;padding:2px 9px;'
                f'border-radius:12px;font-size:0.76em;font-weight:500">{t}</span>'
                for t in r.tech_stack[:5]
            )
            st.markdown(f'<div style="margin:4px 0">{badges}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="height:1.6rem"></div>', unsafe_allow_html=True)

        # ── 작성자 / 날짜 (없으면 고정 높이 placeholder) ──────────────
        meta = []
        if r.author:
            meta.append(f"✍️ {r.author}")
        if r.updated_at:
            meta.append(f"📅 {r.updated_at[:10]}")
        if meta:
            st.caption("  ".join(meta))
        else:
            st.markdown('<div style="height:1.2rem"></div>', unsafe_allow_html=True)

        # ── 기대효과 배지 (없으면 고정 높이 placeholder) ──────────────
        if r.effects:
            badges = " ".join(
                f'<span style="background:#0e2e1e;color:#5db87a;padding:2px 9px;'
                f'border-radius:12px;font-size:0.76em;font-weight:500">{e}</span>'
                for e in r.effects[:3]
            )
            st.markdown(
                f'<div style="margin:2px 0"><span style="font-size:0.76em;color:#777;'
                f'margin-right:4px">기대효과</span>{badges}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div style="height:1.4rem"></div>', unsafe_allow_html=True)
