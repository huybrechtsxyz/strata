"""AiAgentIntegration — advisory AI analysis at build/deploy lifecycle points.

Wraps a configurable LLM provider (Ollama, OpenAI, Azure OpenAI, Anthropic,
Azure CLI) and exposes analysis methods for each lifecycle hook defined in
ADR-0025.  All methods are purely advisory — no infrastructure mutations.

Configuration YAML (``kind: configuration``)::

    spec:
      integrations:
        - name: ai-advisor
          type: ai_agent
          endpoints:
            address: https://my-aoai.openai.azure.com/
          authentication:
            method: api_key
            api_key:
              api_key: AZURE_OPENAI_API_KEY   # env var name
          properties:
            provider: azure_openai            # ollama | openai | azure_openai | anthropic | azure_cli
            model: gpt-4o
            temperature: 0.1
            max_tokens: 4096
            timeout: 60
            enabled_hooks: [deploy_plan_after, build_sbom_after]
"""

import os
from pathlib import Path
from typing import Any, List, Optional, Tuple

from strata.data.prompts import PromptLoader
from strata.integrations.ai.base_ai_provider import AiResponse, BaseAiProvider
from strata.integrations.ai.cache import AiResponseCache
from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger
from strata.logger.audit import audit
from strata.models.integration_model import IntegrationModel
from strata.utils.config import get_ai_cache_dir

logger = get_logger(__name__)


