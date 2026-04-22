import re
import time
import threading
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx
from app.shared.config import get_config, overlay_db_settings, AppConfig
from app.shared.logger import get_logger
from app.infrastructure.db.migrations import run_migrations

logger = get_logger()

# ── 자동 현행화 결과 전달 (session_state 대신 global dict) ───────────
_sync_store: dict[str, dict] = {}
_sync_store_lock = threading.Lock()
# 다중 세션 동시 sync 방지 — 프로세스 전체에서 1개 sync만 허용
_global_sync_lock = threading.Lock()

st.set_page_config(
    page_title="AI리그 로컬 탐색기",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── session_state 키 표준 ────────────────────────────────────────────
# is_sync_running        : 자동 현행화 진행 중 (bool)
# is_manual_sync_running : 수동 현행화 진행 중 (bool)
# is_search_running      : 검색 진행 중 (bool)
# is_report_generating   : 리포트 생성 중 (bool)
# config_version         : 설정 저장 횟수 (int) — 서비스 캐시 무효화 트리거
# ss_sync_*              : 현행화 내부 상태
# ────────────────────────────────────────────────────────────────────

_SYNC_TIMEOUT_SEC = 600  # 10분 초과 시 stuck 상태로 간주


def _reset_search_service() -> None:
    """sync 완료/실패/타임아웃 후 검색 서비스 캐시 무효화."""
    try:
        from app.ui.search import reset_search_service
        reset_search_service()
    except Exception:
        pass


@st.cache_resource
def init_app() -> AppConfig:
    config = get_config()
    run_migrations(config.db_path)
    overlay_db_settings(config)
    logger.info("앱 초기화 완료")
    return config


@st.cache_resource
def _warmup_embedder(config: AppConfig) -> None:
    """
    앱 시작 시 임베딩 모델을 백그라운드에서 미리 로드.
    첫 번째 검색의 62초 대기(lazy load) 제거.
    @st.cache_resource로 프로세스당 1회만 실행.
    """
    def _run():
        try:
            from app.infrastructure.service_factory import get_embedder
            embedder = get_embedder(config)
            if embedder is not None:
                embedder.embed_texts(["warmup"])
                logger.info("임베딩 모델 워밍업 완료")
        except Exception as e:
            logger.warning(f"임베딩 모델 워밍업 실패 (무시): {e}")

    threading.Thread(target=_run, daemon=True).start()


def _start_bg_sync(config: AppConfig) -> None:
    """앱 시작 시 1회 백그라운드 증분 현행화. UI 블로킹 없음."""
    if st.session_state.get("_sync_started"):
        return
    st.session_state["_sync_started"] = True

    if not config.is_confluence_configured:
        return

    # 다른 세션이 이미 sync 중이면 조용히 skip (ChromaDB 동시 쓰기 방지)
    if not _global_sync_lock.acquire(blocking=False):
        logger.info("다른 세션의 sync 진행 중 — 자동 현행화 skip")
        return

    ctx = get_script_run_ctx()
    session_id = ctx.session_id if ctx else "default"

    st.session_state["is_sync_running"] = True
    st.session_state["_sync_session_id"] = session_id

    with _sync_store_lock:
        _sync_store[session_id] = {
            "msg": "Confluence 연결 중...",
            "total": 0,
            "done": 0,
            "start_time": time.monotonic(),
            "status": "running",
        }

    def _progress(msg: str) -> None:
        m = re.match(r"\[(\d+)/(\d+)\]", msg)
        with _sync_store_lock:
            if session_id in _sync_store:
                _sync_store[session_id]["msg"] = msg
                if m:
                    _sync_store[session_id]["done"] = int(m.group(1))
                    _sync_store[session_id]["total"] = int(m.group(2))

    def _run():
        try:
            from app.application.sync_service import SyncService
            svc = SyncService.from_config(config)
            result = svc.run_incremental(progress=_progress)
            with _sync_store_lock:
                if session_id in _sync_store:
                    _sync_store[session_id]["status"] = "done"
                    _sync_store[session_id]["result"] = result
            logger.info(
                f"자동 현행화 완료 — 신규 {result['new_count']} / "
                f"수정 {result['updated_count']} / 삭제 {result['deleted_count']}"
            )
        except Exception as e:
            with _sync_store_lock:
                if session_id in _sync_store:
                    _sync_store[session_id]["status"] = "error"
                    _sync_store[session_id]["error"] = str(e)
            logger.warning(f"자동 현행화 실패: {e}")
        finally:
            _global_sync_lock.release()

    threading.Thread(target=_run, daemon=True).start()


@st.fragment(run_every=2)
def _sync_watcher():
    """2초마다 자동 현행화 상태 확인. global store에서 읽어 session_state 반영."""
    if not st.session_state.get("is_sync_running"):
        return

    session_id = st.session_state.get("_sync_session_id", "default")

    with _sync_store_lock:
        state = dict(_sync_store.get(session_id, {}))

    # _sync_store에 항목 없음 = 서버 재시작 등으로 store가 초기화된 고아 상태
    # is_sync_running은 True지만 실제 작업이 없음 → 즉시 해제
    if not state:
        st.session_state["is_sync_running"] = False
        st.session_state.pop("_sync_session_id", None)
        st.rerun()
        return

    status = state.get("status", "running")

    if status == "done":
        result = state.get("result", {})
        n = result.get("new_count", 0)
        u = result.get("updated_count", 0)
        d = result.get("deleted_count", 0)

        # 실제 변경이 있을 때만 100% 바 잠시 표시 — 변경 없으면 즉시 사라짐
        if n or u or d:
            st.markdown(
                """
                <div style="margin-bottom:6px">
                  <div style="
                    background:linear-gradient(90deg,#1a3a6b,#1565c0);
                    color:#fff;padding:6px 14px 4px;border-radius:6px 6px 0 0;
                    font-size:0.82rem;font-weight:600;display:flex;
                    justify-content:space-between;align-items:center
                  ">
                    <span>✅ 현행화 완료</span>
                    <span>100%</span>
                  </div>
                  <div style="background:#2d2d2d;border-radius:0 0 6px 6px;height:7px;overflow:hidden">
                    <div style="width:100%;height:100%;
                      background:linear-gradient(90deg,#1a3a6b,#1565c0);
                      border-radius:6px"></div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            time.sleep(0.8)
            st.toast(f"✅ 현행화 완료: 신규 {n}건 / 수정 {u}건 / 삭제 {d}건")
            _reset_search_service()
        else:
            # 변경 없음 — 토스트만 표시, 바 없이 즉시 종료
            st.toast("✅ 최신 상태입니다 (변경된 문서 없음)")

        with _sync_store_lock:
            _sync_store.pop(session_id, None)
        st.session_state["is_sync_running"] = False
        from app.ui.dashboard import _load_stats
        _load_stats.clear()
        from app.ui.report import _build_period_options
        _build_period_options.clear()
        st.rerun()

    if status == "error":
        with _sync_store_lock:
            _sync_store.pop(session_id, None)
        st.session_state["is_sync_running"] = False
        err_msg = state.get("error", "알 수 없는 오류")
        logger.warning(f"자동 현행화 실패: {err_msg}")
        st.toast(f"⚠️ 자동 현행화 실패: {err_msg[:60]}")
        _reset_search_service()
        st.rerun()

    start_time = state.get("start_time", time.monotonic())
    elapsed = time.monotonic() - start_time
    if elapsed > _SYNC_TIMEOUT_SEC:
        with _sync_store_lock:
            _sync_store.pop(session_id, None)
        st.session_state["is_sync_running"] = False
        logger.warning("자동 현행화 타임아웃 — 상태 강제 초기화")
        st.toast("⚠️ 현행화가 10분을 초과하여 중단되었습니다.")
        _reset_search_service()
        st.rerun()

    msg = state.get("msg", "처리 중...")
    total = state.get("total", 0)
    done = state.get("done", 0)

    if total > 0:
        pct = min(95, int(done / total * 100))
        bar_label = f"{done} / {total}건"
    else:
        # 문서 수를 모르는 초기 단계 — 3초 안에 40%까지 빠르게 채워 "살아있음" 표시
        pct = min(40, int(elapsed / 3 * 40))
        bar_label = "Confluence 응답 대기 중"

    display_msg = msg if len(msg) <= 50 else msg[:47] + "..."

    st.markdown(
        f"""
        <div style="margin-bottom:6px">
          <div style="
            background:linear-gradient(90deg,#1a3a6b,#1565c0);
            color:#fff;padding:6px 14px 4px;border-radius:6px 6px 0 0;
            font-size:0.82rem;font-weight:600;display:flex;
            justify-content:space-between;align-items:center
          ">
            <span>🔄 자동 현행화 진행 중 · {bar_label}</span>
            <span>~{pct}%</span>
          </div>
          <div style="background:#1a2744;border-radius:0 0 6px 6px;
                      padding:4px 14px 6px;font-size:0.78rem;color:#9bb">
            {display_msg}
          </div>
          <div style="background:#1e2a40;height:4px;border-radius:0 0 4px 4px;overflow:hidden">
            <div style="width:{pct}%;height:100%;
              background:linear-gradient(90deg,#1565c0,#42a5f5);
              transition:width 1s ease"></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main():
    config = init_app()
    _start_bg_sync(config)
    _warmup_embedder(config)

    # headless 모드에서 overflow:hidden이 강제 적용되어 줌인 시 스크롤 불가 → 해제
    st.markdown(
        "<style>html, body, [data-testid='stAppViewContainer'],"
        " [data-testid='stMain'] { overflow: auto !important; }</style>",
        unsafe_allow_html=True,
    )

    st.title("🤖 AI리그 로컬 탐색기")
    # sync 진행 중일 때만 watcher fragment 등록 — 완료 후 불필요한 2초 polling 제거
    if st.session_state.get("is_sync_running"):
        _sync_watcher()

    tab_dashboard, tab_search, tab_report, tab_settings = st.tabs(
        ["🏠 대시보드", "🔍 검색", "📊 리포트", "⚙️ 설정"]
    )

    with tab_dashboard:
        from app.ui.dashboard import render_dashboard

        def handle_sync():
            # 중복 실행 방지: 자동/수동 현행화 중이면 차단
            if st.session_state.get("is_sync_running"):
                st.warning("자동 현행화가 진행 중입니다. 잠시 후 다시 시도하세요.")
                return
            if st.session_state.get("is_manual_sync_running"):
                return  # 이미 실행 중 — 조용히 무시

            st.session_state["is_manual_sync_running"] = True
            try:
                if not config.is_confluence_configured:
                    st.error("설정 탭에서 Confluence 연결 정보를 먼저 입력해주세요.")
                    return
                with st.spinner("현행화 중..."):
                    from app.application.sync_service import SyncService
                    svc = SyncService.from_config(config)
                    result = svc.run_incremental(progress=lambda msg: st.toast(msg))
                    st.success(
                        f"완료! 신규 {result['new_count']}건 / "
                        f"수정 {result['updated_count']}건 / "
                        f"삭제 {result['deleted_count']}건"
                    )
                    from app.ui.dashboard import _load_stats
                    _load_stats.clear()
                    from app.ui.report import _build_period_options
                    _build_period_options.clear()
                    if result["new_count"] or result["updated_count"] or result["deleted_count"]:
                        from app.ui.search import reset_search_service
                        reset_search_service()
                    st.rerun()
            except Exception as e:
                st.error(f"동기화 실패: {e}")
            finally:
                st.session_state["is_manual_sync_running"] = False

        render_dashboard(config, on_sync_click=handle_sync)

    with tab_search:
        from app.ui.search import render_search
        render_search(config)

    with tab_report:
        from app.ui.report import render_report
        render_report(config)

    with tab_settings:
        from app.ui.settings import render_settings
        render_settings(config)


if __name__ == "__main__":
    main()
