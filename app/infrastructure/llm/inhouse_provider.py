"""
사내 InHouse LLM Provider (DevX Gateway) — 동기 + blocking 모드.

SMAgentLab 의 작동 검증된 패턴을 그대로 이식:
  - response_mode = "blocking" (SSE 아님)
  - 선택 필드(agent_id, conversation_id)는 비어있으면 payload 에서 **omit**
    (빈 문자열로 전송하지 않음 — dify 가 빈 값 들어오면 거부)
  - user_id 비어있으면 "system" fallback (SMAgentLab 에서 검증)

호출 흐름:
  1. POST {auth_endpoint}  data: grant_type=client_credentials, client_id, client_secret
     → {"access_token": "...", "expires_in": 300}
  2. POST {chat_endpoint}  Authorization: Bearer {token}, response_mode=blocking
     → {"answer": "..."} 또는 {"message": "..."}
"""
import json
import threading
import time

import httpx

from app.infrastructure.llm.base import LLMProviderBase
from app.shared.exceptions import ReportError
from app.shared.logger import get_logger

logger = get_logger()


def _extract_answer(data: dict) -> str:
    """blocking 응답 JSON 에서 answer 추출. dify 는 answer/message 둘 중 하나로 반환."""
    return data.get("answer") or data.get("message") or ""


