"""Resolves variables, secrets, and feature flags from configured store integrations into concrete runtime values."""

import os
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from strata.controllers.base_controller import BaseController
from strata.exceptions import SecretStoreUnavailableError
from strata.logger import get_logger
from strata.models.store_models import (
    FeatureStoreModel,
    FeatureStoreType,
    SecretRotatePolicy,
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
            (success, ResolvedValues, error_messages). ``success`` is always
            False when any store was unreachable/unauthenticated
            (``resolved.store_unavailable_errors``), regardless of ``strict`` —
            proceeding on an ambiguous store failure risks silently generating
            or overwriting secrets, or deploying with blank values.
        """
        resolved = ResolvedValues()

        environment_service = deployment_service.get_environment_service()
        if environment_service is None:
            logger.warning("No environment service attached to deployment — no variables/secrets/features to resolve.")
            return True, resolved, []

        # Attach provenance from the merge step (None for single-file deployments)
        provenance = deployment_service.get_merge_provenance()
        if provenance is not None:
            resolved.variable_sources = dict(provenance.variable_sources)
            resolved.secret_sources = dict(provenance.secret_sources)
            resolved.feature_sources = dict(provenance.feature_sources)
            resolved.merge_order = list(provenance.merge_order)

        # Lazy-init integrations once (idempotent; no-op if already done).
        self._ensure_integrations_initialized()

        # Preflight: confirm every store integration actually referenced by this
        # deployment is reachable/authenticated BEFORE resolving any individual
        # value. This fails fast on a single, deduplicated check per store
        # instead of discovering the same outage once per item (and potentially
        # after other items already resolved / secrets already generated).
        preflight_errors = self._preflight_check_stores(environment_service)
        if preflight_errors:
            resolved.errors.extend(preflight_errors)
            resolved.store_unavailable_errors.extend(preflight_errors)
            logger.error(
                "Aborting value resolution — required store(s) unavailable",
                errors=preflight_errors,
            )
            return False, resolved, resolved.errors

        # --- variables ---
        for item in environment_service.get_variables():
            try:
                val, err, note = self._resolve_variable(item)
            except SecretStoreUnavailableError as exc:
                msg = f"Variable '{item.key}': {exc}"
                resolved.errors.append(msg)
                resolved.store_unavailable_errors.append(msg)
                continue
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
            try:
                val, err, note = self._resolve_secret(secret_item)
            except SecretStoreUnavailableError as exc:
                msg = f"Secret '{secret_item.key}': {exc}"
                resolved.errors.append(msg)
                resolved.store_unavailable_errors.append(msg)
                continue
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
            try:
                val, err, note = self._resolve_feature(feature_item)
            except SecretStoreUnavailableError as exc:
                msg = f"Feature '{feature_item.key}': {exc}"
                resolved.errors.append(msg)
                resolved.store_unavailable_errors.append(msg)
                continue
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
            store_unavailable=len(resolved.store_unavailable_errors),
        )

        # A store-unavailable failure is always fatal — proceeding with a deploy
        # when a secret store could not be confirmed reachable risks silently
        # generating/overwriting secrets or deploying with blank values. This
        # overrides `strict` in both directions: never downgraded to a warning.
        success = (len(resolved.errors) == 0 if strict else True) and not resolved.store_unavailable_errors
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
        # Existing value found — check rotation policy
        return self._check_rotation(item, val, integration)

    def _check_rotation(
        self,
        item: SecretStoreModel,
        current_value: str,
        integration,
    ) -> Tuple[str, Optional[str], Optional[str]]:
        """Apply rotation policy to an existing secret.

        Returns (value, error_or_None, note_or_None).  When policy is ``warn``
        the original value is returned with a ``rotation_advisory`` note.
        When policy is ``rotate`` a new value is generated and written back.
        """
        if item.rotate is None:
            return current_value, None, None

        meta = integration.get_secret_metadata(str(item.value))
        if meta is None:
            logger.debug(
                "Cannot determine secret age — rotation check skipped",
                key=item.key,
                store=item.store.value,
            )
            return current_value, None, None

        reference_time = meta.updated_at or meta.created_at
        if reference_time is None:
            return current_value, None, None

        age_days = (datetime.now(timezone.utc) - reference_time).days
        if age_days < item.rotate.max_age:
            return current_value, None, None

        # Secret is overdue
        if item.rotate.policy == SecretRotatePolicy.WARN:
            logger.warning(
                "Secret exceeds max age — rotation advisory",
                key=item.key,
                store=item.store.value,
                age_days=age_days,
                max_age=item.rotate.max_age,
            )
            return current_value, None, f"rotation_advisory:{age_days}d/{item.rotate.max_age}d"

        # policy == ROTATE — requires generate spec (enforced by model validator)
        assert item.generate is not None  # guaranteed by validate_rotate_policy_requires_generate
        new_value = generate_secret(item.generate.type.value, item.generate.length)
        ok = integration.update_secret(str(item.value), new_value)
        if not ok:
            logger.error(
                "Auto-rotation failed — store write rejected",
                key=item.key,
                store=item.store.value,
            )
            return current_value, None, f"rotation_failed:{age_days}d/{item.rotate.max_age}d"

        logger.info(
            "Secret auto-rotated",
            action="secret_rotated",
            key=str(item.value),
            store=item.store.value,
            age_days=age_days,
            max_age=item.rotate.max_age,
        )
        return new_value, None, f"rotated:{age_days}d/{item.rotate.max_age}d"

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

    def _preflight_check_stores(self, environment_service) -> List[str]:
        """Confirm availability of every distinct integration-backed store
        referenced by this deployment's variables/secrets/features.

        Stores that don't need an integration (``constant``, ``environment``,
        ``github``) are skipped. Each distinct, registered store type is
        checked exactly once via ``ensure_available()`` regardless of how many
        items reference it. Stores that aren't registered at all are left for
        the existing per-item resolution to report (a configuration error, not
        an availability one — respects ``strict`` as before).

        Returns:
            List of error messages for any referenced store confirmed
            unavailable (empty if all referenced, registered stores are up).
        """
        store_types: set = set()
        for item in environment_service.get_variables():
            if item.store not in (VariableStoreType.CONSTANT, VariableStoreType.ENVIRONMENT):
                store_types.add(item.store.value)
        for item in environment_service.get_secrets():
            if item.store not in (SecretStoreType.CONSTANT, SecretStoreType.ENVIRONMENT, SecretStoreType.GITHUB):
                store_types.add(item.store.value)
        for item in environment_service.get_features():
            if item.store not in (FeatureStoreType.CONSTANT, FeatureStoreType.ENVIRONMENT):
                store_types.add(item.store.value)

        errors: List[str] = []
        for store_type in sorted(store_types):
            integration = self._get_integration_by_type(store_type)
            if integration is None:
                # Not registered at all — surfaced per-item later as a
                # configuration error, not an "unavailable" one.
                continue
            available, reason = integration.ensure_available()
            if not available:
                errors.append(f"Store '{store_type}' required by this deployment is unavailable: {reason}")
        return errors

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
