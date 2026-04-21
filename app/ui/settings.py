"""
설정 탭 UI.

섹션 구성:
  1. 자격증명 현황 배너  — 현재 저장 상태 한눈에 확인
  2. Confluence 설정    — URL/타입/인증방식 (일반 설정)
  3. Confluence PAT    — 자격증명 전용 폼 (SQLite 저장)
  4. LLM 설정          — Provider/모델/엔드포인트/메타데이터 추출 (일반 설정)
  5. LLM API Key       — 자격증명 전용 폼 (SQLite 저장)
  6. 연결 테스트
  7. 고급 기능          — 데이터 정비 (메타 재추출 + 재색인), DB 경로 확인
"""
import time
import streamlit as st
from concurrent.futures import ThreadPoolExecutor

from app.shared.config import AppConfig, save_config_to_json, save_config_to_db
from app.infrastructure.db.settings_repository import SettingsRepository

# ── 데이터 정비 비동기 실행 (UI 스레드 블로킹 방지) ──────────────────────
_advanced_executor = ThreadPoolExecutor(max_workers=1)
_ADVANCED_TIMEOUT_SEC = 1800  # 30분


def _advanced_job(config: AppConfig, mode: str = "full") -> dict:
    """
    백그라운드 스레드 실행 — st.* 일절 사용 금지.
    mode:
      "full"        — 미추출 재추출 + 전체 재색인 (기존 전체 정비)
      "fallback"    — one_line_summary/tech_stack 빈 문서만 재추출 (재색인 없음)
      "new_changed" — 신규/수정 문서만 재추출 (재색인 없음)
    """
    results: dict = {}
    errors: list[str] = []

    try:
        from app.application.sync_service import SyncService
        svc = SyncService.from_config(config)
    except Exception as e:
        return {"init_error": str(e)}

    if mode in ("full", "fallback"):
        try:
            r1 = svc.reextract_metadata()
            results["meta"] = r1
        except Exception as e:
            errors.append(f"메타 재추출 실패: {e}")
    elif mode == "new_changed":
        try:
            r1 = svc.reextract_new_or_changed()
            results["meta"] = r1
        except Exception as e:
            errors.append(f"신규/수정 재추출 실패: {e}")

    if mode == "full":
        try:
            r2 = svc.rebuild_index()
            results["index"] = r2
            try:
                from app.ui.search import reset_search_service
                reset_search_service()
            except Exception:
                pass
        except Exception as e:
            errors.append(f"재색인 실패: {e}")

    results["errors"] = errors
    results["mode"] = mode
    return results


