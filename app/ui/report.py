import time
from concurrent.futures import ThreadPoolExecutor
import streamlit as st
from app.shared.config import AppConfig

from app.application.report_service import ReportService, PERSPECTIVES
from app.infrastructure.db.report_repository import ReportRepository

_REPORT_TIMEOUT_SEC = 300  # LLM 생성 최대 5분

# ── ThreadPoolExecutor: Future로 결과 전달 (session_state 직접 쓰기 금지) ──
_executor = ThreadPoolExecutor(max_workers=2)


def _report_job(config: AppConfig, report_type: str, period: str, perspective: str, regenerate: bool) -> dict:
    """ThreadPoolExecutor에서 실행. session_state를 전혀 건드리지 않음."""
    svc = ReportService.from_config(config)
    if regenerate:
        return svc.generate(report_type, period, perspective)
    return svc.get_or_generate(report_type, period, perspective)


@st.fragment(run_every=2)
def _report_progress_fragment() -> None:
    """
    리포트 생성 중에만 render_report()가 호출하는 polling fragment.
    Future.done()으로 완료 감지 → session_state 갱신 + st.rerun().
    """
    future = st.session_state.get("_report_future")
    if future is None:
        st.session_state["is_report_generating"] = False
        st.rerun()
        return

    elapsed = time.monotonic() - st.session_state.get("ss_report_start_time", 0)

    # ── 완료 감지 ────────────────────────────────────────────────────
    if future.done():
        from app.ui._helpers import render_progress_bar
        period_label = st.session_state.get("_report_period_label", "")
        render_progress_bar(100, f"{period_label} 리포트 생성 완료", done=True, color="orange")
        time.sleep(0.8)
        try:
            report = future.result()
            st.session_state["_report_result"] = report
            st.session_state["_report_regenerated"] = st.session_state.get("_report_regenerate_flag", False)
            st.session_state.pop("_report_error", None)
        except Exception as e:
            st.session_state["_report_error"] = str(e)
            st.session_state.pop("_report_result", None)
        finally:
            st.session_state["is_report_generating"] = False
            st.session_state["_report_future"] = None
        st.rerun()
        return

    # ── 타임아웃 ────────────────────────────────────────────────────
    if elapsed > _REPORT_TIMEOUT_SEC:
        future.cancel()
        st.session_state["is_report_generating"] = False
        st.session_state["_report_future"] = None
        st.session_state["_report_error"] = (
            f"리포트 생성이 {_REPORT_TIMEOUT_SEC // 60}분을 초과했습니다. "
            "LLM 응답 지연 가능성이 있습니다. 잠시 후 재시도하세요."
        )
        st.rerun()
        return

    # ── 진행률 바 ────────────────────────────────────────────────────
    pct = min(88, int(elapsed / _REPORT_TIMEOUT_SEC * 100))
    period_label = st.session_state.get("_report_period_label", "")
    from app.ui._helpers import render_progress_bar
    render_progress_bar(
        pct, f"{period_label} 리포트 생성 중",
        sublabel=f"LLM이 분석 중입니다 · {int(elapsed)}초 경과 · 탭을 이동해도 계속 진행됩니다",
        color="orange",
    )


