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

    # ── 상태 표시 ─────────────────────────────────────────────────────
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

    # ── 버튼 + 설명 (버튼 왼쪽 / 설명 오른쪽) ────────────────────────
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
        c1, c2 = st.columns([1, 2])
        with c1:
            if st.button("전체 정비 (재추출 + 재색인)", type="primary", width="stretch", disabled=btn_disabled):
                _start_job("full")
        with c2:
            st.caption("미추출·품질 낮은 문서를 LLM으로 재분석하고 전체 벡터 인덱스를 재구축합니다. 임베딩 모델 교체나 인덱스 손상 시 사용.")

        c3, c4 = st.columns([1, 2])
        with c3:
            if st.button("미추출·추출불가건 정비", width="stretch", disabled=btn_disabled):
                _start_job("fallback")
        with c4:
            st.caption("기술스택·카테고리·요약 등 품질이 낮은 문서만 LLM으로 재분석합니다. 벡터 재색인 없어 빠릅니다.")

        c5, c6 = st.columns([1, 2])
        with c5:
            if st.button("신규·수정건만 정비", width="stretch", disabled=btn_disabled):
                _start_job("new_changed")
        with c6:
            st.caption("Confluence에서 수정되거나 새로 추가된 문서, 추출 실패 문서만 재추출합니다. 정기 보완에 적합합니다.")

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
        mode_labels = {"full": "전체 정비", "fallback": "미추출·추출불가건 정비", "new_changed": "신규·수정건 정비"}
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
        "fallback": "미추출·추출불가건 정비 진행 중",
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
        st.metric("InHouse Client ID", _mask(config.inhouse_llm_client_id))
    with c3:
        ready = "✅ 호출 가능" if config.is_llm_configured else "❌ 미설정"
        st.metric("LLM 호출 가능 여부", ready)

    if not config.is_llm_configured:
        st.info(
            "ℹ️ LLM 자격증명이 등록되지 않았습니다. "
            "아래 **🔑 Client Credentials** 폼에 DevX 에서 발급받은 Client ID / Secret 을 입력하세요."
        )
    st.caption(
        "💡 Agent ID · User ID · Conversation ID 는 선택사항입니다. "
        "비워두면 payload 에서 자동으로 omit 되며 dify 가 기본값으로 처리합니다."
    )


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
        submitted = st.form_submit_button("저장", type="primary")

    if submitted:
        _save_general(config, {
            "confluence_base_url": base_url.strip(),
            "root_page_id": root_page_id.strip(),
            "confluence_type": confluence_type,
            "auth_type": auth_type,
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
    # ── 임베딩 ──────────────────────────────────────────────────────
    st.markdown("#### 🧩 임베딩 설정")
    _render_embedding_form(config)

    st.divider()

    # ── 사내 LLM ────────────────────────────────────────────────────
    st.markdown("#### 🤖 사내 InHouse LLM (DevX Gateway)")
    st.info(
        "📌 **이 값들은 어디서 받나요?**  \n"
        "사내 DevX 팀에서 발급/등록받은 정보입니다. 모르면 LLM 담당자(또는 #devx-llm 채널)에 문의하세요.  \n\n"
        "**입력 필요:**  \n"
        "• **Client ID / Client Secret** — OAuth 인증용 (DevX 발급)  \n"
        "• **Agent ID / User ID / Conversation ID** — dify 에 등록된 식별자  \n\n"
        "Endpoint URL · timeout 등 기반 설정은 아래 **고급 설정** 에 기본값으로 들어있어 보통 손댈 일 없습니다."
    )

    # 1) 메타·필수 식별자 폼 — 평문 저장 (DB)
    _render_inhouse_identifiers_form(config)

    st.divider()

    # 2) 자격증명 폼 — 키체인 저장
    st.markdown("##### 🔑 Client Credentials")
    st.caption("Client ID / Secret 은 OS 키체인에 암호화 저장됩니다. (config.json/SQLite 평문 저장 X)")
    _render_inhouse_key_credential(config)

    st.divider()

    # 3) 고급 — 접힌 영역
    with st.expander("⚙️ 고급 설정 (보통 변경 안 함)", expanded=False):
        _render_inhouse_advanced_form(config)

    # 4) OpenAI 임베딩 키 (embedding_provider == "openai" 일 때만)
    if config.embedding_provider == "openai":
        st.divider()
        st.markdown("##### 🔑 OpenAI 임베딩 API Key")
        st.caption("위 임베딩 Provider 를 openai 로 선택한 경우에만 사용됩니다. 키체인에 저장.")
        _render_openai_key_credential(config)


def _render_embedding_form(config: AppConfig) -> None:
    """임베딩 모델/Provider 설정만 분리된 폼."""
    with st.form("form_embedding"):
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
        submitted = st.form_submit_button("임베딩 설정 저장", type="primary")

    if not submitted:
        return

    updates: dict = {"embedding_provider": embedding_provider}
    if embedding_provider == "local":
        updates["local_model_name"] = embedding_model.strip()
    else:
        updates["embedding_model"] = embedding_model.strip()

    _prev_provider = config.embedding_provider
    _prev_model = config.local_model_name if _prev_provider == "local" else config.embedding_model
    _new_model = embedding_model.strip()
    _changed = (_prev_provider != embedding_provider) or (_prev_model != _new_model)

    _save_general(config, updates)
    from app.infrastructure.service_factory import invalidate as _invalidate_services
    _invalidate_services()
    st.success("임베딩 설정 저장 완료")
    if _changed:
        st.warning(
            "⚠️ **임베딩 모델이 변경되었습니다.**  \n"
            "기존 벡터 인덱스와 차원이 달라 벡터 검색이 오작동할 수 있습니다.  \n"
            "👉 **고급** 탭 → **전체 정비 (재추출 + 재색인)** 을 실행하세요."
        )
    st.rerun()


def _render_inhouse_identifiers_form(config: AppConfig) -> None:
    """사용자가 LLM 담당자에게 받는 dify 식별자 + extract_metadata."""
    with st.form("form_inhouse_identifiers"):
        agent_id = st.text_input(
            "Agent ID (UUID)",
            value=config.inhouse_llm_agent_id,
            placeholder="예: b6958377-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            help="dify 에 등록된 agent 의 UUID. LLM 담당자가 발급해줍니다.",
        )
        user_id = st.text_input(
            "User ID",
            value=config.inhouse_llm_user_id,
            placeholder="예: 20251105_xxxxxxxx",
            help="dify 에 등록된 사용자 식별자. 본인용 ID 를 LLM 담당자가 발급해줍니다. "
                 "★ 미등록 ID 를 넣으면 호출은 200 OK 가 떨어져도 응답 본문이 비어 옵니다.",
        )
        conversation_id = st.text_input(
            "Conversation ID (UUID)",
            value=config.inhouse_llm_conversation_id,
            placeholder="예: 99cae258-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            help="dify 에 사전 등록된 대화 UUID. 본인용 UUID 를 LLM 담당자가 발급해줍니다. "
                 "비워두면 매 호출마다 새 UUID 가 발급되지만, 미등록이면 응답이 비어 옵니다.",
        )

        extract_metadata = st.checkbox(
            "동기화 시 LLM 메타데이터 자동 추출",
            value=config.extract_metadata,
            help="현행화마다 LLM 으로 기술스택·효과·키워드 등을 자동 추출합니다.",
        )

        submitted = st.form_submit_button("저장", type="primary")

    if not submitted:
        return

    _save_general(config, {
        "inhouse_llm_agent_id": _safe_strip(agent_id),
        "inhouse_llm_user_id": _safe_strip(user_id),
        "inhouse_llm_conversation_id": _safe_strip(conversation_id),
        "extract_metadata": extract_metadata,
    })
    from app.infrastructure.service_factory import invalidate as _invalidate_services
    _invalidate_services()
    st.session_state["config_version"] = st.session_state.get("config_version", 0) + 1
    st.success("LLM 식별자 저장 완료")
    st.rerun()


def _render_inhouse_advanced_form(config: AppConfig) -> None:
    """기반 endpoint/timeout/agent_code — 거의 변경 안 함."""
    with st.form("form_inhouse_advanced"):
        auth_endpoint = st.text_input(
            "Auth Endpoint",
            value=config.inhouse_llm_auth_endpoint,
            help="OAuth2 토큰 발급 URL",
        )
        chat_endpoint = st.text_input(
            "Chat Endpoint",
            value=config.inhouse_llm_chat_endpoint,
            help="채팅 호출 URL (SSE)",
        )
        col1, col2 = st.columns(2)
        with col1:
            agent_code = st.text_input(
                "Agent Code",
                value=config.inhouse_llm_agent_code or "playground",
                help="기본 'playground' — DevX 가 별도 지시 없으면 그대로 두세요.",
            )
        with col2:
            timeout = st.number_input(
                "Timeout (초)", min_value=10, max_value=600,
                value=config.inhouse_llm_timeout or 120,
            )
        submitted = st.form_submit_button("고급 설정 저장")

    if not submitted:
        return

    _save_general(config, {
        "inhouse_llm_auth_endpoint": _safe_strip(auth_endpoint),
        "inhouse_llm_chat_endpoint": _safe_strip(chat_endpoint),
        "inhouse_llm_agent_code": _safe_strip(agent_code),
        "inhouse_llm_timeout": int(timeout),
    })
    from app.infrastructure.service_factory import invalidate as _invalidate_services
    _invalidate_services()
    st.success("고급 설정 저장 완료")
    st.rerun()


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
    st.info(
        f"현재 저장된 Client ID: **{_mask(config.inhouse_llm_client_id)}**  \n"
        f"현재 저장된 Client Secret: **{_mask(config.inhouse_llm_client_secret)}**"
    )
    st.caption(
        "DevX Gateway 는 OAuth2 client_credentials 인증을 사용합니다. "
        "둘 다 입력해야 토큰이 발급됩니다."
    )

    with st.form("form_inhouse_credentials"):
        new_client_id = st.text_input(
            "새 Client ID",
            type="password",
            placeholder="usr-XXXXXXXX (비워두면 변경 안 됨)",
        )
        new_client_secret = st.text_input(
            "새 Client Secret",
            type="password",
            placeholder="비워두면 변경 안 됨",
        )
        col_save, col_clear = st.columns([3, 1])
        with col_save:
            saved = st.form_submit_button("저장", type="primary")
        with col_clear:
            cleared = st.form_submit_button("삭제", type="secondary")

    if saved:
        updates: dict = {}
        if new_client_id.strip():
            updates["inhouse_llm_client_id"] = new_client_id.strip()
        if new_client_secret.strip():
            updates["inhouse_llm_client_secret"] = new_client_secret.strip()
        if not updates:
            st.warning("Client ID 또는 Client Secret 중 하나 이상 입력하세요.")
        else:
            _save_credentials(config, updates)
            st.success(f"자격증명이 저장되었습니다 ({', '.join(updates.keys())}).")
            st.rerun()

    if cleared:
        _save_credentials(config, {
            "inhouse_llm_client_id": "",
            "inhouse_llm_client_secret": "",
        })
        st.warning("Client ID / Secret 이 모두 삭제되었습니다.")
        st.rerun()



def _render_connection_test(config: AppConfig) -> None:
    st.markdown("#### 연결 테스트")

    col_cf, col_llm = st.columns(2)

    with col_cf:
        st.markdown("**Confluence**")
        if st.button("연결 테스트", key="btn_cf_test"):
            if not config.is_confluence_configured:
                st.session_state["_cf_test_result"] = ("warn", "Confluence URL / Root Page ID / PAT를 먼저 설정해주세요.")
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
                            st.session_state["_cf_test_result"] = ("ok", "Confluence 연결 성공!")
                        else:
                            st.session_state["_cf_test_result"] = ("err", "연결 실패. PAT와 URL을 확인해주세요.")
                    except Exception as e:
                        st.session_state["_cf_test_result"] = ("err", f"오류: {e}")

        result = st.session_state.get("_cf_test_result")
        if result:
            kind, msg = result
            if kind == "ok":
                st.success(msg)
            elif kind == "warn":
                st.warning(msg)
            else:
                st.error(msg)

    with col_llm:
        st.markdown("**LLM**")
        if st.button("연결 테스트", key="btn_llm_test"):
            if not config.is_llm_configured:
                st.session_state["_llm_test_result"] = (
                    "warn", "InHouse LLM 자격증명(Client ID/Secret)을 먼저 등록해주세요.", "",
                )
            else:
                with st.spinner("InHouse LLM (DevX Gateway) 확인 중..."):
                    try:
                        from app.infrastructure.llm.inhouse_provider import InHouseLLMProvider
                        p = InHouseLLMProvider(
                            auth_endpoint=config.inhouse_llm_auth_endpoint,
                            chat_endpoint=config.inhouse_llm_chat_endpoint,
                            client_id=config.inhouse_llm_client_id,
                            client_secret=config.inhouse_llm_client_secret,
                            user_id=config.inhouse_llm_user_id,
                            conversation_id=config.inhouse_llm_conversation_id,
                            agent_id=config.inhouse_llm_agent_id,
                            agent_code=config.inhouse_llm_agent_code,
                            timeout=config.inhouse_llm_timeout,
                        )
                        status, msg = p.health_check()
                        caption = f"chat: `{config.inhouse_llm_chat_endpoint}`"
                        st.session_state["_llm_test_result"] = (status, msg, caption)
                    except Exception as e:
                        st.session_state["_llm_test_result"] = ("err", f"오류: {e}", "")

        result = st.session_state.get("_llm_test_result")
        if result:
            kind, msg = result[0], result[1]
            caption = result[2] if len(result) > 2 else ""
            if caption:
                st.caption(caption)
            if kind == "ok":
                st.success(msg)
            elif kind == "warn":
                st.warning(msg)
            else:
                st.error(msg)


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
                mode_labels = {"full": "전체 정비", "fallback": "미추출·추출불가건 정비", "new_changed": "신규·수정건 정비"}
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
