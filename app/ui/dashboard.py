import streamlit as st
import pandas as pd
import altair as alt
from app.application.dashboard_service import DashboardService
from app.domain.models import DashboardStats
from app.shared.config import AppConfig


@st.cache_data(ttl=60)
def _load_stats(db_path: str) -> DashboardStats:
    return DashboardService(db_path).get_stats()


# ── 차트 헬퍼 ─────────────────────────────────────────────────────────

def _hbar(data: list[tuple[str, int]], label: str, height: int | None = None) -> alt.Chart:
    df = pd.DataFrame(data, columns=[label, "건수"])
    h = height if height is not None else max(120, len(data) * 38)
    return (
        alt.Chart(df)
        .mark_bar(color="#4C9BE8", cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            x=alt.X("건수:Q", axis=alt.Axis(tickMinStep=1, labelFontSize=11)),
            y=alt.Y(
                f"{label}:N",
                sort="-x",
                axis=alt.Axis(labelFontSize=11, labelLimit=220, labelPadding=4),
                title=None,
            ),
            tooltip=[label, "건수"],
        )
        .properties(height=h)
    )


def _line(data: list[tuple[str, int]], height: int = 160) -> alt.Chart:
    df = pd.DataFrame(data, columns=["월", "건수"])
    base = alt.Chart(df).encode(x=alt.X("월:N", axis=alt.Axis(labelFontSize=11)))
    bars = base.mark_bar(color="#4C9BE8", opacity=0.6)
    line = base.mark_line(color="#F08030", point=True).encode(y="건수:Q")
    return (bars + line).encode(y=alt.Y("건수:Q", axis=alt.Axis(tickMinStep=1))).properties(height=height)


# ── 섹션 렌더 헬퍼 ────────────────────────────────────────────────────

def _render_agent_card(agent: dict) -> None:
    """대표 에이전트 카드."""
    with st.container(border=True):
        name = agent["agent_name"]
        url = agent["url"]
        if url:
            st.markdown(f"**[{name}]({url})**")
        else:
            st.markdown(f"**{name}**")

        summary = agent["one_line_summary"]
        if summary:
            display = summary[:85] + "…" if len(summary) > 85 else summary
            st.caption(display)
        else:
            st.markdown('<div style="height:1.2rem"></div>', unsafe_allow_html=True)

        if agent["tech_stack"]:
            badges = " ".join(
                f'<span style="background:#1a2f4a;color:#7ab3e0;padding:2px 9px;'
                f'border-radius:12px;font-size:0.76em;font-weight:500">{t}</span>'
                for t in agent["tech_stack"][:5]
            )
            st.markdown(f'<div style="margin:4px 0">{badges}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="height:1.6rem"></div>', unsafe_allow_html=True)

        meta = []
        if agent["author"]:
            meta.append(f"✍️ {agent['author']}")
        if agent["created_at"]:
            meta.append(f"📅 {agent['created_at']}")
        if meta:
            st.caption("  ".join(meta))
        else:
            st.markdown('<div style="height:1.2rem"></div>', unsafe_allow_html=True)

        if agent.get("effects"):
            badges = " ".join(
                f'<span style="background:#0e2e1e;color:#5db87a;padding:2px 9px;'
                f'border-radius:12px;font-size:0.76em;font-weight:500">{e}</span>'
                for e in agent["effects"][:3]
            )
            st.markdown(
                f'<div style="margin:2px 0"><span style="font-size:0.76em;color:#777;'
                f'margin-right:4px">기대효과</span>{badges}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div style="height:1.4rem"></div>', unsafe_allow_html=True)


def _render_category_trend(trend: list[dict]) -> None:
    """카테고리 전주 대비 변화 테이블."""
    if not trend:
        st.caption("이번 주 또는 전주 신규 데이터 없음")
        return

    rows = []
    for item in trend:
        delta = item["delta"]
        if delta > 0:
            indicator = f"▲ +{delta}"
            color = "#1a7f37"
        elif delta < 0:
            indicator = f"▼ {delta}"
            color = "#cf222e"
        else:
            indicator = "─"
            color = "#6e7681"

        rows.append(
            f"<tr>"
            f"<td style='padding:3px 8px'>{item['category']}</td>"
            f"<td style='padding:3px 8px;text-align:center'>{item['total']}</td>"
            f"<td style='padding:3px 8px;text-align:center'>{item['this_week']}</td>"
            f"<td style='padding:3px 8px;text-align:center'>{item['last_week']}</td>"
            f"<td style='padding:3px 8px;text-align:center;color:{color};font-weight:600'>{indicator}</td>"
            f"</tr>"
        )

    header = (
        "<table style='width:100%;border-collapse:collapse;font-size:0.87em'>"
        "<thead><tr style='border-bottom:1px solid #444'>"
        "<th style='padding:3px 8px;text-align:left'>카테고리</th>"
        "<th style='padding:3px 8px'>전체</th>"
        "<th style='padding:3px 8px'>이번주</th>"
        "<th style='padding:3px 8px'>전주</th>"
        "<th style='padding:3px 8px'>변화</th>"
        "</tr></thead><tbody>"
    )
    st.markdown(header + "".join(rows) + "</tbody></table>", unsafe_allow_html=True)


