"""AI agent integration for advisory analysis during build/deploy workflows."""

from typing import TYPE_CHECKING, Optional

from strata.integrations.ai.ai_integration import AiAgentIntegration
from strata.integrations.ai.base_ai_provider import AiResponse, BaseAiProvider

if TYPE_CHECKING:
    from strata.services.configuration_service import ConfigurationService

__all__ = [
    "AiAgentIntegration",
    "AiResponse",
    "BaseAiProvider",
    "find_ai_integration",
]


def find_ai_integration(
    configuration_service: "Optional[ConfigurationService]",
    preferred_name: str = "",
) -> "Optional[AiAgentIntegration]":
    """Return the first (or named) enabled ``ai_agent`` integration from configuration.

    Returns ``None`` if no configuration service is available, no ``ai_agent``
    integration is declared, or the integration fails to instantiate.
    """
    if configuration_service is None:
        return None
    model = configuration_service.model
    if not model or not model.spec.integrations:
        return None

    specs = [i for i in model.spec.integrations if i.type == "ai_agent" and i.enabled]
    if not specs:
        return None

    spec = next((s for s in specs if s.name == preferred_name), specs[0])
    try:
        from strata.integrations.factory import IntegrationFactory

        return IntegrationFactory.create(spec)  # type: ignore[return-value]
    except Exception:
        return None
