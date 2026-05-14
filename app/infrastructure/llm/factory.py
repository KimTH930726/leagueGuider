"""
LLM Provider 팩토리 — 사내 InHouse (DevX Gateway) 전용.

사용처:
  from app.infrastructure.llm.factory import create_llm_provider
  llm = create_llm_provider(config)
  answer = llm.generate(prompt)
"""
from app.infrastructure.llm.base import LLMProviderBase
from app.infrastructure.llm.inhouse_provider import InHouseLLMProvider
from app.shared.logger import get_logger

logger = get_logger()


def create_llm_provider(config) -> LLMProviderBase:
    logger.info("LLM Provider: InHouse DevX Gateway (%s)", config.inhouse_llm_chat_endpoint)
    return InHouseLLMProvider(
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