def _render_data_quality(dq: dict) -> None:
    """데이터 품질 미니 게이지."""
    if not dq or dq.get("total", 0) == 0:
        return
    cols = st.columns(3)
    cols[0].metric("메타 추출", f"{dq['pct_meta']}%", help="메타데이터 추출 완료 비율")
    cols[1].metric("기술스택 보유", f"{dq['pct_tech']}%", help="기술스택 정보가 있는 문서 비율")
    cols[2].metric("카테고리 분류", f"{dq['pct_cat']}%", help="카테고리가 지정된 문서 비율")


# ── 메인 렌더 ─────────────────────────────────────────────────────────

def render_dashboard(config: AppConfig, on_sync_click=None) -> None:
    st.subheader("현황 대시보드")

    stats: DashboardStats = _load_stats(config.db_path)

    has_metadata = bool(stats.top_tech_stacks or stats.top_effects or stats.top_keywords)

    # ── 메트릭 카드 ───────────────────────────────────────────────────
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("전체 에이전트", stats.total_documents)

    week_delta = stats.week_new - stats.prev_week_new
    c2.metric("이번주 신규", stats.week_new,
              delta=week_delta if stats.prev_week_new > 0 else None,
              help=f"전주 신규: {stats.prev_week_new}건")

    c3.metric("이번달 신규", stats.month_new)
    c4.metric("이번달 수정", stats.month_updated)

    dq = stats.data_quality
    if dq and dq.get("total", 0) > 0:
        c5.metric("메타 완성도", f"{dq['pct_meta']}%",
                  help=f"기술스택 {dq['pct_tech']}% | 카테고리 {dq['pct_cat']}%")
    else:
        c5.metric("작성자 수", len(stats.top_authors))

    if stats.last_sync_at:
        c6.metric("마지막 동기화", stats.last_sync_at[:16].replace("T", " "))
    else:
        c6.metric("마지막 동기화", "-")

    # 현행화 버튼 + 안내
    col_desc, col_btn = st.columns([5, 1])
    with col_desc:
        st.caption(
            "**현행화하기**: Confluence 변경분만 동기화합니다.  \n"
            "청킹 방식·임베딩 모델을 변경한 경우 벡터 인덱스 전체 재구축이 필요합니다."
        )
        if st.button("⚙️ 설정 → 전체 재색인으로 이동", key="go_rebuild"):
            st.session_state["_settings_highlight_rebuild"] = True
            import time as _time
            import streamlit.components.v1 as _comp
            _comp.html(
                f"""<script>
                // ts={_time.time()}
                (() => {{
                    const tabs = window.parent.document
                        .querySelectorAll('[data-baseweb="tab"]');
                    for (const t of tabs) {{
                        if (t.innerText.includes('⚙') ||
                            t.innerText.includes('설정')) {{
                            t.click(); break;
                        }}
                    }}
                }})();
                </script>""",
                height=0,
            )
    with col_btn:
        # 자동/수동 현행화 진행 중이면 버튼 비활성화
        _sync_busy = (
            st.session_state.get("is_sync_running", False)
            or st.session_state.get("is_manual_sync_running", False)
        )
        _btn_label = "현행화 중..." if _sync_busy else "현행화하기 ▶"
        if st.button(_btn_label, type="primary", width='stretch', disabled=_sync_busy):
            if on_sync_click:
                on_sync_click()

    st.divider()

    # ── ⭐ 대표 에이전트 Top 3 ─────────────────────────────────────────
    if stats.top_agents:
        st.markdown("**⭐ 추천 에이전트**")
        st.caption("메타데이터 풍부도 + 최근 등록 기준 상위 3건")
        agent_cols = st.columns(len(stats.top_agents))
        for col, agent in zip(agent_cols, stats.top_agents):
            with col:
                _render_agent_card(agent)
        st.divider()

    # ── 카테고리 분포 + 기술스택 ──────────────────────────────────────
    if has_metadata or stats.top_categories:
        col_cat, col_tech = st.columns([3, 2])

        with col_cat:
            st.markdown("**카테고리 분포**")
            if stats.top_categories:
                st.altair_chart(
                    _hbar(stats.top_categories, "카테고리"),
                    width='stretch',
                )
            else:
                st.caption("데이터 없음")

            if stats.category_trend:
                st.markdown("**전주 대비 변화**")
                _render_category_trend(stats.category_trend)

        with col_tech:
            st.markdown("**기술스택 Top 5**")
            if stats.top_tech_stacks:
                st.altair_chart(
                    _hbar(stats.top_tech_stacks[:5], "기술스택"),
                    width='stretch',
                )
            else:
                st.caption("데이터 없음")

            st.markdown("**기대효과 Top 5**")
            if stats.top_effects:
                st.altair_chart(
                    _hbar(stats.top_effects[:5], "기대효과"),
                    width='stretch',
                )
            else:
                st.caption("데이터 없음")

        st.divider()

    # ── 데이터 품질 ───────────────────────────────────────────────────
    if dq and dq.get("total", 0) > 0:
        st.markdown("**데이터 품질 지표**")
        _render_data_quality(dq)
        st.divider()

    # ── 키워드 + 월별 추이 ─────────────────────────────────────────────
    col_kw, col_trend = st.columns([2, 3])

    with col_kw:
        st.markdown("**주요 키워드 Top 10**")
        if stats.top_keywords:
            st.altair_chart(
                _hbar(stats.top_keywords[:10], "키워드"),
                width='stretch',
            )
        else:
            st.caption("데이터 없음")

    with col_trend:
        st.markdown("**월별 에이전트 등록 추이**")
        if stats.monthly_trend:
            st.altair_chart(_line(stats.monthly_trend, height=240), width='stretch')
        else:
            st.caption("데이터 없음")

    if not has_metadata and not stats.top_categories:
        if not config.is_llm_configured:
            st.warning(
                "**기술스택 · 기대효과 · 키워드 · 카테고리** 차트를 표시하려면 LLM 설정이 필요합니다.  \n"
                "👉 상단 **⚙️ 설정** 탭 → **LLM** 탭에서 설정 후 현행화하세요."
            )

    st.divider()

    # ── 최근 등록 에이전트 ────────────────────────────────────────────
    st.markdown("**최근 등록 에이전트**")
    if stats.recent_documents:
        for doc in stats.recent_documents:
            name = doc.get("agent_name") or doc.get("title", "")
            url = doc.get("url", "")
            author = doc.get("author", "")
            created = (doc.get("created_at") or "")[:10]

            col_name, col_meta = st.columns([4, 2])
            with col_name:
                st.markdown(f"🔗 [{name}]({url})" if url else f"📄 {name}")
            with col_meta:
                parts = [f"✍️ {author}" if author else "", f"📅 {created}" if created else ""]
                st.caption("  ".join(p for p in parts if p))
    else:
        st.caption("등록된 에이전트가 없습니다.")

    st.divider()

    # ── 동기화 이력 ───────────────────────────────────────────────────
    st.markdown("**동기화 이력**")
    from app.infrastructure.db.sync_history_repository import SyncHistoryRepository
    history = SyncHistoryRepository(config.db_path).get_recent(100)
    if history:
        PAGE_SIZE = 10
        total_pages = max(1, (len(history) + PAGE_SIZE - 1) // PAGE_SIZE)
        if "sync_hist_page" not in st.session_state:
            st.session_state["sync_hist_page"] = 1
        page = st.session_state["sync_hist_page"]

        st.caption(f"전체 {len(history)}건")

        page_data = history[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        df = pd.DataFrame(page_data)[
            ["sync_type", "started_at", "status", "new_count", "updated_count", "deleted_count"]
        ]
        df.columns = ["유형", "시작", "상태", "신규", "수정", "삭제"]
        df["시작"] = df["시작"].str[:16].str.replace("T", " ", regex=False)
        _status_map = {"success": "완료", "failed": "실패", "running": "진행중"}
        df["상태"] = df["상태"].map(lambda s: _status_map.get(s, s))
        st.dataframe(df, width='stretch', hide_index=True)

        # 페이징 — 테이블 아래 중앙 정렬
        from app.ui._helpers import render_pager
        prev_clicked, next_clicked = render_pager(page, total_pages, "sync_hist")
        if prev_clicked:
            st.session_state["sync_hist_page"] = page - 1
            st.rerun()
        if next_clicked:
            st.session_state["sync_hist_page"] = page + 1
            st.rerun()
    else:
        st.caption("동기화 기록이 없습니다.")