@st.fragment(run_every=2)
def _advanced_section_fragment(config: AppConfig) -> None:
    """
    버튼 + 진행 바를 하나의 fragment로 통합.
    - 버튼 클릭 → widget interaction → fragment 범위 rerun → 탭 이동 없음
    - run_every=2 → 2초 polling으로 완료 감지
    - 완료 시 전체 캐시 클리어 + 전체 rerun (fresh data)
    """
    from app.ui._helpers import render_progress_bar

    is_advanced = st.session_state.get("is_advanced_running", False)
    is_sync_busy = (
        st.session_state.get("is_sync_running", False)
        or st.session_state.get("is_manual_sync_running", False)
    )

    # ── 버튼 영역 ─────────────────────────────────────────────────────
    col_btn, col_info = st.columns([2, 3])
    with col_info:
        try:
            from app.infrastructure.db.connection import db_session as _dbs
            with _dbs(config.db_path) as _conn:
                fallback_cnt = _conn.execute(
                    "SELECT COUNT(*) FROM document_metadata "
                    "WHERE COALESCE(category,'') != '추출불가' "
                    "AND ((tech_stack_json IN ('[]','','null') OR tech_stack_json IS NULL) "
                    "OR (problem IS NULL OR problem = '') "
                    "OR (category IS NULL OR category = '기타'))"
                ).fetchone()[0]
                blocked_cnt = _conn.execute(
                    "SELECT COUNT(*) FROM document_metadata WHERE category = '추출불가'"
                ).fetchone()[0]
                total_cnt = _conn.execute(
                    "SELECT COUNT(*) FROM documents WHERE is_deleted = 0"
                ).fetchone()[0]
            st.session_state["_advanced_total_docs"] = total_cnt
            msgs = []
            if fallback_cnt > 0:
                msgs.append(f"⚠️ 메타 미추출 **{fallback_cnt}건**")
            if blocked_cnt > 0:
                msgs.append(f"🚫 추출불가(LLM 거부, 정비 시 재시도) **{blocked_cnt}건**")
            if msgs:
                st.warning(" · ".join(msgs) + f"  /  전체 {total_cnt}건")
            else:
                st.success(f"✅ 전체 {total_cnt}건 메타데이터 정상")
        except Exception:
            pass

    with col_btn:
        btn_disabled = not config.is_llm_configured or is_advanced or is_sync_busy

        def _start_job(mode: str) -> None:
            if is_sync_busy:
                st.warning("현행화가 진행 중입니다. 완료 후 데이터 정비를 실행하세요.")
                return
            if not config.is_llm_configured:
                st.error("LLM 설정을 먼저 완료해주세요. (설정 탭 → LLM)")
                return
            future = _advanced_executor.submit(_advanced_job, config, mode)
            st.session_state["is_advanced_running"] = True
            st.session_state["ss_advanced_start_time"] = time.monotonic()
            st.session_state["_advanced_future"] = future
            st.session_state["_advanced_mode"] = mode
            st.session_state.pop("_advanced_result", None)
            st.session_state.pop("_advanced_error", None)

        if is_advanced:
            st.button("진행 중...", type="primary", width="stretch", disabled=True)
        else:
            if st.button(
                "전체 정비 (재추출 + 재색인)", type="primary", width="stretch", disabled=btn_disabled,
                help="품질이 낮거나 미추출된 문서를 LLM으로 재분석하고, 전체 벡터 인덱스를 재구축합니다. 임베딩 모델을 교체했거나 인덱스가 손상된 경우 사용하세요.",
            ):
                _start_job("full")
            if st.button(
                "미추출건만 정비", width="stretch", disabled=btn_disabled,
                help="기술스택·카테고리·문제 설명 등 품질이 낮은 문서만 LLM으로 다시 분석합니다. 벡터 재색인 없이 메타데이터만 보완하므로 빠릅니다.",
            ):
                _start_job("fallback")
            if st.button(
                "신규·수정건만 정비", width="stretch", disabled=btn_disabled,
                help="Confluence에서 새로 추가되거나 수정된 문서, 추출에 실패한 문서의 메타데이터만 LLM으로 추출합니다. 정기적인 보완 작업에 적합합니다.",
            ):
                _start_job("new_changed")

    if not is_advanced:
        return

    # ── 진행 중 처리 ──────────────────────────────────────────────────
    future = st.session_state.get("_advanced_future")
    if future is None:
        st.session_state["is_advanced_running"] = False
        st.rerun()
        return

    elapsed = time.monotonic() - st.session_state.get("ss_advanced_start_time", 0)

    # ── 완료 감지 ─────────────────────────────────────────────────────
    if future.done():
        mode = st.session_state.get("_advanced_mode", "full")
        mode_labels = {"full": "전체 정비", "fallback": "미추출건 정비", "new_changed": "신규·수정건 정비"}
        render_progress_bar(100, f"{mode_labels.get(mode, '데이터 정비')} 완료", done=True, color="green")
        time.sleep(0.8)
        try:
            result = future.result()
            st.session_state["_advanced_result"] = result
            st.session_state.pop("_advanced_error", None)
        except Exception as e:
            st.session_state["_advanced_error"] = str(e)
            st.session_state.pop("_advanced_result", None)
        finally:
            st.session_state["is_advanced_running"] = False
            st.session_state["_advanced_future"] = None
        # 전체 캐시 클리어 → 최신 데이터로 재로딩
        try:
            from app.ui.dashboard import _load_stats
            _load_stats.clear()
        except Exception:
            pass
        try:
            from app.ui.search import reset_search_service
            reset_search_service()
        except Exception:
            pass
        try:
            from app.ui.report import _build_period_options
            _build_period_options.clear()
        except Exception:
            pass
        st.rerun()  # 전체 rerun — 최신 데이터로 전체 페이지 갱신
        return

    # ── 타임아웃 ──────────────────────────────────────────────────────
    if elapsed > _ADVANCED_TIMEOUT_SEC:
        future.cancel()
        st.session_state["is_advanced_running"] = False
        st.session_state["_advanced_future"] = None
        st.session_state["_advanced_error"] = (
            f"데이터 정비가 {_ADVANCED_TIMEOUT_SEC // 60}분을 초과하여 중단되었습니다."
        )
        st.rerun()
        return

    # ── 진행률 바 + 예상 소요시간 ────────────────────────────────────
    pct = min(88, int(elapsed / _ADVANCED_TIMEOUT_SEC * 100))

    total_docs = st.session_state.get("_advanced_total_docs", 0)
    if total_docs > 0:
        # 문서당 평균 처리 시간 기반 예상 (LLM 추출 ~3s/doc + 재색인)
        estimated_total = total_docs * 4
        remaining_sec = max(0, estimated_total - elapsed)
    elif pct > 2:
        # elapsed/pct 비율로 역산
        estimated_total = elapsed / (pct / 100)
        remaining_sec = max(0, estimated_total - elapsed)
    else:
        remaining_sec = None

    if remaining_sec is not None:
        if remaining_sec >= 60:
            remain_str = f"약 {int(remaining_sec // 60)}분 {int(remaining_sec % 60)}초 남음"
        else:
            remain_str = f"약 {int(remaining_sec)}초 남음"
        sublabel = f"{int(elapsed)}초 경과 · {remain_str} · 탭 이동 중에도 계속 진행됩니다"
    else:
        sublabel = f"{int(elapsed)}초 경과 · 탭 이동 중에도 계속 진행됩니다"

    mode = st.session_state.get("_advanced_mode", "full")
    mode_titles = {
        "full": "데이터 정비 진행 중 (① 메타 재추출 → ② 벡터 재구축)",
        "fallback": "미추출건 정비 진행 중 (메타 재추출)",
        "new_changed": "신규·수정건 정비 진행 중 (메타 재추출)",
    }
    render_progress_bar(pct, mode_titles.get(mode, "데이터 정비 진행 중"), sublabel=sublabel, color="purple")


