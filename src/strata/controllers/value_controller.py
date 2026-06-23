"""Resolves variables, secrets, and feature flags from configured store integrations into concrete runtime values."""

import json
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
from strata.utils.secret_generator import generate_secret

logger = get_logger(__name__)


@dataclass
class ResolvedValues:
    """Concrete runtime values resolved from variables, secrets, and feature flags.

    Attributes:
        variables              (Dict[str, Any]):          Resolved configuration variables.
        secrets                (Dict[str, Any]):          Resolved secrets  (treat as sensitive).
        features               (Dict[str, Optional[bool]]): Resolved feature-flag booleans.
        stage_outputs          (Dict[str, Any]):          Non-sensitive outputs collected from
                                                           preceding deployment stages.
                                                           Injected as TF_VAR_* / verbatim env
                                                           vars into every subsequent stage.
        stage_outputs_sensitive (Dict[str, Any]):         Sensitive outputs from preceding stages
                                                           (e.g. Terraform ``sensitive = true``).
                                                           Available internally but never injected
                                                           into subprocess environments.
        errors                 (List[str]):               Resolution errors / warnings.
    """

    variables: Dict[str, Any] = field(default_factory=dict)
    secrets: Dict[str, Any] = field(default_factory=dict)
    features: Dict[str, Optional[bool]] = field(default_factory=dict)
    stage_outputs: Dict[str, Any] = field(default_factory=dict)
    stage_outputs_sensitive: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    variable_notes: Dict[str, str] = field(default_factory=dict)
    secret_notes: Dict[str, str] = field(default_factory=dict)
    feature_notes: Dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """Return True when no values were resolved."""
        return not (self.variables or self.secrets or self.features or self.stage_outputs)

    def for_stage(self, allowed_secrets: Optional[List[str]] = None) -> "ResolvedValues":
        """Return a copy scoped to the given stage's secret allowlist.

        STRATA_CONTEXT (variables, features, stage_outputs) passes through unfiltered.
        STRATA_SENSITIVE (secrets, stage_outputs_sensitive) is filtered to only
        the keys in ``allowed_secrets``.

        Args:
            allowed_secrets: List of secret key names this stage may access.
                - ``None`` or ``[]`` → no secrets, no sensitive outputs.
                - ``['*']``          → all secrets + all sensitive outputs (escape hatch).
                - ``['a', 'b']``     → only those keys from secrets + stage_outputs_sensitive.
        """
        if not allowed_secrets:
            return ResolvedValues(
                variables=dict(self.variables),
                secrets={},
                features=dict(self.features),
                stage_outputs=dict(self.stage_outputs),
                stage_outputs_sensitive={},
                errors=list(self.errors),
                variable_notes=dict(self.variable_notes),
                secret_notes={},
                feature_notes=dict(self.feature_notes),
            )

        if allowed_secrets == ["*"]:
            return ResolvedValues(
                variables=dict(self.variables),
                secrets=dict(self.secrets),
                features=dict(self.features),
                stage_outputs=dict(self.stage_outputs),
                stage_outputs_sensitive=dict(self.stage_outputs_sensitive),
                errors=list(self.errors),
                variable_notes=dict(self.variable_notes),
                secret_notes=dict(self.secret_notes),
                feature_notes=dict(self.feature_notes),
            )

        allowed = set(allowed_secrets)
        return ResolvedValues(
            variables=dict(self.variables),
            secrets={k: v for k, v in self.secrets.items() if k in allowed},
            features=dict(self.features),
            stage_outputs=dict(self.stage_outputs),
            stage_outputs_sensitive={k: v for k, v in self.stage_outputs_sensitive.items() if k in allowed},
            errors=list(self.errors),
            variable_notes=dict(self.variable_notes),
            secret_notes={k: v for k, v in self.secret_notes.items() if k in allowed},
            feature_notes=dict(self.feature_notes),
        )

    def debug_summary(self) -> Dict[str, Any]:
        """Return a debug-safe representation of STRATA_CONTEXT and STRATA_SENSITIVE.

        STRATA_CONTEXT (variables, features, stage_outputs) — values shown as-is.
        STRATA_SENSITIVE (secrets, stage_outputs_sensitive) — keys listed, values masked as '***'.
        Safe to pass to structured logging; never write to stdout without --verbose.
        """
        return {
            "strata_context": {
                "variables": dict(self.variables),
                "features": {k: v for k, v in self.features.items() if v is not None},
                "stage_outputs": dict(self.stage_outputs),
            },
            "strata_sensitive": {
                "secrets": {k: "***" for k in self.secrets},
                "stage_outputs_sensitive": {k: "***" for k in self.stage_outputs_sensitive},
            },
        }

    def as_compose_env(self) -> Dict[str, str]:
        """Return all resolved values as a flat env-var dict for Docker Compose ``${KEY}`` substitution.

        Key names are used verbatim (no prefix) — Docker reads them directly from
        the process environment.

        Merge order (later entry wins on key collision):
          features → variables → secrets → stage_outputs

        Feature flags with a ``None`` value are skipped.
        Secrets are included; callers should avoid logging this dict.
        """
        result: Dict[str, str] = {}
        for key, val in self.features.items():
            if val is not None:
                result[key] = str(val).lower()  # "true" / "false"
        for key, val in self.variables.items():
            result[key] = str(val)
        for key, val in self.secrets.items():
            result[key] = str(val)
        for key, val in self.stage_outputs.items():
            if val is not None:
                result[key] = json.dumps(val) if isinstance(val, (dict, list)) else str(val)
        return result

    def as_tf_vars(self) -> Dict[str, str]:
        """Return all resolved values as a ``TF_VAR_*`` environment-variable dict.

        Intended for injection into subprocess calls (e.g. terraform init/plan/apply).
        Variables and features are considered non-sensitive; secrets are included
        but callers should avoid logging this dict.

        Naming:
          - variables     → TF_VAR_<key>
          - secrets       → TF_VAR_<key>
          - features      → TF_VAR_<key>  (bool serialised as "true" / "false")
          - stage_outputs → TF_VAR_<key>  (complex values JSON-encoded)
        """
        result: Dict[str, str] = {}
        for key, val in self.variables.items():
            result[f"TF_VAR_{key}"] = str(val)
        for key, val in self.features.items():
            if val is not None:
                result[f"TF_VAR_{key}"] = str(val).lower()  # "true" / "false"
        for key, val in self.secrets.items():
            result[f"TF_VAR_{key}"] = str(val)
        for key, val in self.stage_outputs.items():
            if val is not None:
                result[f"TF_VAR_{key}"] = json.dumps(val) if isinstance(val, (dict, list)) else str(val)
        return result


@contextmanager
def inject_compose_env(resolved: Optional[ResolvedValues]) -> Generator[None, None, None]:
    """Context manager that injects compose env vars into ``os.environ`` and removes them on exit.

    Unlike ``inject_tf_vars``, keys are used verbatim (no ``TF_VAR_`` prefix).
    Accepts ``None`` for convenience — behaves as a no-op when ``resolved`` is ``None``
    or when ``as_compose_env()`` returns an empty dict.
    """
    env_vars = resolved.as_compose_env() if resolved else {}
    if not env_vars:
        yield
        return

    prev: Dict[str, Optional[str]] = {}
    for key, val in env_vars.items():
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
