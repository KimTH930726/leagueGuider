"""
사내 InHouse LLM Provider (DevX MCP API) — 동기 버전.

SMAgentLab의 service/llm/inhouse.py를 Streamlit(sync) 환경에 맞게 포팅.
- async httpx.AsyncClient  →  sync httpx.Client
- generate_once()           →  generate(prompt)  (LLMProviderBase 인터페이스)
- 스트리밍은 leagueGuider에서 불필요하여 blocking 모드만 구현

DevX MCP API 페이로드 스펙 (SMAgentLab 참조):
  POST {url}
  Authorization: Bearer {api_key}
  Body: {
    usecase_code, query, response_mode="blocking",
    inputs?: {model}, usecase_id?, project_id?, conversation_id?
  }

응답 우선순위 (SMAgentLab _extract_answer 동일):
  1. external_response.dify_response.answer
  2. message
  3. answer
"""
import json
from typing import Optional

import httpx

from app.infrastructure.llm.base import LLMProviderBase
from app.shared.exceptions import ReportError
from app.shared.logger import get_logger

logger = get_logger()


def _extract_answer(data: dict) -> str:
    """SMAgentLab _extract_answer() 동일 로직."""
    ext = data.get("external_response")
    if isinstance(ext, dict):
        dify = ext.get("dify_response")
        if isinstance(dify, dict) and dify.get("answer"):
            return dify["answer"]
    if data.get("message"):
        return data["message"]
    if data.get("answer"):
        return data["answer"]
    return json.dumps(data, ensure_ascii=False)


class InHouseLLMProvider(LLMProviderBase):
    """DevX MCP API 동기 클라이언트."""

    def __init__(
        self,
        url: str,
        api_key: str = "",
        agent_code: str = "playground",
        usecase_id: str = "",
        project_id: str = "",
        model: str = "",
        timeout: int = 120,
    ):
        if not url:
            raise ReportError(
                "LLM Provider=inhouse 이지만 inhouse_llm_url 이 설정되지 않았습니다.\n"
                "설정 탭 → InHouse LLM URL을 입력하세요."
            )
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._agent_code = agent_code
        self._usecase_id = usecase_id or None
        self._project_id = project_id or None
        self._model = model or None
        self._timeout = timeout

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _payload(self, query: str) -> dict:
        """blocking 모드 페이로드 (SMAgentLab _build_payload 참조)."""
        body: dict = {
            "usecase_code": self._agent_code,
            "query": query,
            "response_mode": "blocking",
        }
        if self._model:
            body["inputs"] = {"model": self._model}
        if self._usecase_id:
            body["usecase_id"] = self._usecase_id
        if self._project_id:
            body["project_id"] = self._project_id
        return body

    def generate(self, prompt: str) -> str:
        """
        단순 blocking POST → answer 반환.
        leagueGuider에서는 system prompt를 prompt에 인라인으로 넣어서 전달함
        (extractor.py, report_service.py 모두 이 방식).
        """
        logger.info(
            "InHouse LLM 호출 → %s (query=%d chars)",
            self._url, len(prompt),
        )
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    self._url,
                    json=self._payload(prompt),
                    headers=self._headers(),
                )
                logger.info("InHouse LLM 응답 ← status=%d", resp.status_code)
                resp.raise_for_status()
                answer = _extract_answer(resp.json())
                logger.info("InHouse LLM 답변 길이: %d chars", len(answer))
                if "민감 정보" in answer or "응답을 제공할 수 없습니다" in answer:
                    raise ReportError(
                        "LLM 생성 실패: 요청 내용이 민감 정보 필터에 걸렸습니다.\n"
                        "프롬프트에 포함된 문서 내용을 확인하거나, 관리자에게 문의하세요."
                    )
                return answer
        except httpx.HTTPStatusError as e:
            raise ReportError(
                f"InHouse LLM HTTP 오류 (status={e.response.status_code}): {e}"
            ) from e
        except httpx.RequestError as e:
            raise ReportError(f"InHouse LLM 연결 오류: {e}") from e
        except Exception as e:
            raise ReportError(f"InHouse LLM 실패: {e}") from e

    def health_check(self) -> tuple[bool, str]:
        """서버 도달 가능 여부. (성공여부, 메시지) 반환.
        HTTP 응답이 오면 (연결 오류가 아니면) 서버 살아있음으로 판단."""
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    self._url,
                    json=self._payload("ping"),
                    headers=self._headers(),
                )
            logger.info("InHouse health_check ← status=%d", resp.status_code)
            if resp.status_code < 500:
                return True, f"서버 응답 확인 (HTTP {resp.status_code})"
            return False, f"서버 오류 (HTTP {resp.status_code}): {resp.text[:200]}"
        except httpx.ConnectError as e:
            return False, f"연결 실패 — URL을 확인하세요: {e}"
        except httpx.TimeoutException:
            return False, "응답 시간 초과 (10초)"
        except Exception as e:
            return False, f"오류: {e}"
