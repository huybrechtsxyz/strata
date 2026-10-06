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

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from sys import stdin
from typing import Any, cast

from strata.controllers.deployment_resolution import resolve_deployment_chains
from strata.controllers.integration_resolution import bind_integration_config
from strata.controllers.solution_context import SolutionContext
from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.value_references import resolve_document_value_references
from strata.controllers.version_pins import log_pin_applied
from strata.integrations.capabilities import StoreIntegration
from strata.integrations.errors import IntegrationError, ValueResolutionError
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
from strata.services.version_service import VersionService
from strata.utils.diagnostics import Diagnostics
from strata.utils.dict_merge import deep_merge
from strata.utils.errors import UsageError
from strata.utils.secret_generator import generate_secret, mask_secret
from strata.utils.value_tokens import has_value_tokens, resolve_value_tokens

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

    `index` (docs/design/store-integration-configuration.md, Phase 1):
    threaded through so `.get()` can look up a real `kind: integration`
    document by type before constructing — the exact same zero-or-one-
    enabled-match rule `integration_resolution.py`'s `bind_integration_config()`
    already uses for `InfraIntegration`, reused here rather than
    reimplemented, since the rule itself does not vary by capability tier.
    """

    def __init__(self, index: DocumentIndex) -> None:
        self._index = index
        self._instances: dict[str, StoreIntegration] = {}

    def get(self, integration_type: str) -> StoreIntegration:
        """Return the cached `StoreIntegration` for `integration_type`, constructing it on first use.

        Raises:
            IntegrationNotFoundError: No class is registered for `integration_type`.
            UsageError: More than one enabled `Integration` document declares
                `spec.type == integration_type` (`bind_integration_config()`'s
                own rule, reused unchanged here).
            ValueResolutionError: A class is registered, but it isn't a `StoreIntegration`
                (e.g. an `InfraIntegration` sharing a type string — not possible today,
                guarded here for when the registry grows non-store types).
        """
        if integration_type not in self._instances:
            config = bind_integration_config(
                self._index, integration_type, requester=f"Store type '{integration_type}'"
            )
            instance = get_integration(integration_type, config=config)
            if not isinstance(instance, StoreIntegration):
                raise ValueResolutionError(f"'{integration_type}' does not resolve values (not a store integration).")
            self._instances[integration_type] = instance
        return self._instances[integration_type]


def resolve_deployment(
    context: SolutionContext, deployment_name: str, *, version_pin: str | None = None
) -> DeploymentModel:
    """Find `deployment_name` and fold in its `extends`/tenant-defaults chain.

    Shared by `resolve_values()` (this module) and `build_controller.build_run()`
    (which needs the same resolved `spec.workspace` - a workspace can itself
    be inherited via `extends`, so a caller doing its own raw
    `index.get(DEPLOYMENT, ...)` lookup could silently disagree with what
    `resolve_values()` used).

    Args:
        context: The loaded solution.
        deployment_name: `meta.name` of the deployment to resolve.
        version_pin: `--pin` (docs/work/version-lifecycle.md Phase 5) — when
            given, overrides the resolved deployment's `spec.version` to this
            name for the returned model only, never written to disk. The
            one place this override happens: every real consumer
            (`resolve_version()`, `resolve_artifact_field()`, the build-time
            images/charts/remotes overlays) reads `spec.version` off the
            deployment object it was handed, so overriding it here makes
            every one of them see the pin transparently, with nothing
            else to thread it through.

    Raises:
        UsageError: `deployment_name` does not name a real deployment, or
            `version_pin` does not name an indexed Version document.
    """
    index = context.controller.index
    entry = index.get(PlatformKind.DEPLOYMENT, deployment_name)
    if entry is None:
        raise UsageError(
            f"No deployment named '{deployment_name}'. Available: {sorted(index.names_of(PlatformKind.DEPLOYMENT))}"
        )
    resolved_deployments, _ = resolve_deployment_chains(index)
    deployment = resolved_deployments.get(deployment_name, cast(DeploymentModel, entry.model))

    if version_pin is not None:
        if index.get(PlatformKind.VERSION, version_pin) is None:
            raise UsageError(f"--pin names an unknown version document: '{version_pin}'.")
        deployment = deployment.model_copy(update={"spec": deployment.spec.model_copy(update={"version": version_pin})})

    return deployment


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


def resolve_version(context: SolutionContext, deployment: DeploymentModel) -> VersionModel | None:
    """The `VersionModel` `deployment.spec.version` names, or `None` if unset
    or unresolvable (docs/design/version-pin-overlay.md).

    Same "silently return None" treatment as `resolve_tenant()` immediately
    above — `validate_references` is the layer that reports a bad
    `spec.version`, not this function.
    """
    if not deployment.spec.version:
        return None
    entry = context.controller.index.get(PlatformKind.VERSION, deployment.spec.version)
    if entry is None:
        return None
    return cast(VersionModel, entry.model)


def resolve_artifact(context: SolutionContext, artifact_name: str) -> ArtifactModel | None:
    """The `ArtifactModel` named `artifact_name`, or `None` if unresolvable
    (docs/work/artifact-references.md).

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
    (docs/work/artifact-references.md's `store: artifact` mechanism).

    `field` is one of `image_name`/`image_tag`/`image_ref`:

    - `image_name` is identity — read straight off the artifact document,
      never pin-overlaid (matches `chart_name`'s treatment — a pin never
      changes what's being pinned, only its version).
    - `image_tag` consults `deployment.spec.version` -> `VersionModel.spec.
      pins.artifacts[artifact_name]` first (via `VersionService.resolve()`,
      the single shared pin lookup every overlay category uses,
      docs/design/version-pin-overlay.md); falls back to the artifact's
      own declared `image_tag` when unset or no pin exists. Every
      application is logged (ADR-0019 decision 7).
    - `image_ref` synthesises `"{image_name}:{image_tag}"` (or bare
      `image_name` when the tag is blank/unset) — adminapp's real combined
      Terraform variable shape (docs/work/artifact-references.md).

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
            pin = VersionService.from_model(version).resolve("artifacts", artifact_name)
            if pin is not None:
                log_pin_applied("artifacts", artifact_name, image_tag, pin.version, version.meta.name)
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
    document lookup, docs/work/artifact-references.md - variables only,
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


def resolve_values(
    context: SolutionContext, deployment_name: str, keys: list[str], *, version_pin: str | None = None
) -> ValueResolution:
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
        version_pin: `--pin` (docs/work/version-lifecycle.md Phase 5) —
            forwarded to `resolve_deployment()`, so a `store: artifact`
            key's `resolve_artifact_field()` lookup sees the pinned
            version too, not just `build_run()`'s own images/charts/
            remotes overlays.

    Returns:
        Every key in `keys`, either in `.values` or as a finding in
        `.diagnostics` explaining why it did not resolve — plus every
        resolved `${value:...}` cross-document reference found anywhere in
        the solution, and a `.diagnostics` entry for every one that did
        not resolve.

    Raises:
        UsageError: `deployment_name` does not name a real deployment, or
            `version_pin` does not name an indexed Version document.
    """
    deployment = resolve_deployment(context, deployment_name, version_pin=version_pin)
    environments = reachable_environments(context, deployment)

    variables, secrets, features = merge_environment_models(environments)
    resolvers = _Resolvers(context.controller.index)
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
            value = _resolve_store_value(store, resolvers, value_reference_values)
        except ValueResolutionError as exc:
            result.diagnostics.error(str(exc), location=key, code="value_resolution_failed")
            continue
        result.values[key] = value

    return result


def _resolve_store_value(
    store: VariableStoreModel | SecretStoreModel | FeatureStoreModel,
    resolvers: _Resolvers,
    value_reference_values: dict[str, str],
) -> str:
    """Dispatch to the right backend for `store.store`, and return its value.

    `value_reference_values` is `resolve_document_value_references()`'s own
    output (docs/design/cross-document-value-references.md) — already
    fully resolved, with no `${...}` token of its own by construction (that
    design's own "cycles are impossible" guarantee), so it's always safe to
    resolve a `store: constant` value against regardless of which order
    `keys` happens to be processed in. Fixes a real, previously-documented
    gap: a constant's own `value` field was never checked for `${value:...}`
    (or any token) at all — it passed `strata validate` cleanly (the
    generic whole-document walk already saw it) but then reached every
    consumer as the literal, unresolved token string.
    """
    store_type = store.store
    if store_type in _CONSTANT_TYPES:
        value = str(store.value)
        if has_value_tokens(value):
            try:
                value = resolve_value_tokens(value, value_reference_values)
            except ValueError as exc:
                raise ValueResolutionError(str(exc)) from exc
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


#: `values list --type` choices — matches v1's `--type variables|secrets|features`.
VALUE_TYPE_FILTERS = ("variables", "secrets", "features")


@dataclass(frozen=True)
class DeclaredValueRow:
    """One declared variable/secret/feature, with no store I/O performed
    (`values list --declared-only`, docs/design/values-secrets-command.md
    Phase 2).

    `generate`/`rotate` are only ever set for a secret (the only kind with
    those spec fields, `store_model.SecretStoreModel`) — always `None` for a
    variable/feature row.
    """

    key: str
    kind: str
    """One of "variable"/"secret"/"feature"."""
    store: str
    value_ref: str | None
    generate: str | None = None
    """`"{type}/{length}"` when the secret has a `generate:` spec, else `None`."""
    rotate: str | None = None
    """`"{max_age}d/{policy}"` when the secret has a `rotate:` spec, else `None`."""


def _declared_variable_row(item: VariableStoreModel) -> DeclaredValueRow:
    return DeclaredValueRow(
        key=item.key, kind="variable", store=item.store.value, value_ref=None if item.value is None else str(item.value)
    )


def _declared_secret_row(item: SecretStoreModel) -> DeclaredValueRow:
    return DeclaredValueRow(
        key=item.key,
        kind="secret",
        store=item.store.value,
        value_ref=None if item.value is None else str(item.value),
        generate=f"{item.generate.type.value}/{item.generate.length}" if item.generate else None,
        rotate=f"{item.rotate.max_age}d/{item.rotate.policy.value}" if item.rotate else None,
    )


def _declared_feature_row(item: FeatureStoreModel) -> DeclaredValueRow:
    return DeclaredValueRow(
        key=item.key, kind="feature", store=item.store.value, value_ref=None if item.value is None else str(item.value)
    )


def list_values(
    context: SolutionContext,
    deployment_name: str,
    *,
    type_filter: str | None = None,
    show_store: bool = False,
    unresolved_only: bool = False,
    declared_only: bool = False,
) -> tuple[list[DeclaredValueRow] | list["ValueListRow"], Diagnostics]:
    """List every variable/secret/feature reachable from `deployment_name`.

    Two modes, one function, matching `values list`'s own `--declared-only`
    flag (docs/design/values-secrets-command.md):

    - `declared_only=True` (Phase 2): skips all store I/O — reads
      `merge_environment_models(reachable_environments(...))` and returns
      one `DeclaredValueRow` per declared variable/secret/feature (store/
      value-ref/generate-or-rotate summary only), no `resolve()` call at
      all.
    - `declared_only=False` (Phase 4, the default — v1's real `values list`
      behavior): additionally resolves every declared key exactly like
      `resolve_values()` does per requested key, masking secret values
      (`mask_secret()`) and leaving variables/features in full, returning
      one `ValueListRow` per key. `show_store`/`unresolved_only` only apply
      to this mode (meaningless without a resolved value) — `show_store`
      is console-rendering-only (`values_command.py`'s job; both modes'
      rows always carry `store` regardless), `unresolved_only` filters the
      returned rows to failures only.

    `--trace` (v1's merge-provenance display) is **not** built in either
    mode — v2 has no merge-provenance tracking at all yet (confirmed: no
    `deployment_service.get_merge_provenance()`-equivalent exists;
    `merge_environment_models()` returns only the final winning store per
    key, not which environment file it came from). Adding `--trace` needs
    that tracking built first, a separate effort from "reuse `resolve()`
    per key" — out of scope here.

    Args:
        context: An already-`require_valid()`-ed solution.
        deployment_name: `meta.name` of the deployment to list values for.
        type_filter: Restrict to one of `VALUE_TYPE_FILTERS`, or `None` for
            all three kinds.
        show_store: Live mode only — passed through unused by this
            function; kept as a parameter so callers/tests can pass every
            CLI flag uniformly. Console rendering (whether the `store`
            column is shown) is `values_command.py`'s concern, not this
            function's — every row always carries `store`, live or declared.
        unresolved_only: Live mode only — keep only rows that failed to
            resolve.
        declared_only: `True` for the offline Phase 2 path; `False`
            (default) for the live Phase 4 path.

    Returns:
        `(rows, Diagnostics)`, sorted by `(kind, key)` for stable output.
        In declared-only mode, `Diagnostics` is always empty — nothing is
        resolved, so nothing can fail to resolve. In live mode, one
        `Diagnostic` error per key that failed to resolve (D4: no
        whole-batch preflight — the same uniform per-key failure model
        `values get`/`values resolve` already use).

    Raises:
        UsageError: `deployment_name` does not name a real deployment (via
            `resolve_deployment()`).
    """
    del show_store  # console-rendering-only, see docstring
    deployment = resolve_deployment(context, deployment_name)
    environments = reachable_environments(context, deployment)
    variables, secrets, features = merge_environment_models(environments)

    if declared_only:
        rows: list[DeclaredValueRow] = []
        if type_filter in (None, "variables"):
            rows.extend(_declared_variable_row(item) for item in variables.values())
        if type_filter in (None, "secrets"):
            rows.extend(_declared_secret_row(item) for item in secrets.values())
        if type_filter in (None, "features"):
            rows.extend(_declared_feature_row(item) for item in features.values())
        rows.sort(key=lambda row: (row.kind, row.key))
        return rows, Diagnostics()

    return _list_values_live(
        context, deployment, variables, secrets, features, type_filter=type_filter, unresolved_only=unresolved_only
    )


@dataclass(frozen=True)
class ValueListRow:
    """One resolved variable/secret/feature (`values list`, live mode —
    Phase 4). Secrets always masked (`mask_secret()`); variables/features
    shown in full — same per-kind treatment v1's real `values list` used.

    `value` is `None` exactly when `ok` is `False` — a failed row carries
    its failure `reason` instead (also reported as a `Diagnostic` on the
    same call, matching `values get`/`values resolve`'s per-key model).
    """

    key: str
    kind: str
    """One of "variable"/"secret"/"feature"."""
    store: str
    value: str | None
    ok: bool
    reason: str | None = None


def _list_values_live(
    context: SolutionContext,
    deployment: DeploymentModel,
    variables: dict[str, VariableStoreModel],
    secrets: dict[str, SecretStoreModel],
    features: dict[str, FeatureStoreModel],
    *,
    type_filter: str | None,
    unresolved_only: bool,
) -> tuple[list[ValueListRow], Diagnostics]:
    """Live-resolve every declared key, reusing the exact same per-key
    resolution `resolve_values()` uses (`_resolve_store_value()`,
    `store: artifact`'s direct dispatch) — see `list_values()` for the
    public contract.
    """
    resolvers = _Resolvers(context.controller.index)
    diagnostics = Diagnostics()
    value_reference_values, _ = resolve_document_value_references(context.controller.index)

    items: list[tuple[str, str, VariableStoreModel | SecretStoreModel | FeatureStoreModel]] = []
    if type_filter in (None, "variables"):
        items.extend(("variable", key, item) for key, item in variables.items())
    if type_filter in (None, "secrets"):
        items.extend(("secret", key, item) for key, item in secrets.items())
    if type_filter in (None, "features"):
        items.extend(("feature", key, item) for key, item in features.items())

    rows: list[ValueListRow] = []
    for kind, key, store in items:
        if isinstance(store, VariableStoreModel) and store.store == VariableStoreType.ARTIFACT:
            assert store.field is not None  # guaranteed by validate_field_only_on_artifact_store
            artifact_value = resolve_artifact_field(context, deployment, str(store.value), store.field)
            if artifact_value is None:
                reason = (
                    f"'store: artifact' references '{store.value}', which is not a known artifact in this solution."
                )
                diagnostics.error(f"'{key}': {reason}", location=key, code="value_resolution_failed")
                rows.append(ValueListRow(key=key, kind=kind, store="artifact", value=None, ok=False, reason=reason))
                continue
            rows.append(ValueListRow(key=key, kind=kind, store="artifact", value=artifact_value, ok=True))
            continue

        try:
            value = _resolve_store_value(store, resolvers, value_reference_values)
        except ValueResolutionError as exc:
            diagnostics.error(f"'{key}': {exc}", location=key, code="value_resolution_failed")
            rows.append(
                ValueListRow(key=key, kind=kind, store=store.store.value, value=None, ok=False, reason=str(exc))
            )
            continue

        rendered = mask_secret(value) if kind == "secret" else value
        rows.append(ValueListRow(key=key, kind=kind, store=store.store.value, value=rendered, ok=True))

    if unresolved_only:
        rows = [row for row in rows if not row.ok]
    rows.sort(key=lambda row: (row.kind, row.key))

    return rows, diagnostics


@dataclass(frozen=True)
class ResolveRow:
    """One key's resolution-path diagnostic (`values resolve`) — never
    carries a value, only whether it would resolve and why.

    `registered` is `True` for a built-in store (`constant`/`environment`/
    `github`) or `store: artifact` — there is no integration to register,
    so "registered" degenerates to "structurally resolvable" for those.
    `probed` is `True` only when `--probe` actually attempted a live
    `resolve()` call for this row (never for a built-in/artifact store,
    which are cheap/local and already fully checked either way).
    """

    key: str
    kind: str
    """One of "variable"/"secret"/"feature"."""
    store: str
    registered: bool
    probed: bool
    ok: bool
    reason: str | None = None


def resolve_diagnostic(
    context: SolutionContext,
    deployment_name: str,
    *,
    key: str | None = None,
    probe: bool = False,
) -> tuple[list[ResolveRow], Diagnostics]:
    """Diagnose whether declared keys would resolve, without revealing any value.

    `values resolve`'s controller function — `build run`'s missing
    pre-flight equivalent in v1 (its own docstring). For each in-scope key,
    checks:

    - **Store type** — `constant`/`environment`/`github` (built-in, no
      integration) and `store: artifact` (an in-solution document lookup,
      `resolve_artifact_field()` — same mechanism `resolve_values()` uses)
      are checked directly, cheaply, always (no `--probe` needed: reading
      `os.environ` or looking up an already-loaded document is not "store
      I/O" in the sense `--probe` gates).
    - **Integration registered** — for every other store type, constructs
      the `StoreIntegration` via `_Resolvers.get()` (the same lazy,
      per-store-type cache `resolve_values()` uses) and catches
      `IntegrationNotFoundError`. Construction alone never performs network
      I/O for any of the three real resolvers (Infisical/Azure Key Vault/
      Azure App Config just read env vars/config) — safe to do unconditionally.
    - **Reachable** — only with `--probe`: calls `.resolve(str(store.value))`
      for real, discarding the value and keeping only success/failure.

    Args:
        context: An already-`require_valid()`-ed solution.
        deployment_name: `meta.name` of the deployment to diagnose.
        key: Diagnose this one declared key only, or `None` for every
            variable/secret/feature reachable from the deployment.
        probe: Also attempt a real `resolve()` call per integration-backed key.

    Returns:
        `(rows, Diagnostics)`, sorted by `(kind, key)`. One `Diagnostic`
        error per row that would not resolve (D4: no whole-batch preflight —
        every key reported independently, same uniform failure model
        `values get`/`values list` already use).

    Raises:
        UsageError: `deployment_name` does not name a real deployment (via
            `resolve_deployment()`), or more than one enabled `Integration`
            document declares the same store type (`bind_integration_config()`'s
            rule, via `_Resolvers.get()` — a real configuration error, not a
            per-key finding).
    """
    deployment = resolve_deployment(context, deployment_name)
    environments = reachable_environments(context, deployment)
    variables, secrets, features = merge_environment_models(environments)

    items: list[tuple[str, str, VariableStoreModel | SecretStoreModel | FeatureStoreModel]] = [
        *(("variable", k, v) for k, v in variables.items()),
        *(("secret", k, v) for k, v in secrets.items()),
        *(("feature", k, v) for k, v in features.items()),
    ]

    diagnostics = Diagnostics()
    if key is not None:
        items = [item for item in items if item[1] == key]
        if not items:
            diagnostics.error(
                f"'{key}' is not declared in any environment reachable from deployment '{deployment_name}'.",
                location=key,
                code="unknown_value_key",
            )
            return [], diagnostics

    resolvers = _Resolvers(context.controller.index)
    rows: list[ResolveRow] = [
        _diagnose_item(context, deployment, kind, item_key, store, resolvers, probe, diagnostics)
        for kind, item_key, store in items
    ]
    rows.sort(key=lambda row: (row.kind, row.key))

    return rows, diagnostics


def _diagnose_item(
    context: SolutionContext,
    deployment: DeploymentModel,
    kind: str,
    key: str,
    store: VariableStoreModel | SecretStoreModel | FeatureStoreModel,
    resolvers: "_Resolvers",
    probe: bool,
    diagnostics: Diagnostics,
) -> ResolveRow:
    """Diagnose one declared key. See `resolve_diagnostic()` for the checks performed."""
    store_type = store.store

    if store_type in _CONSTANT_TYPES:
        return ResolveRow(key=key, kind=kind, store=store_type.value, registered=True, probed=False, ok=True)

    if store_type in _ENVIRONMENT_TYPES or store_type == SecretStoreType.GITHUB:
        from os import environ

        var_name = str(store.value)
        if var_name in environ:
            return ResolveRow(key=key, kind=kind, store=store_type.value, registered=True, probed=False, ok=True)
        reason = f"environment variable '{var_name}' is not set."
        diagnostics.error(f"'{key}': {reason}", location=key, code="value_would_not_resolve")
        return ResolveRow(
            key=key, kind=kind, store=store_type.value, registered=True, probed=False, ok=False, reason=reason
        )

    if isinstance(store, VariableStoreModel) and store_type == VariableStoreType.ARTIFACT:
        assert store.field is not None  # guaranteed by validate_field_only_on_artifact_store
        value = resolve_artifact_field(context, deployment, str(store.value), store.field)
        if value is not None:
            return ResolveRow(key=key, kind=kind, store="artifact", registered=True, probed=False, ok=True)
        reason = f"'store: artifact' references '{store.value}', which is not a known artifact in this solution."
        diagnostics.error(f"'{key}': {reason}", location=key, code="value_would_not_resolve")
        return ResolveRow(key=key, kind=kind, store="artifact", registered=True, probed=False, ok=False, reason=reason)

    try:
        integration = resolvers.get(store_type.value)
    except IntegrationNotFoundError:
        reason = f"no resolver implemented yet for store '{store_type.value}'."
        diagnostics.error(f"'{key}': {reason}", location=key, code="value_would_not_resolve")
        return ResolveRow(
            key=key, kind=kind, store=store_type.value, registered=False, probed=False, ok=False, reason=reason
        )

    if not probe:
        return ResolveRow(key=key, kind=kind, store=store_type.value, registered=True, probed=False, ok=True)

    try:
        integration.resolve(str(store.value))
    except ValueResolutionError as exc:
        diagnostics.error(f"'{key}': {exc}", location=key, code="value_would_not_resolve")
        return ResolveRow(
            key=key, kind=kind, store=store_type.value, registered=True, probed=True, ok=False, reason=str(exc)
        )
    return ResolveRow(key=key, kind=kind, store=store_type.value, registered=True, probed=True, ok=True)


#: Built-in store types `set_value()` refuses to write — matches
#: `_CONSTANT_TYPES`/`_ENVIRONMENT_TYPES` plus the two kind-specific
#: built-ins (`github` for secrets, `artifact` for variables) that aren't
#: in either of those sets.
_NO_INTEGRATION_TO_WRITE_TO = "no store integration to write to — edit it directly instead:"


@dataclass(frozen=True)
class SetResult:
    """What `values set` writes, for display — never the value itself
    (docs/design/values-secrets-command.md Phase 6).
    """

    key: str
    kind: str
    """One of "variable"/"secret"/"feature"."""
    store: str
    generated: bool
    """True when the written value came from `--generate` (the key's own
    `generate:` spec), not an explicit `--value`/`--from-file`/`--stdin`."""


def _reject_unwritable_builtin_store(
    key: str, kind: str, store: VariableStoreModel | SecretStoreModel | FeatureStoreModel
) -> None:
    """Raise `UsageError` for a store `values set` cannot write to at all —
    there is no integration to call `.set()` on; the value lives directly
    in the environment YAML, an OS/CI environment variable, or another
    in-solution document. Matches v1's `values set` messaging for these
    same cases (its own docstring: "constant/environment: prints where to
    edit").
    """
    store_type = store.store
    if store_type in _CONSTANT_TYPES:
        raise UsageError(
            f"'{key}' is a constant value — {_NO_INTEGRATION_TO_WRITE_TO} edit its 'value:' "
            "field in the environment YAML."
        )
    if store_type in _ENVIRONMENT_TYPES:
        raise UsageError(
            f"'{key}' reads from environment variable '{store.value}' — {_NO_INTEGRATION_TO_WRITE_TO} "
            "set that variable directly (shell, CI job, etc.)."
        )
    if kind == "secret" and store_type == SecretStoreType.GITHUB:
        raise UsageError(
            f"'{key}' reads from GitHub Actions secret '{store.value}' — {_NO_INTEGRATION_TO_WRITE_TO} "
            "set it in the repository/organization secrets."
        )
    if kind == "variable" and store_type == VariableStoreType.ARTIFACT:
        raise UsageError(
            f"'{key}' resolves from an in-solution artifact reference — {_NO_INTEGRATION_TO_WRITE_TO} "
            "edit the referenced artifact/version document."
        )


def set_value(
    context: SolutionContext,
    deployment_name: str,
    key: str,
    *,
    value: str | None = None,
    from_file: str | None = None,
    from_stdin: bool = False,
    generate: bool = False,
) -> tuple[SetResult | None, Diagnostics]:
    """Write `key`'s value to its configured store backend for `deployment_name`.

    `values set`'s controller function (docs/design/values-secrets-command.md
    Phase 6, built on Phase 5's `StoreIntegration.set()`). Exactly one value
    source is required: `value` (explicit), `from_file` (a path — read as
    text, multiline-safe for certs/keys), `from_stdin` (reads `sys.stdin`),
    or `generate` (uses the key's own declared `SecretGenerateSpec` — secrets
    only).

    A `constant`/`environment`/`github`/`store: artifact` key has no
    integration to write to at all — rejected with a `UsageError` naming
    where to edit it directly instead (`_reject_unwritable_builtin_store()`),
    same messaging v1's `values set` used for these.

    A *real* write failure — the integration's own `.set()` raised
    `IntegrationError` (unsupported, unreachable, unauthenticated) — is
    **not** raised further: it is reported as a single `Diagnostic` and
    `(None, diagnostics)` is returned, matching `values get`/`list`/
    `resolve`'s uniform "the operation itself failed" reporting (D4) rather
    than propagating an exception type `command_run()`'s `StrataError`-only
    handler does not recognize (`IntegrationError` is not a `StrataError`).

    Args:
        context: An already-`require_valid()`-ed solution.
        deployment_name: `meta.name` of the deployment `key` is written for.
        key: The declared variable/secret/feature key to write.
        value: Explicit literal value.
        from_file: Path to read the value from (text, as-is).
        from_stdin: Read the value from `sys.stdin`.
        generate: Use the key's own `generate:` spec instead of a supplied value.

    Returns:
        `(SetResult, Diagnostics)` on a successful write — `SetResult` never
        carries the value itself, only what was written and where, safe to
        print. `(None, Diagnostics)` when the write itself failed; the
        `Diagnostics` explains why.

    Raises:
        UsageError: `deployment_name`/`key` does not exist, the value
            source is missing/ambiguous (not exactly one of
            value/from_file/from_stdin/generate), `--generate` was used on
            a non-secret or a secret with no `generate:` spec, `from_file`
            could not be read, or `key`'s store has no integration to
            write to at all (see `_reject_unwritable_builtin_store()`).
    """
    deployment = resolve_deployment(context, deployment_name)
    environments = reachable_environments(context, deployment)
    variables, secrets, features = merge_environment_models(environments)

    store: VariableStoreModel | SecretStoreModel | FeatureStoreModel | None
    if key in secrets:
        store, kind = secrets[key], "secret"
    elif key in features:
        store, kind = features[key], "feature"
    elif key in variables:
        store, kind = variables[key], "variable"
    else:
        raise UsageError(f"'{key}' is not declared in any environment reachable from deployment '{deployment_name}'.")

    _reject_unwritable_builtin_store(key, kind, store)

    sources = (value is not None, from_file is not None, from_stdin, generate)
    if sum(sources) != 1:
        raise UsageError("Provide exactly one of --value, --from-file, --stdin, or --generate.")

    if generate:
        if kind != "secret":
            raise UsageError(f"--generate is only valid for secrets — '{key}' is a {kind}.")
        assert isinstance(store, SecretStoreModel)
        if store.generate is None:
            raise UsageError(f"Secret '{key}' has no generate: spec — use --value/--from-file/--stdin instead.")
        resolved_value = generate_secret(store.generate.type.value, store.generate.length)
    elif from_file is not None:
        try:
            resolved_value = Path(from_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"Cannot read --from-file '{from_file}': {exc}") from exc
    elif from_stdin:
        resolved_value = stdin.read()
    else:
        assert value is not None  # guaranteed by the exactly-one-source check above
        resolved_value = value

    resolvers = _Resolvers(context.controller.index)
    diagnostics = Diagnostics()
    try:
        integration = resolvers.get(store.store.value)
        integration.set(str(store.value), resolved_value)
    except (IntegrationNotFoundError, IntegrationError) as exc:
        diagnostics.error(f"'{key}': {exc}", location=key, code="value_write_failed")
        return None, diagnostics

    return SetResult(key=key, kind=kind, store=store.store.value, generated=generate), diagnostics


def rotate_secret(
    context: SolutionContext,
    deployment_name: str,
    key: str,
    *,
    force: bool = False,
) -> tuple[SetResult | None, Diagnostics]:
    """Rotate secret `key`: generate a new value from its own `generate:`
    spec and overwrite it in the store (docs/design/values-secrets-command.md
    Phase 7, built on Phase 5's `StoreIntegration.set()`).

    Reuses `SetResult` for the return type — a rotation is, from the
    caller's point of view, exactly a generated `values set` write
    (`kind="secret"`, `generated=True`) with no new shape needed.

    A secret's `generate:` spec is only ever declared on an integration-
    backed store — `SecretStoreModel.validate_generate_not_on_builtin()`
    already guarantees a `constant`/`environment`/`github` secret can never
    carry one — so checking for a `generate:` spec alone is sufficient to
    also rule out every built-in store; unlike `set_value()`, there is no
    separate `_reject_unwritable_builtin_store()` call needed here.

    `force` is accepted only for CLI signature symmetry — the actual
    confirmation prompt (`click.confirm()`) is the command layer's job
    (`values_command.py:values_rotate()`), not this function's: every
    controller in this codebase stays free of interactive I/O.

    A real write failure (`.set()` raised `IntegrationError`) is reported
    as a `Diagnostic` and returns `(None, diagnostics)` rather than being
    raised further — same reasoning as `set_value()` (D4; `IntegrationError`
    is not a `StrataError`, so `command_run()`'s exception handler would
    not catch it).

    Args:
        context: An already-`require_valid()`-ed solution.
        deployment_name: `meta.name` of the deployment `key` is rotated for.
        key: The declared secret key to rotate.
        force: Unused here — see docstring.

    Returns:
        `(SetResult, Diagnostics)` on a successful rotation. `(None,
        Diagnostics)` when the write itself failed.

    Raises:
        UsageError: `deployment_name` does not name a real deployment,
            `key` is not declared, `key` is a variable/feature (rotation
            is secrets-only), or the secret has no `generate:` spec
            (pointing at `values set --value`/`--from-file`/`--stdin`
            instead).
    """
    del force  # accepted for CLI signature symmetry only — see docstring
    deployment = resolve_deployment(context, deployment_name)
    environments = reachable_environments(context, deployment)
    variables, secrets, features = merge_environment_models(environments)

    if key in secrets:
        store = secrets[key]
    elif key in features:
        raise UsageError(f"'{key}' is a feature — rotation only applies to secrets.")
    elif key in variables:
        raise UsageError(f"'{key}' is a variable — rotation only applies to secrets.")
    else:
        raise UsageError(f"'{key}' is not declared in any environment reachable from deployment '{deployment_name}'.")

    if store.generate is None:
        raise UsageError(
            f"Secret '{key}' has no generate: spec — cannot auto-rotate. "
            "Use 'values set --value'/'--from-file'/'--stdin' to set it manually instead."
        )

    new_value = generate_secret(store.generate.type.value, store.generate.length)

    resolvers = _Resolvers(context.controller.index)
    diagnostics = Diagnostics()
    try:
        integration = resolvers.get(store.store.value)
        integration.set(str(store.value), new_value)
    except (IntegrationNotFoundError, IntegrationError) as exc:
        diagnostics.error(f"'{key}': {exc}", location=key, code="value_write_failed")
        return None, diagnostics

    return SetResult(key=key, kind="secret", store=store.store.value, generated=True), diagnostics


@dataclass(frozen=True)
class SecretStatusRow:
    """One secret's rotation health (`values status`, Phase 8) — never
    carries the secret's value, only its age relative to its own
    `rotate.max_age`.

    `status` is one of:
      - `"ok"`: has metadata, age is within `max_age`.
      - `"overdue"`: has metadata, age has reached or exceeded `max_age`.
      - `"no_metadata"`: the store integration couldn't report timestamps
        for this key (`StoreIntegration.metadata()` returned `None` or
        raised `IntegrationError`).
      - `"no_integration"`: no resolver class is registered for this
        secret's store type at all.
      - `"no_timestamp"`: metadata came back, but with neither
        `created_at` nor `updated_at` set.

    `age_days`/`days_remaining` are only ever set for `"ok"`/`"overdue"`
    (the only statuses with a usable timestamp to measure age from).
    """

    key: str
    store: str
    max_age: int
    policy: str
    """`"warn"` or `"rotate"` (`SecretRotatePolicy`'s value)."""
    status: str
    age_days: int | None = None
    days_remaining: int | None = None


def secret_status(context: SolutionContext, deployment_name: str) -> tuple[list[SecretStatusRow], Diagnostics]:
    """Report rotation health for every secret reachable from
    `deployment_name` that declares a `rotate:` spec.

    `values status`'s controller function (docs/design/
    values-secrets-command.md Phase 8, built on Phase 5's
    `StoreIntegration.metadata()`) — the read-only counterpart to `values
    rotate`: this never mutates a store (D2), it only reports age vs. each
    secret's own `rotate.max_age`. Secrets with no `rotate:` spec at all
    are silently skipped — there is nothing to report on.

    Unlike every other function in this module, this returns a
    `Diagnostics` that is **not** the sole success/failure signal — every
    classification (`ok`/`overdue`/`no_metadata`/`no_integration`/
    `no_timestamp`) is a valid, non-exceptional outcome, so `Diagnostics`
    here is a presentation convenience (one `WARNING` for anything that
    couldn't be measured, one `ERROR` per secret overdue for rotation —
    the console/JSON renderers already print each row regardless) rather
    than a resolution-failure bag like `values get`/`list`/`resolve`/`set`/
    `rotate` return. `values_command.py` still derives the final exit code
    from the rows themselves (`any(row.status == "overdue" ...)`), not
    from `diagnostics.ok` alone, matching the design's own CLI contract.

    Args:
        context: An already-`require_valid()`-ed solution.
        deployment_name: `meta.name` of the deployment to check.

    Returns:
        `(rows, Diagnostics)`, `rows` sorted by `key`.

    Raises:
        UsageError: `deployment_name` does not name a real deployment.
    """
    deployment = resolve_deployment(context, deployment_name)
    environments = reachable_environments(context, deployment)
    _variables, secrets, _features = merge_environment_models(environments)

    resolvers = _Resolvers(context.controller.index)
    diagnostics = Diagnostics()
    rows: list[SecretStatusRow] = []
    now = datetime.now(timezone.utc)

    for key, store in secrets.items():
        if store.rotate is None:
            continue
        max_age = store.rotate.max_age
        policy = store.rotate.policy.value

        try:
            integration = resolvers.get(store.store.value)
        except IntegrationNotFoundError:
            diagnostics.warning(
                f"'{key}': no resolver implemented yet for store '{store.store.value}' — cannot check rotation age.",
                location=key,
                code="secret_status_no_integration",
            )
            rows.append(
                SecretStatusRow(
                    key=key, store=store.store.value, max_age=max_age, policy=policy, status="no_integration"
                )
            )
            continue

        try:
            meta = integration.metadata(str(store.value))
        except IntegrationError as exc:
            diagnostics.warning(f"'{key}': {exc}", location=key, code="secret_status_no_metadata")
            rows.append(
                SecretStatusRow(key=key, store=store.store.value, max_age=max_age, policy=policy, status="no_metadata")
            )
            continue

        if meta is None:
            diagnostics.warning(
                f"'{key}': store did not report metadata for this secret.",
                location=key,
                code="secret_status_no_metadata",
            )
            rows.append(
                SecretStatusRow(key=key, store=store.store.value, max_age=max_age, policy=policy, status="no_metadata")
            )
            continue

        reference_time = meta.updated_at or meta.created_at
        if reference_time is None:
            diagnostics.warning(
                f"'{key}': store reported metadata with no created_at/updated_at timestamp.",
                location=key,
                code="secret_status_no_timestamp",
            )
            rows.append(
                SecretStatusRow(key=key, store=store.store.value, max_age=max_age, policy=policy, status="no_timestamp")
            )
            continue

        age_days = (now - reference_time).days
        if age_days >= max_age:
            diagnostics.error(
                f"'{key}' is overdue for rotation ({age_days}d / {max_age}d).",
                location=key,
                code="secret_rotation_overdue",
            )
            rows.append(
                SecretStatusRow(
                    key=key,
                    store=store.store.value,
                    max_age=max_age,
                    policy=policy,
                    status="overdue",
                    age_days=age_days,
                )
            )
        else:
            rows.append(
                SecretStatusRow(
                    key=key,
                    store=store.store.value,
                    max_age=max_age,
                    policy=policy,
                    status="ok",
                    age_days=age_days,
                    days_remaining=max_age - age_days,
                )
            )

    rows.sort(key=lambda row: row.key)
    return rows, diagnostics
