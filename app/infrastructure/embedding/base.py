from abc import ABC, abstractmethod


class EmbeddingProviderBase(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """텍스트 리스트 → 임베딩 리스트"""
        ...

    def embed(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]
