from app.infrastructure.llm.base import LLMProviderBase
from app.shared.exceptions import ReportError
from app.shared.logger import get_logger

logger = get_logger()


class OpenAILLMProvider(LLMProviderBase):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key)
        except ImportError as e:
            raise ReportError("openai 패키지가 설치되지 않았습니다.") from e
        self.model = model

    def generate(self, prompt: str) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            raise ReportError(f"LLM 생성 실패: {e}") from e


def get_llm_provider(provider: str, model: str, api_key: str) -> LLMProviderBase:
    if provider == "openai":
        return OpenAILLMProvider(api_key=api_key, model=model)
    raise ReportError(f"지원하지 않는 LLM provider: {provider}")