# ── 헬퍼 ─────────────────────────────────────────────────────────────

def _mask(value: str, show: int = 4) -> str:
    """자격증명 마스킹 표시. 빈 값이면 '미설정' 반환."""
    if not value:
        return "❌ 미설정"
    if len(value) <= show:
        return "✅ " + "*" * len(value)
    return f"✅ ****{value[-show:]}"


def _safe_strip(value) -> str:
    return value.strip() if isinstance(value, str) else value


def _save_general(config: AppConfig, updates: dict) -> None:
    """일반 설정 → config에 반영 후 JSON + DB 저장."""
    for k, v in updates.items():
        setattr(config, k, v)
    save_config_to_json(config)
    save_config_to_db(config)


def _save_credentials(config: AppConfig, creds: dict) -> None:
    """자격증명 → config에 반영 후 키체인에 저장 + 서비스 캐시 무효화."""
    for k, v in creds.items():
        setattr(config, k, v)
    repo = SettingsRepository(config.db_path)
    repo.save_credentials(creds)
    from app.infrastructure.service_factory import invalidate as _inv
    _inv()


# ── 메인 렌더 ─────────────────────────────────────────────────────────

def render_settings(config: AppConfig) -> None:
    st.subheader("설정")

    _render_credential_status(config)
    st.divider()

    # 대시보드 버튼 → 설정 탭 진입 시 "고급" 서브탭 자동 클릭
    if st.session_state.pop("_settings_highlight_rebuild", False):
        import streamlit.components.v1 as _comp
        _comp.html(
            """<script>
            setTimeout(() => {
                const tabs = window.parent.document
                    .querySelectorAll('[data-baseweb="tab"]');
                for (const t of tabs) {
                    if (t.innerText.trim() === '고급') {
                        t.click(); break;
                    }
                }
            }, 200);
            </script>""",
            height=0,
        )

    tab_confluence, tab_llm, tab_test, tab_advanced = st.tabs([
        "Confluence", "LLM", "연결 테스트", "고급"
    ])

    with tab_confluence:
        _render_confluence_section(config)

    with tab_llm:
        _render_llm_section(config)

    with tab_test:
        _render_connection_test(config)

    with tab_advanced:
        _render_advanced(config)


