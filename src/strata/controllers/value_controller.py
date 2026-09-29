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

from typing import Any, cast

from strata.controllers.deployment_resolution import resolve_deployment_chains
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_references import resolve_document_value_references
from strata.integrations.capabilities import StoreIntegration
from strata.integrations.errors import ValueResolutionError
from strata.integrations.registry import IntegrationNotFoundError
from strata.integrations.registry import get as get_integration
from strata.integrations.resolved_context import ValueReference, ValueResolution
from strata.models.artifact_model import ArtifactModel
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
from strata.models.version_model import VersionModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.environment_service import merge_environment_models
from strata.utils.dict_merge import deep_merge
from strata.utils.errors import UsageError

#: Store types resolved without any integration — read directly.
_CONSTANT_TYPES = {VariableStoreType.CONSTANT, SecretStoreType.CONSTANT, FeatureStoreType.CONSTANT}
_ENVIRONMENT_TYPES = {VariableStoreType.ENVIRONMENT, SecretStoreType.ENVIRONMENT, FeatureStoreType.ENVIRONMENT}


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


def resolve_deployment(context: SolutionContext, deployment_name: str) -> DeploymentModel:
    """Find `deployment_name` and fold in its `extends`/tenant-defaults chain.

    Shared by `resolve_values()` (this module) and `build_controller.build_run()`
    (which needs the same resolved `spec.workspace` - a workspace can itself
    be inherited via `extends`, so a caller doing its own raw
    `index.get(DEPLOYMENT, ...)` lookup could silently disagree with what
    `resolve_values()` used).

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
    return resolved_deployments.get(deployment_name, cast(DeploymentModel, entry.model))


def resolve_tenant(context: SolutionContext, deployment: DeploymentModel) -> TenantModel | None:
    """The `TenantModel` `deployment.spec.tenant` names, or `None` if unset
    or unresolvable (docs/design/build-command.md's `tenant` Terraform
    category).

    `deployment.spec.tenant` survives `resolve_deployment()`'s `extends`/
    tenant-defaults-merge chain unchanged - `merge_deployment_specs()` only
    consumes `partial`/`extends`, never `tenant` - so reading it directly
    off an already-`resolve_deployment()`-resolved model is safe and needs
    no re-derivation. Mirrors `deployment_resolution._merge_tenant_defaults()`'s
    own lookup exactly, just exposed as a public, reusable result instead of
    being folded into a merged dict and discarded.

    A missing/unresolvable `tenant` reference silently returns `None`, same
    treatment `_merge_tenant_defaults()` gives it - `validate_references` is
    the layer that reports a bad `spec.tenant`, not this function.
    """
    if not deployment.spec.tenant:
        return None
    entry = context.controller.index.get(PlatformKind.TENANT, deployment.spec.tenant)
    if entry is None:
        return None
    return cast(TenantModel, entry.model)


def resolve_artifact(context: SolutionContext, artifact_name: str) -> ArtifactModel | None:
    """The `ArtifactModel` named `artifact_name`, or `None` if unresolvable
    (docs/design/artifact-references.md).

    A missing artifact reference silently returns `None` — `validate_references`
    is the layer that reports a bad reference, not this function, matching
    `resolve_tenant()`'s identical treatment.
    """
    entry = context.controller.index.get(PlatformKind.ARTIFACT, artifact_name)
    if entry is None:
        return None
    return cast(ArtifactModel, entry.model)


def resolve_artifact_field(
    context: SolutionContext, deployment: DeploymentModel, artifact_name: str, field: str
) -> str | None:
    """Resolve one `(artifact_name, field)` pair to a plain string
    (docs/design/artifact-references.md's `store: artifact` mechanism).

    `field` is one of `image_name`/`image_tag`/`image_ref`:

    - `image_name` is identity — read straight off the artifact document,
      never pin-overlaid (matches `chart_name`'s treatment — a pin never
      changes what's being pinned, only its version).
    - `image_tag` consults `deployment.spec.version` -> `VersionModel.spec.
      pins.artifacts[artifact_name]` first; falls back to the artifact's
      own declared `image_tag` when unset or no pin exists.
    - `image_ref` synthesises `"{image_name}:{image_tag}"` (or bare
      `image_name` when the tag is blank/unset) — adminapp's real combined
      Terraform variable shape (docs/design/artifact-references.md).

    Returns `None` when `artifact_name` doesn't resolve — same
    silently-return-None treatment as `resolve_artifact()`/`resolve_tenant()`.
    """
    artifact = resolve_artifact(context, artifact_name)
    if artifact is None:
        return None
    if field == "image_name":
        return artifact.spec.image_name

    image_tag = artifact.spec.image_tag
    if deployment.spec.version:
        version_entry = context.controller.index.get(PlatformKind.VERSION, deployment.spec.version)
        if version_entry is not None:
            version = cast(VersionModel, version_entry.model)
            pin = (version.spec.pins.artifacts or {}).get(artifact_name)
            if pin is not None:
                image_tag = pin.version

    if field == "image_tag":
        return image_tag
    # field == "image_ref": synthesised, never a stored field.
    if not image_tag:
        return artifact.spec.image_name
    return f"{artifact.spec.image_name}:{image_tag}"


def reachable_environments(context: SolutionContext, deployment: DeploymentModel) -> list[EnvironmentModel]:
    """Every `EnvironmentModel` `deployment.spec.environments` names.

    Tenant environments are already folded in ahead of the deployment's own
    (ADR-0024's `_merge_tenant_defaults()`, inside `resolve_deployment_chains()`)
    - no separate tenant lookup needed here.

    Public (not `_`-prefixed): `build_controller.py` calls this directly
    (docs/design/build-time-value-categories.md, Q4) to build
    `ResolvedWorkspaceGraph`'s `variable_refs`/`feature_refs`/`secret_refs`/
    `properties`/`custom` fields, alongside `resolve_values()`'s own use of
    it - both must agree on which environments are in scope.
    """
    index = context.controller.index
    environments: list[EnvironmentModel] = []
    for name in deployment.spec.environments or []:
        environment_entry = index.get(PlatformKind.ENVIRONMENT, name)
        if environment_entry is not None:
            environments.append(cast(EnvironmentModel, environment_entry.model))
    return environments


def _coerce_feature_value(raw: Any) -> bool:
    """v1 precedent (`_build_feature_flags_vars`): bool passthrough; a string
    is checked against a falsy set case-insensitively; anything else via
    `bool()`.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.lower() not in ("false", "0", "no", "")
    return bool(raw)


def build_value_references(
    environments: list[EnvironmentModel],
    *,
    context: SolutionContext | None = None,
    deployment: DeploymentModel | None = None,
) -> tuple[list[ValueReference], list[ValueReference], list[ValueReference]]:
    """Build `(variable_refs, feature_refs, secret_refs)` for
    `ResolvedWorkspaceGraph` (docs/design/build-time-value-categories.md, Q1/Q4).

    Bypasses `ValueResolution` entirely, mirroring v1's real
    `_build_feature_flags_vars()`/`_build_flat_variables()`: reads store
    definitions directly rather than resolving through `resolve_values()`.
    `value` is populated **only** for `constant` (literal passthrough - no
    casting, `VariableStoreModel.value`/`FeatureStoreModel.value` are not
    cross-validated against `type` in v2 either), `environment` (a local
    `os.environ` read - not network I/O), and `artifact` (an in-solution
    document lookup, docs/design/artifact-references.md - variables only,
    never features/secrets) stores. Every other store type, and every
    secret regardless of store type, gets `value=None` - structurally,
    never resolved here (Q5: integration-backed resolution is deploy's
    job, not build's). `artifact` clears this bar for the same reason
    `constant`/`environment` do: no external system involved, fully known
    at build time.

    `context`/`deployment` are optional and keyword-only, matching the
    other `graph`-assembly kwargs' pattern (docs/design/
    build-time-value-categories.md) - omitted, a `store: artifact`
    variable's `value` is simply `None` (no context to resolve against),
    same treatment integration-backed stores already get.
    """
    from os import environ

    variables, secrets, features = merge_environment_models(environments)

    def _value_for(
        store_type: VariableStoreType | FeatureStoreType | SecretStoreType, raw: Any, *, is_feature: bool
    ) -> Any:
        if store_type in _CONSTANT_TYPES:
            return _coerce_feature_value(raw) if is_feature else raw
        if store_type in _ENVIRONMENT_TYPES:
            env_val = environ.get(str(raw))
            if env_val is None:
                return None
            return _coerce_feature_value(env_val) if is_feature else env_val
        return None

    def _variable_value(store: VariableStoreModel) -> Any:
        if store.store == VariableStoreType.ARTIFACT:
            if context is None or deployment is None or store.field is None:
                return None
            return resolve_artifact_field(context, deployment, str(store.value), store.field)
        return _value_for(store.store, store.value, is_feature=False)

    variable_refs = [
        ValueReference(
            key=key,
            store=store.store.value,
            description=store.description,
            value_type=store.type,
            value=_variable_value(store),
        )
        for key, store in variables.items()
    ]
    feature_refs = [
        ValueReference(
            key=key,
            store=store.store.value,
            description=store.description,
            value=_value_for(store.store, store.value, is_feature=True),
        )
        for key, store in features.items()
    ]
    secret_refs = [
        ValueReference(key=key, store=store.store.value, description=store.description)
        for key, store in secrets.items()
    ]
    return variable_refs, feature_refs, secret_refs


def merge_workspace_environment_deployment_properties(
    workspace: WorkspaceModel,
    environments: list[EnvironmentModel],
    deployment: DeploymentModel,
    source: str,
) -> dict[str, Any]:
    """Deep-merge `{source}` (`"properties"` or `"custom"`):
    `workspace.spec.{source}` -> each reachable `environment.spec.{source}`
    in order -> `deployment.spec.{source}` (docs/design/
    build-time-value-categories.md, Q3 - a deliberate improvement over v1's
    real, docstring-contradicting behaviour, which never merges the
    deployment's own value at all).

    No `overrides.{source}` step, unlike v1: `EnvironmentSpecModel` has no
    `overrides` field in v2 at all - confirmed via its own module docstring,
    deliberately not ported (0 of 26 real environment documents used it,
    ADR-0003's minimal-slice policy).
    """
    result: dict[str, Any] = dict(getattr(workspace.spec, source, None) or {})
    for environment in environments:
        result = deep_merge(result, getattr(environment.spec, source, None) or {})
    result = deep_merge(result, getattr(deployment.spec, source, None) or {})
    return result


def resolve_values(context: SolutionContext, deployment_name: str, keys: list[str]) -> ValueResolution:
    """Resolve `keys` against `deployment_name`'s merged environment(s).

    `store: artifact` variables are resolved directly against the
    solution's own `ArtifactModel` + `kind: version` pins
    (`resolve_artifact_field()`) — the same mechanism
    `build_value_references()`'s `_variable_value()` already uses at build
    time (docs/design/deploy-command.md's Remaining Work item 8, resolved
    2026-09-27) — never dispatched to `_resolve_store_value()`'s
    `StoreIntegration` lookup, since `artifact` is not, and never will be,
    a registered integration (it resolves an in-solution document
    reference, not an external system).

    Also always merges every `${value:kind.name.path}` cross-document
    reference found anywhere in the whole solution (docs/design/
    cross-document-value-references.md's Phase 4) —
    `resolve_document_value_references()`'s output, unconditionally, not
    filtered by or dependent on `keys` at all. This is deliberately
    different from every `keys` entry above: a `${value:...}` token is
    never declared in an Environment the way `var`/`secret`/`feature` are
    (it names its own target directly via `(kind, name)` document
    identity, ADR-0015), so callers never pass one in `keys` — the
    resolved deployment-wide `all_keys` union `deploy_controller.py`
    builds only ever contains declared var/secret/feature names. Merging
    it here regardless of `keys`/`deployment_name` is what makes it
    reachable at all: `ValueResolution.values` is the one flat map every
    downstream consumer (`resolve_value_tokens()`'s per-key lookup,
    Terraform `TF_VAR_`/Helm/Compose delivery) already reads uniformly,
    blind to which mechanism produced an entry.

    Args:
        context: An already-`require_valid()`-ed solution.
        deployment_name: `meta.name` of the deployment to resolve values for.
        keys: The variable/secret/feature keys to look up.

    Returns:
        Every key in `keys`, either in `.values` or as a finding in
        `.diagnostics` explaining why it did not resolve — plus every
        resolved `${value:...}` cross-document reference found anywhere in
        the solution, and a `.diagnostics` entry for every one that did
        not resolve.

    Raises:
        UsageError: `deployment_name` does not name a real deployment.
    """
    deployment = resolve_deployment(context, deployment_name)
    environments = reachable_environments(context, deployment)

    variables, secrets, features = merge_environment_models(environments)
    resolvers = _Resolvers()
    result = ValueResolution(deployment=deployment_name)

    value_reference_values, value_reference_diagnostics = resolve_document_value_references(context.controller.index)
    result.values.update(value_reference_values)
    result.diagnostics.extend(value_reference_diagnostics)

    for key in keys:
        store = secrets.get(key) or features.get(key) or variables.get(key)
        if store is None:
            result.diagnostics.error(
                f"'{key}' is not declared in any environment reachable from deployment '{deployment_name}'.",
                location=key,
                code="unknown_value_key",
            )
            continue
        if isinstance(store, VariableStoreModel) and store.store == VariableStoreType.ARTIFACT:
            # `field` is guaranteed set — `VariableStoreModel.
            # validate_field_only_on_artifact_store()` requires it whenever
            # `store == artifact`.
            assert store.field is not None
            artifact_value = resolve_artifact_field(context, deployment, str(store.value), store.field)
            if artifact_value is None:
                result.diagnostics.error(
                    f"'{key}': 'store: artifact' references '{store.value}', which is not a "
                    "known artifact in this solution.",
                    location=key,
                    code="value_resolution_failed",
                )
                continue
            result.values[key] = artifact_value
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
