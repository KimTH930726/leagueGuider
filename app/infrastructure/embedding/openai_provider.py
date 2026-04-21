from app.infrastructure.embedding.base import EmbeddingProviderBase
from app.shared.exceptions import EmbeddingError
from app.shared.logger import get_logger

logger = get_logger()

BATCH_SIZE = 100  # OpenAI 임베딩 최대 배치 크기


class OpenAIEmbeddingProvider(EmbeddingProviderBase):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key)
        except ImportError as e:
            raise EmbeddingError("openai 패키지가 설치되지 않았습니다.") from e
        self.model = model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        results: list[list[float]] = []
        try:
            # 배치 분할 처리
            for i in range(0, len(texts), BATCH_SIZE):
                batch = texts[i : i + BATCH_SIZE]
                resp = self._client.embeddings.create(input=batch, model=self.model)
                batch_result = [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]
                results.extend(batch_result)
        except Exception as e:
            raise EmbeddingError(f"OpenAI 임베딩 실패: {e}") from e
        return results


def get_embedding_provider(
    provider: str, model: str, api_key: str, local_model_dir: str = ""
) -> EmbeddingProviderBase:
    """provider 문자열로 구현체 반환"""
    if provider == "openai":
        return OpenAIEmbeddingProvider(api_key=api_key, model=model)
    if provider == "local":
        from app.infrastructure.embedding.local_provider import LocalEmbeddingProvider
        return LocalEmbeddingProvider(model_name=model, model_dir=local_model_dir)
    raise EmbeddingError(f"지원하지 않는 embedding provider: {provider}")