# ── 섹션 렌더 ─────────────────────────────────────────────────────────

def _render_credential_status(config: AppConfig) -> None:
    """자격증명 현황 배너."""
    st.markdown("##### 자격증명 현황")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Confluence PAT", _mask(config.auth_token))
    with c2:
        if config.llm_provider == "openai":
            st.metric("OpenAI API Key", _mask(config.llm_api_key))
        else:
            st.metric("InHouse API Key", _mask(config.inhouse_llm_api_key))
    with c3:
        provider_label = "OpenAI" if config.llm_provider == "openai" else "InHouse"
        configured = "✅ 설정됨" if config.is_llm_configured else "❌ 미설정"
        st.metric(f"LLM ({provider_label})", configured)


def _render_confluence_section(config: AppConfig) -> None:
    st.markdown("#### Confluence 일반 설정")

    with st.form("form_confluence_general"):
        base_url = st.text_input(
            "Base URL",
            value=config.confluence_base_url,
            placeholder="https://confl.sinc.co.kr",
        )
        root_page_id = st.text_input(
            "Root Page ID",
            value=config.root_page_id,
            placeholder="495526926",
            help="Confluence 페이지 URL의 pageId= 값",
        )
        col1, col2 = st.columns(2)
        with col1:
            confluence_type = st.selectbox(
                "Confluence 타입",
                options=["server", "cloud"],
                index=0 if config.confluence_type == "server" else 1,
                help="온프레미스/DC → server | atlassian.net → cloud",
            )
        with col2:
            auth_type = st.selectbox(
                "인증 방식",
                options=["token", "basic"],
                index=0 if config.auth_type == "token" else 1,
                help="PAT → token | ID+PW → basic",
            )
        auth_username = st.text_input(
            "사용자명 / 이메일",
            value=config.auth_username,
            help="Basic 인증 시 입력. PAT 사용 시 빈칸.",
        )
        submitted = st.form_submit_button("저장", type="primary")

    if submitted:
        _save_general(config, {
            "confluence_base_url": base_url.strip(),
            "root_page_id": root_page_id.strip(),
            "confluence_type": confluence_type,
            "auth_type": auth_type,
            "auth_username": auth_username.strip(),
        })
        st.success("Confluence 설정 저장 완료")
        st.rerun()

    st.divider()
    st.markdown("#### Confluence 자격증명")
    st.caption("PAT는 SQLite에만 저장됩니다. config.json에는 기록되지 않습니다.")

    _render_pat_credential(config)