def render_report(config: AppConfig) -> None:
    st.subheader("AI 분석 리포트")
    st.caption(
        "📋 **분석 기준** — "
        "페이지 제목·요약·문제(problem)·본문 앞 300자를 LLM에 전달해 분석  |  "
        "LLM 메타데이터(기술스택·효과·카테고리)가 추출된 경우 해당 값도 활용  |  "
        "같은 기간(월/주) + 같은 관점의 리포트는 재생성 시 최신 내용으로 덮어씀"
    )

    is_reporting = st.session_state.get("is_report_generating", False)

    col_type, col_period, col_persp = st.columns([2, 2, 2])

    with col_type:
        report_type = st.selectbox(
            "리포트 유형",
            options=["monthly", "weekly"],
            format_func=lambda x: "월간" if x == "monthly" else "주간",
            disabled=is_reporting,
        )

    period_options = _build_period_options(report_type, config.db_path)
    with col_period:
        if not period_options:
            st.warning("문서가 없습니다. 현행화를 먼저 실행하세요.")
            selected_period = None
        else:
            selected_period = st.selectbox(
                "기간",
                options=list(period_options.keys()),
                format_func=lambda k: period_options[k],
                disabled=is_reporting,
            )

    with col_persp:
        perspective = st.radio(
            "관점",
            options=list(PERSPECTIVES.keys()),
            format_func=lambda k: PERSPECTIVES[k][0],
            horizontal=True,
            key="report_perspective",
            disabled=is_reporting,
            help=(
                "**리더십 관점** — 경영진·팀장을 위한 요약입니다. "
                "핵심 인사이트 Top3, 3기간 카테고리 추이, 리스크, 실행 권고, 성숙도 지표를 포함합니다.\n\n"
                "**실무자 관점** — 개발자·실무 담당자를 위한 기술 심층 분석입니다. "
                "기술 인사이트 Top3, 구현 패턴, 재사용 가능한 패턴, 기술 리스크, 실행 권고를 포함합니다."
            ),
        )

    col_btn, col_regen = st.columns([1, 1])
    with col_btn:
        load_clicked = st.button("조회", width='stretch', disabled=is_reporting)
    with col_regen:
        regen_clicked = st.button(
            "생성 중..." if is_reporting else "재생성 ↺",
            width='stretch',
            disabled=is_reporting,
        )

    # ── 버튼 클릭: 백그라운드 스레드 시작 ───────────────────────────────
    if (load_clicked or regen_clicked) and not is_reporting:
        if not selected_period:
            st.warning("분석할 기간을 선택하세요.")
            _show_report_list(config, report_type, perspective)
            return

        if not config.is_llm_configured:
            st.error("리포트 생성을 위해 설정 탭에서 LLM을 설정해주세요.")
            return

        regenerate = bool(regen_clicked)
        persp_label = PERSPECTIVES[perspective][0]
        future = _executor.submit(_report_job, config, report_type, selected_period, perspective, regenerate)

        st.session_state["is_report_generating"] = True
        st.session_state["ss_report_start_time"] = time.monotonic()
        st.session_state["_report_future"] = future
        st.session_state["_report_regenerate_flag"] = regenerate
        st.session_state["_report_period_label"] = f"{selected_period} [{persp_label}]"
        st.session_state.pop("_report_error", None)
        st.session_state.pop("_report_result", None)
        st.rerun()

    # ── 상태별 분기: 생성 중→polling fragment / 완료→정적 렌더링 ────────
    if is_reporting:
        _report_progress_fragment()
        return

    # ── 오류 표시 ────────────────────────────────────────────────────────
    if err := st.session_state.get("_report_error"):
        st.error(f"리포트 생성 실패: {err}")

    # ── 결과 표시 ────────────────────────────────────────────────────────
    if result := st.session_state.get("_report_result"):
        if st.session_state.pop("_report_regenerated", False):
            st.success("리포트 생성 완료")
        _render_report_detail(result)
        return

    # ── 기본: 저장된 리포트 목록 ────────────────────────────────────────
    _show_report_list(config, report_type, perspective)


def _render_report_detail(report: dict) -> None:
    summary_text = report.get("summary_text") or ""
    period_key = report.get("_period_key") or report.get("period_key", "").split(":")[0]

    st.divider()

    col_meta, col_dl = st.columns([4, 1])
    with col_meta:
        highlights = report.get("highlights_json") or {}
        persp_raw = highlights.get("perspective", "leadership")
        persp_label = PERSPECTIVES.get(persp_raw, ("리더십 관점",))[0]
        st.caption(
            f"**{persp_label}**  |  "
            f"분석 문서 **{report.get('based_on_document_count', 0)}건**  |  "
            f"생성: {(report.get('created_at') or '')[:16].replace('T', ' ')}"
        )
    with col_dl:
        label = "월간" if report["report_type"] == "monthly" else "주간"
        filename = f"{label}_리포트_{period_key}_{persp_raw}.md"
        st.download_button(
            "⬇ MD 다운로드",
            data=summary_text.encode("utf-8"),
            file_name=filename,
            mime="text/markdown",
            width='stretch',
        )

    if summary_text:
        st.markdown(summary_text)
    else:
        st.info("내용이 없습니다. 재생성 버튼을 눌러주세요.")


