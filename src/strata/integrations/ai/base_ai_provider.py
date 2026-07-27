"""Abstract base class and response dataclass for AI LLM providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AiResponse:
    """Structured response from an AI provider."""

    content: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    cached: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class BaseAiProvider(ABC):
    """Abstract base class for LLM provider implementations."""

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> AiResponse:
        """Send a completion request and return the structured response."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider endpoint is reachable."""