def _render_pat_credential(config: AppConfig) -> None:
    """PAT 등록/수정 폼."""
    current = _mask(config.auth_token)
    st.info(f"현재 저장된 PAT: **{current}**")

    with st.form("form_pat"):
        new_pat = st.text_input(
            "새 PAT 입력",
            type="password",
            placeholder="PAT를 입력하세요 (비워두면 변경 안 됨)",
        )
        col_save, col_clear = st.columns([3, 1])
        with col_save:
            pat_saved = st.form_submit_button("PAT 저장", type="primary")
        with col_clear:
            pat_cleared = st.form_submit_button("삭제", type="secondary")

    if pat_saved:
        if not new_pat.strip():
            st.warning("PAT를 입력하세요.")
        else:
            _save_credentials(config, {"auth_token": new_pat.strip()})
            st.success("PAT가 SQLite에 저장되었습니다.")
            st.rerun()

    if pat_cleared:
        _save_credentials(config, {"auth_token": ""})
        st.warning("PAT가 삭제되었습니다.")
        st.rerun()



def _render_llm_section(config: AppConfig) -> None:
    st.markdown("#### LLM 일반 설정")

    with st.form("form_llm_general"):
        col_e1, col_e2 = st.columns(2)
        embed_options = ["openai", "local"]
        with col_e1:
            embedding_provider = st.selectbox(
                "임베딩 Provider",
                options=embed_options,
                index=embed_options.index(config.embedding_provider)
                if config.embedding_provider in embed_options else 0,
                help="local: 오프라인/EXE 배포용 (API Key 불필요)",
            )
        with col_e2:
            if embedding_provider == "local":
                embedding_model = st.text_input(
                    "로컬 모델명",
                    value=config.local_model_name or "paraphrase-multilingual-mpnet-base-v2",
                    help="sentence-transformers 모델 ID",
                )
            else:
                embedding_model = st.text_input(
                    "임베딩 모델",
                    value=config.embedding_model or "text-embedding-3-small",
                )

        llm_provider_options = ["openai", "inhouse"]
        llm_provider = st.selectbox(
            "LLM Provider",
            options=llm_provider_options,
            index=llm_provider_options.index(config.llm_provider)
            if config.llm_provider in llm_provider_options else 0,
        )

        if llm_provider == "openai":
            llm_model = st.text_input(
                "모델명", value=config.llm_model or "gpt-4o-mini"
            )
            inhouse_llm_url = config.inhouse_llm_url
            inhouse_llm_usecase_id = config.inhouse_llm_usecase_id
            inhouse_llm_project_id = config.inhouse_llm_project_id
            inhouse_llm_agent_code = config.inhouse_llm_agent_code
            inhouse_llm_timeout = config.inhouse_llm_timeout
        else:
            st.caption("사내 DevX MCP API 설정")
            inhouse_llm_url = st.text_input(
                "InHouse LLM URL",
                value=config.inhouse_llm_url,
            )
            col_u1, col_u2 = st.columns(2)
            with col_u1:
                inhouse_llm_usecase_id = st.text_input(
                    "Usecase ID (UUID)",
                    value=config.inhouse_llm_usecase_id,
                    placeholder="b6958377-...",
                )
            with col_u2:
                inhouse_llm_project_id = st.text_input(
                    "Project ID (UUID)",
                    value=config.inhouse_llm_project_id,
                    placeholder="eb01fb40-...",
                )
            col_a1, col_a2 = st.columns(2)
            with col_a1:
                inhouse_llm_agent_code = st.text_input(
                    "Agent Code",
                    value=config.inhouse_llm_agent_code or "playground",
                )
            with col_a2:
                inhouse_llm_timeout = st.number_input(
                    "Timeout (초)", min_value=10, max_value=600,
                    value=config.inhouse_llm_timeout or 120,
                )
            llm_model = config.llm_model

        extract_metadata = st.checkbox(
            "동기화 시 LLM 메타데이터 자동 추출",
            value=config.extract_metadata,
            help="동기화마다 LLM으로 기술스택·효과·키워드를 자동 추출합니다. API Key 필요.",
        )

        submitted = st.form_submit_button("저장", type="primary")

    if submitted:
        embed_updates: dict = {
            "embedding_provider": embedding_provider,
            "extract_metadata": extract_metadata,
        }
        if embedding_provider == "local":
            embed_updates["local_model_name"] = embedding_model.strip()
        else:
            embed_updates["embedding_model"] = embedding_model.strip()
        _save_general(config, {
            **embed_updates,
            "llm_provider": llm_provider,
            "llm_model": _safe_strip(llm_model),
            "inhouse_llm_url": _safe_strip(inhouse_llm_url),
            "inhouse_llm_usecase_id": _safe_strip(inhouse_llm_usecase_id),
            "inhouse_llm_project_id": _safe_strip(inhouse_llm_project_id),
            "inhouse_llm_agent_code": _safe_strip(inhouse_llm_agent_code),
            "inhouse_llm_timeout": int(inhouse_llm_timeout),
        })
        from app.infrastructure.service_factory import invalidate as _invalidate_services
        _invalidate_services()
        st.session_state["config_version"] = st.session_state.get("config_version", 0) + 1
        st.success("LLM 설정 저장 완료 — 서비스 캐시 초기화됨")
        st.rerun()

    st.divider()
    st.markdown("#### LLM API Key")
    st.caption("API Key는 SQLite에만 저장됩니다. config.json에는 기록되지 않습니다.")

    if config.llm_provider == "openai":
        _render_openai_key_credential(config)
    else:
        _render_inhouse_key_credential(config)


