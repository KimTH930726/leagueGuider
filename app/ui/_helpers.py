import streamlit as st

# 프로그레스 바 그라디언트 프리셋
_GRAD = {
    "blue":   ("linear-gradient(90deg,#1a3a6b,#1565c0)", "linear-gradient(90deg,#1565c0,#42a5f5)"),
    "orange": ("linear-gradient(90deg,#7a3a00,#e65100)", "linear-gradient(90deg,#e65100,#ff9800)"),
    "green":  ("linear-gradient(90deg,#1a4a1a,#2e7d32)", "linear-gradient(90deg,#2e7d32,#66bb6a)"),
    "purple": ("linear-gradient(90deg,#4a0072,#7b1fa2)", "linear-gradient(90deg,#7b1fa2,#9c27b0)"),
}


def render_progress_bar(
    pct: int,
    label: str,
    sublabel: str = "",
    color: str = "blue",
    done: bool = False,
) -> None:
    """
    상단 라벨 + 하단 진행 바를 HTML로 렌더링.
    color: "blue" | "orange" | "green"
    done=True 이면 완료 스타일(100%) 적용.
    """
    header_grad, bar_grad = _GRAD.get(color, _GRAD["blue"])
    bar_w = 100 if done else pct
    icon = "✅" if done else "🔄"
    pct_label = "100%" if done else f"~{pct}%"

    st.markdown(
        f"""
        <div style="margin-bottom:6px">
          <div style="
            background:{header_grad};
            color:#fff;padding:6px 14px 4px;border-radius:6px 6px 0 0;
            font-size:0.82rem;font-weight:600;display:flex;
            justify-content:space-between;align-items:center
          ">
            <span>{icon} {label}</span>
            <span>{pct_label}</span>
          </div>
          {"" if not sublabel else f'<div style="background:#1a2744;border-radius:0;padding:4px 14px 6px;font-size:0.78rem;color:#9bb">{sublabel[:50]}{"..." if len(sublabel)>50 else ""}</div>'}
          <div style="background:#1e2a40;height:4px;border-radius:0 0 4px 4px;overflow:hidden">
            <div style="width:{bar_w}%;height:100%;
              background:{bar_grad};
              transition:width 1s ease"></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_pager(page: int, total: int, key: str) -> tuple[bool, bool]:
    """
    화면 중앙에 꽉 붙은 페이징 버튼 렌더링.
    Returns (prev_clicked, next_clicked).
    """
    _, center, _ = st.columns([3, 2, 3])
    with center:
        c_prev, c_num, c_next = st.columns([1, 0.8, 1], gap="small")
        with c_prev:
            prev = st.button(
                "← 이전",
                key=f"{key}_prev",
                disabled=page <= 1,
                use_container_width=True,
            )
        with c_num:
            st.markdown(
                f"<div style='text-align:center;padding-top:6px;"
                f"font-size:0.9rem;white-space:nowrap'>{page} / {total}</div>",
                unsafe_allow_html=True,
            )
        with c_next:
            nxt = st.button(
                "다음 →",
                key=f"{key}_next",
                disabled=page >= total,
                use_container_width=True,
            )
    return prev, nxt
