"""
LLM Provider 팩토리.

사용처:
  from app.infrastructure.llm.factory import create_llm_provider
  llm = create_llm_provider(config)
  answer = llm.generate(prompt)

지원 provider: "openai" | "inhouse"
"""
from app.infrastructure.llm.base import LLMProviderBase
from app.shared.exceptions import ReportError
from app.shared.logger import get_logger

logger = get_logger()


def create_llm_provider(config) -> LLMProviderBase:
    """
    AppConfig를 받아 적절한 LLMProviderBase 구현체를 반환.
    provider 추가 시 이 함수만 수정.
    """
    provider = config.llm_provider

    if provider == "openai":
        from app.infrastructure.llm.openai_provider import OpenAILLMProvider
        if not config.llm_api_key:
            raise ReportError("LLM Provider=openai 이지만 API Key가 설정되지 않았습니다.")
        logger.info("LLM Provider: OpenAI (%s)", config.llm_model)
        return OpenAILLMProvider(api_key=config.llm_api_key, model=config.llm_model)

    if provider == "inhouse":
        from app.infrastructure.llm.inhouse_provider import InHouseLLMProvider
        logger.info("LLM Provider: InHouse (%s)", config.inhouse_llm_url)
        return InHouseLLMProvider(
            url=config.inhouse_llm_url,
            api_key=config.inhouse_llm_api_key,
            agent_code=config.inhouse_llm_agent_code,
            usecase_id=config.inhouse_llm_usecase_id,
            project_id=config.inhouse_llm_project_id,
            timeout=config.inhouse_llm_timeout,
        )

    raise ReportError(
        f"지원하지 않는 LLM provider: '{provider}'. "
        f"지원 목록: openai, inhouse"
    )