def _render_openai_key_credential(config: AppConfig) -> None:
    current = _mask(config.llm_api_key)
    st.info(f"현재 저장된 OpenAI API Key: **{current}**")

    with st.form("form_openai_key"):
        new_key = st.text_input(
            "새 OpenAI API Key",
            type="password",
            placeholder="sk-proj-... (비워두면 변경 안 됨)",
        )
        col_save, col_clear = st.columns([3, 1])
        with col_save:
            key_saved = st.form_submit_button("저장", type="primary")
        with col_clear:
            key_cleared = st.form_submit_button("삭제", type="secondary")

    if key_saved:
        if not new_key.strip():
            st.warning("API Key를 입력하세요.")
        else:
            _save_credentials(config, {"llm_api_key": new_key.strip()})
            st.success("OpenAI API Key가 저장되었습니다.")
            st.rerun()

    if key_cleared:
        _save_credentials(config, {"llm_api_key": ""})
        st.warning("OpenAI API Key가 삭제되었습니다.")
        st.rerun()


def _render_inhouse_key_credential(config: AppConfig) -> None:
    current = _mask(config.inhouse_llm_api_key)
    st.info(f"현재 저장된 InHouse API Key: **{current}**")
    st.caption("InHouse API Key가 없으면 빈칸으로 두세요 (시스템 키 없이 동작).")

    with st.form("form_inhouse_key"):
        new_key = st.text_input(
            "새 InHouse API Key",
            type="password",
            placeholder="비워두면 변경 안 됨",
        )
        col_save, col_clear = st.columns([3, 1])
        with col_save:
            key_saved = st.form_submit_button("저장", type="primary")
        with col_clear:
            key_cleared = st.form_submit_button("삭제", type="secondary")

    if key_saved:
        val = new_key.strip()
        if not val:
            st.warning("API Key를 입력하세요.")
        else:
            _save_credentials(config, {"inhouse_llm_api_key": val})
            st.success("InHouse API Key가 저장되었습니다.")
            st.rerun()

    if key_cleared:
        _save_credentials(config, {"inhouse_llm_api_key": ""})
        st.warning("InHouse API Key가 삭제되었습니다.")
        st.rerun()



