#!/usr/bin/env python3
"""Resolves runtime values (variables/secrets/features) for a deployment.

The controller layer, not the service layer, because it needs the whole
solution index — a deployment's environments are separate documents found
by name (ADR-0015), not inline content a single service can load alone.

**Precedence on a key declared in more than one store.** Matches v1's real
behaviour (`GetValuesDeployCommand._execute`): secrets win over features,
features win over variables. Unusual in practice — declaring the same key
in two different store kinds is presumably a mistake — but the behaviour
should be deterministic rather than dict-iteration-order-dependent.

**What "resolve" means here.** Every requested key falls into exactly one of
three outcomes: resolved (a value came back), *not declared* (no environment
reachable from this deployment names the key at all), or *resolution failed*
(declared, but the backing store could not produce a value — bad auth,
network failure, or a genuinely missing entry in that store). The caller
needs all three distinguished, since "not declared" and "failed" call for
different fixes.
"""

from dataclasses import dataclass, field
from typing import cast

from strata.controllers.deployment_resolution import resolve_deployment_chains
from strata.controllers.solution_context import SolutionContext
from strata.integrations.capabilities import StoreIntegration
from strata.integrations.errors import ValueResolutionError
from strata.integrations.registry import IntegrationNotFoundError
from strata.integrations.registry import get as get_integration
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.environment_model import EnvironmentModel
from strata.models.store_model import (
    FeatureStoreModel,
    FeatureStoreType,
    SecretStoreModel,
    SecretStoreType,
    VariableStoreModel,
    VariableStoreType,
)
from strata.models.tenant_model import TenantModel
from strata.services.environment_service import merge_environment_models
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError

#: Store types resolved without any integration — read directly.
_CONSTANT_TYPES = {VariableStoreType.CONSTANT, SecretStoreType.CONSTANT, FeatureStoreType.CONSTANT}
_ENVIRONMENT_TYPES = {VariableStoreType.ENVIRONMENT, SecretStoreType.ENVIRONMENT, FeatureStoreType.ENVIRONMENT}


@dataclass
class ValueResolution:
    """The outcome of resolving a set of requested keys."""

    deployment: str
    values: dict[str, str] = field(default_factory=dict)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)


class _Resolvers:
    """Lazily-constructed, reused across every key in one `resolve_values` call.

    Keyed by store **type** (`store.store.value`), not a declaration name —
    a store has nothing to name (`store: infisical`, no `integration:`
    reference field). This matches v1's own real behaviour, not just v2's
    simpler one: v1's `ValueController._get_integration_by_type` also
    dispatches by type, looping every *named* registered integration and
    returning the first match — so a v1 solution with two named Infisical
    integrations already got an arbitrary ("first in iteration order") one.
    Caching a single instance per type here is a more deterministic version
    of that same behaviour, not a weaker one (ADR-0021 D3 still applies as
    written to a caller that resolves a *named* Integration document — e.g.
    Phase 5's `ProvisionerModel.integration` binding).

    A resolver may hold a live client/bearer token — constructing one per
    key instead of reusing it across a `resolve_values()` call would mean
    re-authenticating once per key for no reason.
    """

    def __init__(self) -> None:
        self._instances: dict[str, StoreIntegration] = {}

    def get(self, integration_type: str) -> StoreIntegration:
        """Return the cached `StoreIntegration` for `integration_type`, constructing it on first use.

        Raises:
            IntegrationNotFoundError: No class is registered for `integration_type`.
            ValueResolutionError: A class is registered, but it isn't a `StoreIntegration`
                (e.g. an `InfraIntegration` sharing a type string — not possible today,
                guarded here for when the registry grows non-store types).
        """
        if integration_type not in self._instances:
            instance = get_integration(integration_type)
            if not isinstance(instance, StoreIntegration):
                raise ValueResolutionError(f"'{integration_type}' does not resolve values (not a store integration).")
            self._instances[integration_type] = instance
        return self._instances[integration_type]


def resolve_values(context: SolutionContext, deployment_name: str, keys: list[str]) -> ValueResolution:
    """Resolve `keys` against `deployment_name`'s merged environment(s).

    Args:
        context: An already-`require_valid()`-ed solution.
        deployment_name: `meta.name` of the deployment to resolve values for.
        keys: The variable/secret/feature keys to look up.

    Returns:
        Every key in `keys`, either in `.values` or as a finding in
        `.diagnostics` explaining why it did not resolve.

    Raises:
        UsageError: `deployment_name` does not name a real deployment.
    """
    index = context.controller.index
    entry = index.get(PlatformKind.DEPLOYMENT, deployment_name)
    if entry is None:
        raise UsageError(
            f"No deployment named '{deployment_name}'. Available: {sorted(index.names_of(PlatformKind.DEPLOYMENT))}"
        )

    resolved_deployments, _ = resolve_deployment_chains(index)
    deployment = resolved_deployments.get(deployment_name, cast(DeploymentModel, entry.model))

    environment_names: list[str] = []
    if deployment.spec.tenant:
        tenant_entry = index.get(PlatformKind.TENANT, deployment.spec.tenant)
        if tenant_entry is not None:
            tenant = cast(TenantModel, tenant_entry.model)
            environment_names.extend(tenant.spec.environments or [])
    environment_names.extend(deployment.spec.environments or [])

    environments: list[EnvironmentModel] = []
    for name in environment_names:
        environment_entry = index.get(PlatformKind.ENVIRONMENT, name)
        if environment_entry is not None:
            environments.append(cast(EnvironmentModel, environment_entry.model))

    variables, secrets, features = merge_environment_models(environments)
    resolvers = _Resolvers()
    result = ValueResolution(deployment=deployment_name)

    for key in keys:
        store = secrets.get(key) or features.get(key) or variables.get(key)
        if store is None:
            result.diagnostics.error(
                f"'{key}' is not declared in any environment reachable from deployment "
                f"'{deployment_name}'.",
                location=key,
                code="unknown_value_key",
            )
            continue
        try:
            value = _resolve_store_value(store, resolvers)
        except ValueResolutionError as exc:
            result.diagnostics.error(str(exc), location=key, code="value_resolution_failed")
            continue
        result.values[key] = value

    return result


def _resolve_store_value(
    store: VariableStoreModel | SecretStoreModel | FeatureStoreModel, resolvers: _Resolvers
) -> str:
    """Dispatch to the right backend for `store.store`, and return its value."""
    store_type = store.store
    if store_type in _CONSTANT_TYPES:
        value = str(store.value)
    elif store_type in _ENVIRONMENT_TYPES or store_type == SecretStoreType.GITHUB:
        from os import environ

        var_name = str(store.value)
        if var_name not in environ:
            raise ValueResolutionError(f"environment variable '{var_name}' is not set.")
        value = environ[var_name]
    else:
        try:
            integration = resolvers.get(store_type.value)
        except IntegrationNotFoundError as exc:
            raise ValueResolutionError(f"no resolver implemented yet for store '{store_type.value}'.") from exc
        value = integration.resolve(str(store.value))

    if isinstance(store, FeatureStoreModel):
        # v1 precedent: a feature always renders as a lowercase true/false/none string.
        value = "none" if value is None else str(value).lower()
    return value