def _show_report_list(config: AppConfig, report_type: str, perspective: str) -> None:
    repo = ReportRepository(config.db_path)
    reports = repo.get_by_type(report_type, perspective)
    label = "월간" if report_type == "monthly" else "주간"
    persp_label = PERSPECTIVES[perspective][0]

    if not reports:
        st.info(f"저장된 {label} [{persp_label}] 리포트가 없습니다. 기간 선택 후 [조회]를 눌러주세요.")
        return

    PAGE_SIZE = 5
    total_pages = max(1, (len(reports) + PAGE_SIZE - 1) // PAGE_SIZE)
    page_key = f"report_list_page_{report_type}_{perspective}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 1
    page = st.session_state[page_key]

    st.markdown(f"**저장된 {label} [{persp_label}] 리포트 목록** ({len(reports)}건)")

    page_reports = reports[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    for r in page_reports:
        period_key = r.get("_period_key") or r["period_key"].split(":")[0]
        created = (r.get("created_at") or "")[:16].replace("T", " ")
        title = (
            f"{period_key}  "
            f"({r['period_start'][:10]} ~ {r['period_end'][:10]})  "
            f"—  {r.get('based_on_document_count', 0)}건  |  생성: {created}"
        )
        with st.expander(title):
            summary = r.get("summary_text") or ""
            if summary:
                lbl = "월간" if r["report_type"] == "monthly" else "주간"
                persp_raw = (r.get("highlights_json") or {}).get("perspective", perspective)
                dl_key = f"dl_{r.get('period_key')}"
                expand_key = f"expand_{r.get('period_key')}"
                is_expanded = st.session_state.get(expand_key, False)

                # 다운로드 버튼 우측 상단 고정
                col_content, col_dl = st.columns([5, 1])
                with col_dl:
                    st.download_button(
                        "⬇ MD 다운로드",
                        data=summary.encode("utf-8"),
                        file_name=f"{lbl}_리포트_{period_key}_{persp_raw}.md",
                        mime="text/markdown",
                        key=dl_key,
                        width='stretch',
                    )
                with col_content:
                    if is_expanded:
                        st.markdown(summary)
                    else:
                        st.markdown(summary[:800])

                    if len(summary) > 800:
                        btn_label = "▲ 접기" if is_expanded else "▼ 더보기"
                        if st.button(btn_label, key=f"toggle_{r.get('period_key')}"):
                            st.session_state[expand_key] = not is_expanded
                            st.rerun()
            else:
                st.caption("내용 없음")

    # 페이징 — 목록 아래 중앙 정렬
    from app.ui._helpers import render_pager
    prev_clicked, next_clicked = render_pager(page, total_pages, f"rlist_{report_type}_{perspective}")
    if prev_clicked:
        st.session_state[page_key] = page - 1
        st.rerun()
    if next_clicked:
        st.session_state[page_key] = page + 1
        st.rerun()


@st.cache_data(ttl=3600)
def _build_period_options(report_type: str, db_path: str) -> dict[str, str]:
    """DB에서 실제 문서가 있는 기간만 반환. 건수 포함. TTL 30초 캐시."""
    from app.infrastructure.db.connection import db_session
    from app.shared.text_utils import now_kst as _now_kst

    now = _now_kst()
    candidates: list[tuple[str, str]] = []

    if report_type == "weekly":
        import datetime as dt
        for i in range(52):
            d = now - dt.timedelta(weeks=i)
            key = f"{d.year}-W{d.strftime('%W')}"
            # W00은 연초 부분 주 — strptime 경계값 오류 방지를 위해 제외
            if key.endswith("-W00"):
                continue
            label = f"{key} ({d.strftime('%Y/%m/%d')} 주)"
            candidates.append((key, label))
    else:
        for i in range(24):
            month = now.month - i
            year = now.year
            while month <= 0:
                month += 12
                year -= 1
            key = f"{year}-{month:02d}"
            label = f"{year}년 {month}월"
            candidates.append((key, label))

    try:
        with db_session(db_path) as conn:
            if report_type == "weekly":
                rows = conn.execute(
                    "SELECT strftime('%Y',created_at)||'-W'||strftime('%W',created_at) as wk, COUNT(*) "
                    "FROM documents WHERE is_deleted=0 AND strftime('%W',created_at) != '00' GROUP BY wk"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT substr(created_at,1,7) as ym, COUNT(*) "
                    "FROM documents WHERE is_deleted=0 GROUP BY ym"
                ).fetchall()
        counts = {r[0]: r[1] for r in rows}
    except Exception:
        counts = {}

    options: dict[str, str] = {}
    for key, label in candidates:
        cnt = counts.get(key, 0)
        if cnt > 0:
            if report_type == "weekly":
                options[key] = f"{label} ({cnt}건)"
            else:
                year, month = key.split("-")
                options[key] = f"{year}년 {int(month)}월 ({cnt}건)"

    return options