def _render_connection_test(config: AppConfig) -> None:
    st.markdown("#### 연결 테스트")

    col_cf, col_llm = st.columns(2)

    with col_cf:
        st.markdown("**Confluence**")
        if st.button("연결 테스트", key="btn_cf_test"):
            if not config.is_confluence_configured:
                st.warning("Confluence URL / Root Page ID / PAT를 먼저 설정해주세요.")
            else:
                with st.spinner("연결 중..."):
                    try:
                        from app.infrastructure.confluence.client import ConfluenceClient
                        client = ConfluenceClient(
                            base_url=config.confluence_base_url,
                            auth_token=config.auth_token,
                            auth_username=config.auth_username,
                            confluence_type=config.confluence_type,
                        )
                        try:
                            ok = client.test_connection()
                        finally:
                            client.close()
                        if ok:
                            st.success("Confluence 연결 성공!")
                        else:
                            st.error("연결 실패. PAT와 URL을 확인해주세요.")
                    except Exception as e:
                        st.error(f"오류: {e}")

    with col_llm:
        st.markdown("**LLM**")
        if st.button("연결 테스트", key="btn_llm_test"):
            if not config.is_llm_configured:
                st.warning("LLM 설정을 먼저 완료해주세요.")
            elif config.llm_provider == "inhouse":
                with st.spinner("InHouse LLM 확인 중..."):
                    try:
                        from app.infrastructure.llm.inhouse_provider import InHouseLLMProvider
                        p = InHouseLLMProvider(
                            url=config.inhouse_llm_url,
                            api_key=config.inhouse_llm_api_key,
                            agent_code=config.inhouse_llm_agent_code,
                            usecase_id=config.inhouse_llm_usecase_id,
                            project_id=config.inhouse_llm_project_id,
                            timeout=config.inhouse_llm_timeout,
                        )
                        st.caption(f"접속 URL: `{config.inhouse_llm_url}`")
                        ok, msg = p.health_check()
                        if ok:
                            st.success(f"✅ {msg}")
                        else:
                            st.error(f"❌ {msg}")
                    except Exception as e:
                        st.error(f"오류: {e}")
            else:
                with st.spinner("OpenAI API 확인 중..."):
                    try:
                        from openai import OpenAI
                        OpenAI(api_key=config.llm_api_key).models.list()
                        st.success("OpenAI API Key 유효!")
                    except Exception as e:
                        st.error(f"오류: {e}")


def _render_advanced(config: AppConfig) -> None:
    st.markdown("#### 데이터 정비")
    st.info(
        "**언제 실행하나요?**\n\n"
        "- 처음 동기화 당시 LLM이 미설정이어서 기술스택·요약·카테고리가 비어 있는 경우\n"
        "- 임베딩 모델을 교체하거나 벡터 인덱스가 손상된 경우\n"
        "- 대시보드 차트에 데이터가 표시되지 않는 경우\n\n"
        "**실행 순서**: ① 미추출 문서 LLM 재분석 → ② 전체 벡터 인덱스 재구축  \n"
        "문서 수에 따라 수 분~수십 분 소요될 수 있습니다. **탭 이동 중에도 백그라운드로 계속 진행됩니다.**"
    )

    # 버튼 + 진행 바를 하나의 fragment로 통합 → 버튼 클릭 시 탭 이동 없음
    _advanced_section_fragment(config)

    # ── 완료/에러 결과 표시 ──────────────────────────────────────────
    if err := st.session_state.get("_advanced_error"):
        st.error(f"데이터 정비 실패: {err}")
    elif result := st.session_state.get("_advanced_result"):
        if init_err := result.get("init_error"):
            st.error(f"서비스 초기화 실패: {init_err}")
        else:
            errors = result.get("errors", [])
            if errors:
                st.error("\n".join(errors))
            else:
                parts = []
                mode = result.get("mode", "full")
                mode_labels = {"full": "전체 정비", "fallback": "미추출건 정비", "new_changed": "신규·수정건 정비"}
                parts = []
                if meta := result.get("meta"):
                    parts.append(f"메타 재추출: 성공 {meta.get('done', 0)}건 / 실패 {meta.get('failed', 0)}건")
                if index := result.get("index"):
                    parts.append(f"벡터 재색인: {index.get('reindexed', 0)}건")
                label = mode_labels.get(mode, "데이터 정비")
                st.success(f"{label} 완료! " + " | ".join(parts))

    st.divider()
    st.markdown("#### DB 경로 정보")
    st.code(f"SQLite: {config.db_path}\nChroma: {config.chroma_path}")
