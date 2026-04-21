from abc import ABC, abstractmethod


class LLMProviderBase(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        """프롬프트 → 응답 텍스트"""
        ...