class AiAgentIntegration(BaseIntegration):
    """Wraps an LLM provider for advisory analysis during build/deploy.

    Lifecycle methods: ``analyse_plan``, ``diagnose_failure``, ``analyse_sbom``,
    ``explain_drift``, ``summarise_deployment``, ``review_policy_violations``.
    """

    COMMAND = "ai_agent"

    def __init__(self, config: IntegrationModel) -> None:
        super().__init__(config)
        props = config.properties or {}
        self._provider_type: str = props.get("provider", "ollama")
        self._model_name: str = props.get("model", "")
        self._temperature: float = float(props.get("temperature", 0.1))
        self._max_tokens: int = int(props.get("max_tokens", 4096))
        self._timeout: int = int(props.get("timeout", 60))
        self._enabled_hooks: List[str] = props.get("enabled_hooks", [])
        self._provider: Optional[BaseAiProvider] = None
        self._cache: Optional[AiResponseCache] = None
        self._cache_ttl: int = int(props.get("cache_ttl_seconds", 86_400))

    # ------------------------------------------------------------------
    # BaseIntegration abstract methods
    # ------------------------------------------------------------------

    def get_version_command(self) -> List[str]:
        return []  # No CLI binary

    def parse_version(self, version_output: str) -> str:
        return self._model_name

    def is_available(self, use_cache: bool = True) -> bool:  # noqa: ARG002
        if self._is_available is not None:
            return self._is_available
        try:
            self._is_available = self.provider.is_available()
        except Exception as exc:
            logger.debug("ai_agent_availability_check_failed", error=str(exc))
            self._is_available = False
        return self._is_available

    def ensure_available(self) -> Tuple[bool, str]:
        if not self.is_available():
            return False, f"AI provider '{self._provider_type}' is not reachable — check endpoint and authentication"
        return True, ""

    # ------------------------------------------------------------------
    # Provider factory
    # ------------------------------------------------------------------

    @property
    def provider(self) -> BaseAiProvider:
        if self._provider is None:
            self._provider = self._build_provider()
        return self._provider

    def _build_provider(self) -> BaseAiProvider:
        props = self.config.properties or {}
        provider_type = props.get("provider", "ollama")
        model = props.get("model", "")
        endpoint = self.config.endpoints.address if self.config.endpoints else ""
        timeout = int(props.get("timeout", 60))
        auth = self.config.authentication

        if provider_type == "ollama":
            from strata.integrations.ai.ollama_provider import OllamaProvider

            return OllamaProvider(
                endpoint=endpoint or "http://localhost:11434",
                model=model or "llama3",
                timeout=timeout,
            )

        if provider_type in ("openai", "azure_openai"):
            from strata.integrations.ai.openai_provider import OpenAiProvider

            api_key = self._resolve_api_key(auth)
            return OpenAiProvider(
                model=model,
                api_key=api_key,
                endpoint=endpoint,
                is_azure=(provider_type == "azure_openai"),
                timeout=timeout,
            )

        if provider_type == "azure_cli":
            from strata.integrations.ai.azure_cli_provider import AzureCliProvider

            return AzureCliProvider(model=model, endpoint=endpoint, timeout=timeout)

        if provider_type == "anthropic":
            from strata.integrations.ai.anthropic_provider import AnthropicProvider

            api_key = self._resolve_api_key(auth)
            return AnthropicProvider(model=model, api_key=api_key, timeout=timeout)

        raise ValueError(f"Unknown AI provider type: {provider_type!r}")

    @staticmethod
    def _resolve_api_key(auth: Any) -> str:
        """Resolve API key from authentication config.

        Supports ``method: api_key`` (``api_key.api_key`` is an env var name).
        Logs a warning when the env var is absent so operators can diagnose
        misconfiguration without inspecting source code.
        """
        if auth is None or auth.method != "api_key":
            return ""
        if not auth.api_key or not auth.api_key.api_key:
            logger.warning("api_key auth configured but api_key.api_key field is empty")
            return ""
        env_var_name: str = auth.api_key.api_key
        value = os.environ.get(env_var_name)
        if not value:
            logger.warning("ai_agent_api_key_env_var_missing", env_var=env_var_name)
        return value or ""

    # ------------------------------------------------------------------
    # Internal analysis runner
    # ------------------------------------------------------------------

    def _get_cache(self, work_path: Optional[Path]) -> Optional[AiResponseCache]:
        """Return (or lazily initialise) the response cache for *work_path*."""
        if work_path is None:
            return None
        if self._cache is None:
            self._cache = AiResponseCache(get_ai_cache_dir(work_path), ttl=self._cache_ttl)
        return self._cache

    def _analyse(
        self,
        hook: str,
        system_prompt: str,
        user_prompt: str,
        work_path: Optional[Path] = None,
        cacheable: bool = True,
        prompt_version: str = "1.0",
    ) -> AiResponse:
        cache = self._get_cache(work_path) if cacheable else None
        content_hash: str = ""
        if cache is not None:
            content_hash = AiResponseCache.content_hash(user_prompt)
            cached = cache.get(prompt_version, content_hash, self._model_name)
            if cached is not None:
                logger.debug("ai_cache_hit", hook=hook)
                audit(
                    f"ai_agent.{hook}",
                    target=self.integration_name,
                    detail={"cached": True, "model": self._model_name},
                )
                return cached

        response = self.provider.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        audit(
            f"ai_agent.{hook}",
            target=self.integration_name,
            detail={
                "provider": response.provider,
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "duration_ms": response.duration_ms,
                "cached": False,
            },
        )
        if cache is not None and content_hash:
            cache.put(prompt_version, content_hash, self._model_name, response)
        return response

    # ------------------------------------------------------------------
    # Lifecycle analysis methods (ADR-0025 §4)
    # ------------------------------------------------------------------

    def analyse_plan(self, plan_json: dict, deployment_context: dict) -> AiResponse:
        """Analyse a Terraform plan and return summary + risk assessment."""
        work_path = _work_path(deployment_context)
        prompt = PromptLoader.load("plan_review", work_path=work_path)
        return self._analyse(
            "analyse_plan",
            prompt.SYSTEM,
            prompt.build_user_prompt(plan_json, deployment_context),
            work_path=work_path,
            cacheable=True,
            prompt_version=prompt._cls.VERSION,
        )

    def diagnose_failure(self, error_output: str, step: str, context: dict) -> AiResponse:
        """Diagnose a deployer step failure and suggest remediation."""
        work_path = _work_path(context)
        prompt = PromptLoader.load("failure_diagnosis", work_path=work_path)
        return self._analyse(
            "diagnose_failure",
            prompt.SYSTEM,
            prompt.build_user_prompt(error_output, step, context),
            work_path=work_path,
            cacheable=False,  # always unique context
        )

    def analyse_sbom(self, sbom_json: dict, policies: list) -> AiResponse:
        """Analyse SBOM for supply-chain risks against configured policies."""
        prompt = PromptLoader.load("sbom_analysis")
        return self._analyse(
            "analyse_sbom",
            prompt.SYSTEM,
            prompt.build_user_prompt(sbom_json, policies),
            cacheable=True,
            prompt_version=prompt._cls.VERSION,
        )

    def explain_drift(self, drift_report: dict, context: dict) -> AiResponse:
        """Explain infrastructure drift in plain language."""
        work_path = _work_path(context)
        prompt = PromptLoader.load("drift_explanation", work_path=work_path)
        return self._analyse(
            "explain_drift",
            prompt.SYSTEM,
            prompt.build_user_prompt(drift_report, context),
            work_path=work_path,
            cacheable=True,
            prompt_version=prompt._cls.VERSION,
        )

    def summarise_deployment(self, manifest: dict, history: list) -> AiResponse:
        """Generate a human-readable deployment summary."""
        prompt = PromptLoader.load("deployment_summary")
        return self._analyse(
            "summarise_deployment",
            prompt.SYSTEM,
            prompt.build_user_prompt(manifest, history),
            cacheable=False,  # each deployment run is unique
        )

    def review_policy_violations(self, violations: list, context: dict) -> AiResponse:
        """Explain policy violations and suggest YAML fixes."""
        work_path = _work_path(context)
        prompt = PromptLoader.load("policy_review", work_path=work_path)
        return self._analyse(
            "review_policy_violations",
            prompt.SYSTEM,
            prompt.build_user_prompt(violations, context),
            work_path=work_path,
            cacheable=True,
            prompt_version=prompt._cls.VERSION,
        )

    # ------------------------------------------------------------------
    # Hook gating helper
    # ------------------------------------------------------------------

    def hook_enabled(self, hook: str) -> bool:
        """Return True if this lifecycle hook is in the enabled_hooks list."""
        return hook in self._enabled_hooks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _work_path(context: dict) -> Optional[Path]:
    wp = context.get("work_path")
    return Path(wp) if wp else None
