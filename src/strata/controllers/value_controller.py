"""Resolves variables, secrets, and feature flags from configured store integrations into concrete runtime values."""

import os
from typing import Any, List, Optional, Tuple

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
from strata.utils.resolved_values import (  # noqa: F401 — re-exported for callers
    ResolvedValues,
    inject_compose_env,
    inject_tf_vars,
)
from strata.utils.secret_generator import generate_secret

logger = get_logger(__name__)


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
            val, err, note = self._resolve_variable(item)
            if err:
                resolved.errors.append(err)
                if strict:
                    return False, resolved, resolved.errors
            else:
                resolved.variables[item.key] = val
                if note:
                    resolved.variable_notes[item.key] = note

        # --- secrets ---
        for secret_item in environment_service.get_secrets():
            val, err, note = self._resolve_secret(secret_item)
            if err:
                resolved.errors.append(err)
                if strict:
                    return False, resolved, resolved.errors
            else:
                resolved.secrets[secret_item.key] = val
                if note:
                    resolved.secret_notes[secret_item.key] = note

        # --- features ---
        for feature_item in environment_service.get_features():
            val, err, note = self._resolve_feature(feature_item)
            if err:
                resolved.errors.append(err)
                if strict:
                    return False, resolved, resolved.errors
            else:
                resolved.features[feature_item.key] = val
                if note:
                    resolved.feature_notes[feature_item.key] = note

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

    def _resolve_variable(self, item: VariableStoreModel) -> Tuple[Optional[Any], Optional[str], Optional[str]]:
        """Resolve a single variable.  Returns (value, error_or_None, note_or_None)."""
        store = item.store

        if store == VariableStoreType.CONSTANT:
            return item.value, None, None

        if store == VariableStoreType.ENVIRONMENT:
            env_val = os.environ.get(str(item.value))
            if env_val is None:
                return None, (f"Variable '{item.key}': env var '{item.value}' is not set."), None
            return env_val, None, None

        # Integration-backed store
        integration = self._get_integration_by_type(store.value)
        if integration is None:
            return None, (f"Variable '{item.key}': no integration registered for store type '{store.value}'."), None
        val = integration.get_variable(str(item.value))
        if val is None:
            if item.default is None:
                return None, (f"Variable '{item.key}': key '{item.value}' not found in '{store.value}' store."), None
            # Seed-on-missing: write the declared default
            ok = integration.set_variable(str(item.value), item.default)
            if not ok:
                # Race: another process may have just written it — try re-reading
                reread = integration.get_variable(str(item.value))
                if reread is not None:
                    logger.warning(
                        "Variable seeded by another process — using existing value",
                        key=item.key,
                        store=store.value,
                    )
                    return reread, None, None
                return None, (f"Variable '{item.key}': store write for default failed in '{store.value}'."), None
            logger.info(
                "Variable seeded with default",
                action="variable_seeded",
                key=str(item.value),
                store=store.value,
                default=item.default,
            )
            return item.default, None, f"default: {item.default}"
        return val, None, None

    def _resolve_secret(self, item: SecretStoreModel) -> Tuple[Optional[Any], Optional[str], Optional[str]]:
        """Resolve a single secret.  Returns (value, error_or_None, note_or_None)."""
        store = item.store

        if store == SecretStoreType.CONSTANT:
            return item.value, None, None

        if store == SecretStoreType.ENVIRONMENT:
            env_val = os.environ.get(str(item.value))
            if env_val is None:
                return None, (f"Secret '{item.key}': env var '{item.value}' is not set."), None
            return env_val, None, None

        if store == SecretStoreType.GITHUB:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                logger.warning(
                    "Resolving 'github' store secret outside GitHub Actions",
                    key=item.key,
                    hint="Set the env var manually for local runs, or run inside a GitHub Actions workflow.",
                )
            env_key = str(item.value).upper()
            env_val = os.environ.get(env_key)
            if env_val is None:
                return (
                    None,
                    (
                        f"Secret '{item.key}': GitHub Actions env var '{env_key}' is not set. "
                        f"Ensure the secret is declared in your GitHub Actions workflow and the workflow is running."
                    ),
                    None,
                )
            return env_val, None, None

        integration = self._get_integration_by_type(store.value)
        if integration is None:
            return None, (f"Secret '{item.key}': no integration registered for store type '{store.value}'."), None
        val = integration.get_secret(str(item.value))
        if val is None:
            if item.generate is None:
                return None, (f"Secret '{item.key}': key '{item.value}' not found in '{store.value}' store."), None
            # Generate-on-missing: create a new cryptographic value
            generated = generate_secret(item.generate.type.value, item.generate.length)
            ok = integration.set_secret(str(item.value), generated)
            if not ok:
                # Race: another process may have just written it — try re-reading
                reread = integration.get_secret(str(item.value))
                if reread is not None:
                    logger.warning(
                        "Secret created by another process — using existing value",
                        key=item.key,
                        store=store.value,
                    )
                    return reread, None, None
                return (
                    None,
                    (f"Secret '{item.key}': generation succeeded but store write failed in '{store.value}'."),
                    None,
                )
            logger.info(
                "Secret generated and stored",
                action="secret_generated",
                key=str(item.value),
                store=store.value,
                generator_type=item.generate.type.value,
            )
            return generated, None, "generated"
        return val, None, None

    def _resolve_feature(self, item: FeatureStoreModel) -> Tuple[Optional[bool], Optional[str], Optional[str]]:
        """Resolve a single feature flag.  Returns (value, error_or_None, note_or_None)."""
        store = item.store

        if store == FeatureStoreType.CONSTANT:
            try:
                return bool(item.value), None, None
            except (TypeError, ValueError):
                return None, (f"Feature '{item.key}': cannot convert constant value '{item.value}' to bool."), None

        if store == FeatureStoreType.ENVIRONMENT:
            env_val = os.environ.get(str(item.value))
            if env_val is None:
                return None, (f"Feature '{item.key}': env var '{item.value}' is not set."), None
            return env_val.lower() not in ("0", "false", "no", "off"), None, None

        integration = self._get_integration_by_type(store.value)
        if integration is None:
            return None, (f"Feature '{item.key}': no integration registered for store type '{store.value}'."), None
        val = integration.get_feature(str(item.value))
        if val is None:
            if item.default is None:
                return None, (f"Feature '{item.key}': flag '{item.value}' not found in '{store.value}' store."), None
            # Seed-on-missing: write the declared default state
            default_bool = item.default.lower() not in ("0", "false", "no", "off")
            ok = integration.set_feature(str(item.value), default_bool)
            if not ok:
                reread = integration.get_feature(str(item.value))
                if reread is not None:
                    logger.warning(
                        "Feature flag seeded by another process — using existing value",
                        key=item.key,
                        store=store.value,
                    )
                    return reread, None, None
                return None, (f"Feature '{item.key}': store write for default failed in '{store.value}'."), None
            logger.info(
                "Feature flag seeded with default",
                action="feature_seeded",
                key=str(item.value),
                store=store.value,
                default=item.default,
            )
            return default_bool, None, f"default: {item.default}"
        return val, None, None

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