class InHouseLLMProvider(LLMProviderBase):
    """DevX Gateway 동기 클라이언트 (OAuth2 client_credentials + blocking)."""

    _token_cache: dict[str, dict] = {}
    _token_lock = threading.Lock()

    def __init__(
        self,
        auth_endpoint: str,
        chat_endpoint: str,
        client_id: str,
        client_secret: str,
        user_id: str = "",
        conversation_id: str = "",
        agent_id: str = "",
        agent_code: str = "playground",
        timeout: int = 120,
    ):
        if not auth_endpoint or not chat_endpoint:
            raise ReportError(
                "InHouse LLM endpoint 가 비어 있습니다.\n"
                "설정 탭 → LLM 고급 설정에서 auth/chat endpoint 를 입력하세요."
            )
        if not client_id or not client_secret:
            raise ReportError(
                "InHouse LLM client_id / client_secret 이 설정되지 않았습니다.\n"
                "설정 탭 → LLM 자격증명에서 등록하세요."
            )
        self._auth_endpoint = auth_endpoint.rstrip("/")
        self._chat_endpoint = chat_endpoint.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        # SMAgentLab 검증 패턴: user_id 비어있으면 "system" fallback
        self._user = user_id or "system"
        self._conversation_id = conversation_id  # 비면 payload 에서 omit
        self._agent_id = agent_id                # 비면 payload 에서 omit
        self._agent_code = agent_code or "playground"
        self._timeout = timeout

    def _get_access_token(self) -> str:
        with self._token_lock:
            now = time.time()
            state = self._token_cache.get(self._client_id) or {"token": None, "expires_at": 0.0}
            if state["token"] and state["expires_at"] > now + 30:
                return state["token"]

            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(
                        self._auth_endpoint,
                        data={
                            "grant_type": "client_credentials",
                            "client_id": self._client_id,
                            "client_secret": self._client_secret,
                        },
                    )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                raise ReportError(
                    f"InHouse LLM 토큰 발급 실패 (HTTP {e.response.status_code}): "
                    f"{e.response.text[:200]}"
                ) from e
            except httpx.RequestError as e:
                raise ReportError(f"InHouse LLM 토큰 발급 연결 오류: {e}") from e

            token = data.get("access_token")
            if not token:
                raise ReportError(f"InHouse LLM 토큰 응답에 access_token 없음: {data}")
            expires_in = int(data.get("expires_in", 300))
            state["token"] = token
            state["expires_at"] = now + expires_in
            self._token_cache[self._client_id] = state
            logger.info(
                "InHouse LLM 토큰 발급 완료 (expires_in=%ds, client=%s...)",
                expires_in, self._client_id[:8],
            )
            return token

    def _payload(self, query: str) -> dict:
        """blocking 페이로드 — 빈 선택 필드는 omit (★ dify 가 빈 값 거부)."""
        payload: dict = {
            "user": self._user,
            "query": query,
            "agent_code": self._agent_code,
            "knowledge_ids": [],
            "response_mode": "blocking",
        }
        if self._agent_id:
            payload["agent_id"] = self._agent_id
        if self._conversation_id:
            payload["conversation_id"] = self._conversation_id
        return payload

    def generate(self, prompt: str) -> str:
        """프롬프트 → 응답. blocking 호출."""
        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = self._payload(prompt)
        logger.info(
            "InHouse LLM 호출 → %s (query=%d chars, user=%s, agent_id=%s, conv_id=%s)",
            self._chat_endpoint, len(prompt), self._user,
            self._agent_id or "(none)", self._conversation_id or "(none)",
        )
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(self._chat_endpoint, json=payload, headers=headers)
            logger.info("InHouse LLM 응답 ← status=%d", resp.status_code)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise ReportError(
                f"InHouse LLM HTTP 오류 (status={e.response.status_code}): "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise ReportError(f"InHouse LLM 연결 오류: {e}") from e
        except json.JSONDecodeError:
            raise ReportError(f"InHouse LLM 응답이 JSON 형식이 아님: {resp.text[:200]}")

        answer = _extract_answer(data).strip()
        if not answer:
            raise ReportError(
                "InHouse LLM 응답이 비어있습니다.\n"
                f"전체 응답: {json.dumps(data, ensure_ascii=False)[:300]}"
            )
        if "민감 정보" in answer or "응답을 제공할 수 없습니다" in answer:
            raise ReportError(
                "LLM 생성 실패: 요청 내용이 민감 정보 필터에 걸렸습니다."
            )
        logger.info("InHouse LLM 답변 길이: %d chars", len(answer))
        return answer

    def health_check(self) -> tuple[str, str]:
        """토큰 발급 + ping 호출로 단계별 검증.
        반환: ("ok" | "warn" | "err", 메시지)."""
        # Step 1: 토큰 발급
        try:
            token = self._get_access_token()
        except ReportError as e:
            return "err", (
                f"❌ **1단계: 토큰 발급 실패** — Client ID/Secret 또는 Auth Endpoint 확인.  \n"
                f"세부: {str(e).splitlines()[0]}"
            )
        except Exception as e:
            return "err", f"❌ **1단계: 토큰 발급 오류** — {e}"

        # Step 2: chat 호출 (ping)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(self._chat_endpoint, json=self._payload("ping"), headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            return "err", (
                f"✅ 1단계 토큰 발급 OK  \n"
                f"❌ **2단계: chat HTTP {e.response.status_code}** — "
                f"{e.response.text[:200]}"
            )
        except httpx.RequestError as e:
            return "err", (
                f"✅ 1단계 토큰 발급 OK  \n"
                f"❌ **2단계: chat endpoint 연결 실패** — {e}"
            )
        except Exception as e:
            return "err", f"✅ 1단계 OK / ❌ **2단계 오류**: {e}"

        answer = _extract_answer(data).strip()
        if not answer:
            return "warn", (
                "✅ **1단계: 토큰 발급 OK**  \n"
                "⚠️ **2단계: chat 응답 본문이 비어있음**  \n\n"
                f"전체 응답: `{json.dumps(data, ensure_ascii=False)[:200]}`  \n\n"
                "**가능성 (확인 순서대로):**  \n"
                "1. **Client ID 가 가진 권한**으로 호출 가능한 agent 가 아님 → LLM 담당자에게 권한 확인  \n"
                "2. **Agent ID** 가 등록된 UUID 가 맞는지 확인 (현재: "
                f"`{self._agent_id or '(미설정 — payload omit)'}`)  \n"
                "3. **User ID** 가 dify 에 등록된 값인지 확인 (현재: "
                f"`{self._user}`)  \n"
            )
        return "ok", (
            "✅ **모든 단계 통과**  \n"
            "1단계 토큰 + 2단계 chat 응답 정상 수신.  \n"
            f"답변 미리보기: `{answer[:60]}{'…' if len(answer) > 60 else ''}`"
        )
