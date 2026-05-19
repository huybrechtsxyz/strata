"""Resolves variables, secrets, and feature flags from configured store integrations into concrete runtime values."""

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional, Tuple

from strata.controllers.base_controller import BaseController
from strata.logger import get_logger
from strata.models.store_models import (
    FeatureStoreModel,
    FeatureStoreType,
    SecretStoreModel,
    SecretStoreType,
    VariableStoreModel,
    VariableStoreType,
)
from strata.services.deployment_service import DeploymentService
from strata.services.integration_service import IntegrationService

logger = get_logger(__name__)


@dataclass
class ResolvedValues:
    """Concrete runtime values resolved from variables, secrets, and feature flags.

    Attributes:
        variables (Dict[str, Any]):          Resolved configuration variables.
        secrets   (Dict[str, Any]):          Resolved secrets  (treat as sensitive).
        features  (Dict[str, Optional[bool]]): Resolved feature-flag booleans.
        errors    (List[str]):               Resolution errors / warnings.
    """

    variables: Dict[str, Any] = field(default_factory=dict)
    secrets: Dict[str, Any] = field(default_factory=dict)
    features: Dict[str, Optional[bool]] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return True when no values were resolved."""
        return not (self.variables or self.secrets or self.features)

    def as_tf_vars(self) -> Dict[str, str]:
        """Return all resolved values as a ``TF_VAR_*`` environment-variable dict.

        Intended for injection into subprocess calls (e.g. terraform init/plan/apply).
        Variables and features are considered non-sensitive; secrets are included
        but callers should avoid logging this dict.

        Naming:
          - variables → TF_VAR_<key>
          - secrets   → TF_VAR_<key>
          - features  → TF_VAR_<key>  (bool serialised as "true" / "false")
        """
        result: Dict[str, str] = {}
        for key, val in self.variables.items():
            result[f"TF_VAR_{key}"] = str(val)
        for key, val in self.features.items():
            if val is not None:
                result[f"TF_VAR_{key}"] = str(val).lower()  # "true" / "false"
        for key, val in self.secrets.items():
            result[f"TF_VAR_{key}"] = str(val)
        return result


@contextmanager
def inject_tf_vars(resolved: ResolvedValues) -> Generator[None, None, None]:
    """Context manager that injects TF_VAR_* env vars and removes them on exit.

    Since ``run_command`` snapshots ``os.environ`` at call time, variables must
    be present before each subprocess call.  Use this context around any step
    that runs a terraform sub-process:

        with inject_tf_vars(self._resolved_values):
            result = self._tf.apply(...)
    """
    tf_vars = resolved.as_tf_vars() if resolved else {}
    if not tf_vars:
        yield
        return

    prev: Dict[str, Optional[str]] = {}
    for key, val in tf_vars.items():
        prev[key] = os.environ.get(key)
        os.environ[key] = val

    try:
        yield
    finally:
        for key, original in prev.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


class ValueController(BaseController):
    """Resolves variables, secrets, and feature flags for a deployment.

    Typical usage inside a command or deployer:

        controller = ValueController()
        ok, resolved, errors = controller.resolve_values(deployment_service)
        if not ok:
            # handle errors
        with inject_tf_vars(resolved):
            terraform.apply(...)
    """

    def resolve_values(
        self,
        deployment_service: "DeploymentService",
        strict: bool = False,
    ) -> Tuple[bool, ResolvedValues, List[str]]:
        """Resolve all variables, secrets, and feature flags from the deployment.

        Args:
            deployment_service: Loaded, validated deployment service.
            strict:             When True, any resolution failure is treated as
                                an error and success=False is returned.

        Returns:
            (success, ResolvedValues, error_messages)
        """
        resolved = ResolvedValues()

        environment_service = deployment_service.get_environment_service()
        if environment_service is None:
            logger.warning("No environment service attached to deployment — no variables/secrets/features to resolve.")
            return True, resolved, []

        # Lazy-init integrations once (idempotent; no-op if already done).
        self._ensure_integrations_initialized()

        # --- variables ---
        for item in environment_service.get_variables():
            val, err = self._resolve_variable(item)
            if err:
                resolved.errors.append(err)
                if strict:
                    return False, resolved, resolved.errors
            else:
                resolved.variables[item.key] = val

        # --- secrets ---
        for secret_item in environment_service.get_secrets():
            val, err = self._resolve_secret(secret_item)
            if err:
                resolved.errors.append(err)
                if strict:
                    return False, resolved, resolved.errors
            else:
                resolved.secrets[secret_item.key] = val

        # --- features ---
        for feature_item in environment_service.get_features():
            val, err = self._resolve_feature(feature_item)
            if err:
                resolved.errors.append(err)
                if strict:
                    return False, resolved, resolved.errors
            else:
                resolved.features[feature_item.key] = val

        logger.debug(
            "Value resolution complete",
            variables=len(resolved.variables),
            secrets=len(resolved.secrets),
            features=len(resolved.features),
            errors=len(resolved.errors),
        )

        success = len(resolved.errors) == 0 if strict else True
        return success, resolved, resolved.errors

    # Per-type resolvers

    def _resolve_variable(self, item: VariableStoreModel) -> Tuple[Optional[Any], Optional[str]]:
        """Resolve a single variable.  Returns (value, error_or_None)."""
        store = item.store

        if store == VariableStoreType.CONSTANT:
            return item.value, None

        if store == VariableStoreType.ENVIRONMENT:
            env_val = os.environ.get(str(item.value))
            if env_val is None:
                return None, (f"Variable '{item.key}': env var '{item.value}' is not set.")
            return env_val, None

        # Integration-backed store
        integration = self._get_integration_by_type(store.value)
        if integration is None:
            return None, (f"Variable '{item.key}': no integration registered for store type '{store.value}'.")
        val = integration.get_variable(str(item.value))
        if val is None:
            return None, (f"Variable '{item.key}': key '{item.value}' not found in '{store.value}' store.")
        return val, None

    def _resolve_secret(self, item: SecretStoreModel) -> Tuple[Optional[Any], Optional[str]]:
        """Resolve a single secret.  Returns (value, error_or_None)."""
        store = item.store

        if store == SecretStoreType.CONSTANT:
            return item.value, None

        if store == SecretStoreType.ENVIRONMENT:
            env_val = os.environ.get(str(item.value))
            if env_val is None:
                return None, (f"Secret '{item.key}': env var '{item.value}' is not set.")
            return env_val, None

        integration = self._get_integration_by_type(store.value)
        if integration is None:
            return None, (f"Secret '{item.key}': no integration registered for store type '{store.value}'.")
        val = integration.get_secret(str(item.value))
        if val is None:
            return None, (f"Secret '{item.key}': key '{item.value}' not found in '{store.value}' store.")
        return val, None

    def _resolve_feature(self, item: FeatureStoreModel) -> Tuple[Optional[bool], Optional[str]]:
        """Resolve a single feature flag.  Returns (value, error_or_None)."""
        store = item.store

        if store == FeatureStoreType.CONSTANT:
            try:
                return bool(item.value), None
            except (TypeError, ValueError):
                return None, (f"Feature '{item.key}': cannot convert constant value '{item.value}' to bool.")

        if store == FeatureStoreType.ENVIRONMENT:
            env_val = os.environ.get(str(item.value))
            if env_val is None:
                return None, (f"Feature '{item.key}': env var '{item.value}' is not set.")
            return env_val.lower() not in ("0", "false", "no", "off"), None

        integration = self._get_integration_by_type(store.value)
        if integration is None:
            return None, (f"Feature '{item.key}': no integration registered for store type '{store.value}'.")
        val = integration.get_feature(str(item.value))
        if val is None:
            return None, (f"Feature '{item.key}': flag '{item.value}' not found in '{store.value}' store.")
        return val, None

    # Helpers

    @staticmethod
    def _ensure_integrations_initialized() -> None:
        """Lazily initialise integrations (idempotent)."""

        svc = IntegrationService.get_instance()
        if not svc.is_initialized():
            ok, errors = svc.initialize_integrations()
            if not ok:
                logger.warning(
                    "Integration initialisation had errors",
                    errors=errors,
                )

    @staticmethod
    def _get_integration_by_type(store_type: str):
        """Return the first registered integration whose type matches *store_type*.

        Returns None when no matching integration is registered.
        """

        svc = IntegrationService.get_instance()
        for name in svc.list_integrations():
            integration = svc.get_integration(name)
            if integration is not None and getattr(integration, "integration_type", None) == store_type:
                return integration
        return None
