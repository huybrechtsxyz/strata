#!/usr/bin/env python3
"""Plain resolved-data bundles handed into `InfraIntegration.prepare()`
(ADR-0022 D1/D1a, ADR-0023 D5) — no index/`SolutionContext` access, just
already-resolved models, assembled once by the `build run` orchestrator
and passed down unchanged.

Both types live here rather than in `strata.controllers`, deliberately:
`Integration` subclasses are not allowed to touch `DocumentIndex`/
`SolutionContext` directly (ADR-0021 D2), and `strata.integrations` sits
below `strata.controllers` in the import-linter layering (ADR-0003/
`pyproject.toml`'s `[tool.importlinter]`), so a type an `Integration`
method needs to reference cannot live in a higher layer — the same
argument that put `ResolvedWorkspaceGraph` here also applies to
`ValueResolution`, previously defined inside
`strata.controllers.value_controller` (moved here; that module now
imports it from here instead, since a controller importing a lower-layer
type is the direction the contract allows).
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from strata.integrations.helm_cache import ChartPin
from strata.integrations.helm_dependencies import DependencyRecord
from strata.models.common_models import ModuleReferenceModel
from strata.models.deployment_model import DeploymentModel
from strata.models.dns_model import DnsModel
from strata.models.firewall_model import FirewallModel
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.network_model import NetworkModel
from strata.models.provider_model import ProviderModel
from strata.models.resource_model import ResourceModel
from strata.models.store_model import VariableValueType
from strata.models.tenant_model import TenantModel
from strata.models.version_model import VersionModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.diagnostics import Diagnostics


@dataclass
class ValueResolution:
    """The outcome of resolving a set of requested keys.

    One flat `values: dict[str, str]` — the store a value came from
    (variable/secret/feature) is not preserved on the result. Callers that
    must keep secrets out of a build-time artifact (ADR-0022 D1a's safety
    note) enforce that by which `keys` they request, not by anything this
    type guarantees structurally.
    """

    deployment: str
    values: dict[str, str] = field(default_factory=dict)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)


@dataclass(frozen=True)
class SecretMetadata:
    """What a store can tell us about a secret without revealing its value
    (docs/design/values-secrets-command.md D3) — `StoreIntegration.metadata()`'s
    return type, consumed by `values status`'s age-vs-`rotate.max_age` check
    (Phase 8).

    Both fields are best-effort: a store that cannot report one, or either,
    returns `None` for it rather than raising — `values status` treats a
    missing timestamp as its own `no_timestamp` classification, not a
    resolution failure.
    """

    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class KeyValueEntry:
    """One `KeyValueStoreIntegration.get()` result — the stored bytes and
    the backend's own version/ETag for them, consumed by a caller's
    compare-and-swap retry loop (docs/work/key-value-store-integration.md)."""

    value: bytes
    version: str


@dataclass(frozen=True)
class ValueReference:
    """One declared variable/feature/secret — never a resolved
    integration-backed value (docs/design/build-time-value-categories.md,
    Q4).

    `value` is populated **only** for `constant`/`environment` stores —
    exactly the values `build run` is already allowed to know and already
    writes to `.auto.tfvars.json` output. For a secret, or any
    integration-backed store, `value` stays `None` — structurally, not by a
    filter applied later — so this type (and any collection of it) is safe
    to dump wholesale for debugging/audit.
    """

    key: str
    store: str
    description: str | None = None
    value_type: VariableValueType | None = None
    value: Any = None


@dataclass(frozen=True)
class EffectiveImage:
    """One container image a deployment will run, after `kind: version` pins
    (`effective_images.effective_images()`, docs/work/effective-images.md).

    `origin` is `artifact` (a `kind: artifact`, reached through a `store: artifact` variable or a module
    service's `artifact`) or `module-service` (a module service's literal `image`). `name` is the artifact
    or service name; `artifact`/`module` say which document it came from, and `via` lists every use
    (`variable:KEY`, `service:MODULE/SERVICE`, `token:KIND/NAME` for a document holding an
    `${artifact:}` token).

    `image_tag` is the tag part only and `image_digest` the digest part (`sha256:...`) of what a pin or the
    declaration supplies (`1.4.2`, `sha256:...` or `1.4.2@sha256:...`); either is `None` when absent, and
    `image_ref` is the bare name when both are. `tag_source` is `pin`, `declared` or `none`; `pin_version`
    names the version document of a pin, and `pin_flag` is true when that came from `--pin` rather than the deployment's `spec.version`.

    `integration` is an artifact's `spec.integration`, by name only (docs/work/artifact-gap-analysis.md,
    design 6): it tells a pipeline that copies the image which credentials to use. Nothing is resolved from it.

    `delivered` says whether strata writes the image into what runs (docs/work/helm-image-delivery.md, option B):
    true for a Compose service, an artifact a step receives through a variable and one an `${artifact:}` token
    delivers; false for a Helm module's service image, which the chart may override. `delivery` gives the reason
    when it is false (`helm module: the chart decides`).
    """

    origin: str
    name: str
    image_name: str
    image_tag: str | None
    image_ref: str
    tag_source: str
    artifact: str | None = None
    module: str | None = None
    via: tuple[str, ...] = ()
    registry: str | None = None
    configuration: dict[str, Any] | None = None
    pin_version: str | None = None
    pin_flag: bool = False
    integration: str | None = None
    image_digest: str | None = None
    delivered: bool = True
    delivery: str | None = None


@dataclass(frozen=True)
class UnknownImageModule:
    """An enabled Helm module that declares no service `image` and no `artifact`, so the images its chart deploys
    are known only to Helm (`effective_images.unknown_image_modules()`, docs/work/helm-image-delivery.md, option B).

    `namespace` is the namespace whose `modules` enables it; `chart_version` is the one that deploys, after a
    `charts` pin.
    """

    module: str
    namespace: str
    chart: str | None = None
    chart_version: str | None = None


@dataclass(frozen=True)
class ResolvedWorkspaceGraph:
    """Already-resolved documents a workspace references.

    Named for what it *is* — the resolved workspace's own document graph —
    not for Terraform's default projection (ADR-0023) being its first
    consumer; a future deploy-manifest feature is expected to reuse this
    same type rather than a parallel one (ADR-0023 Consequences).

    `providers`/`resources`/`namespaces`/`firewalls`/`dns`/`networks` are
    keyed by document name (the same names `WorkspaceSpecModel.providers`/
    `.resources[].resource`/`.namespaces`/`.firewalls`/`.dns_zones`/
    `.networks` reference), so a consumer never re-does its own name
    lookup. `workspace.spec.topology` itself is already inline
    (`TopologySpecModel`, ADR-0028) — no separate resolution needed.

    `modules` (docs/design/unified-terraform-projection.md) is keyed
    the same way, but one level removed: the names come from every resolved
    namespace's own `spec.modules[].module` (`ModuleReferenceModel`), not
    directly from a `WorkspaceSpecModel` field — scoped to namespace-attached
    modules only, deliberately excluding `TopologyComponentModel.modules`
    (zero real use found across six real workspaces checked, per that doc's
    own evidence).

    `variable_refs`/`feature_refs`/`secret_refs`/`properties`/`custom`
    (docs/design/build-time-value-categories.md, Q3/Q4) are computed once
    by `build_controller.py`, before the per-provisioner loop — derived,
    minimal data, not raw `DeploymentModel`/`EnvironmentModel` instances.

    `tenant` (docs/work/build-command.md's `tenant` Terraform category) is
    the deployment's own `spec.tenant` reference, resolved once by
    `value_controller.resolve_tenant()` — `None` when the deployment
    references no tenant, or the reference doesn't resolve.

    `deployment` is the whole resolved Deployment document — needed by
    `terraform_projection.py`'s `_strata_workspace()` (docs/design/
    terraform-tfvars-parity.md), which reads `deployment.meta.name`/
    `.labels`/`.annotations`/`.tags`/`.apiVersion` alongside the workspace's
    own, matching v1's real `_build_workspace_vars()` exactly. `None` for a
    caller with no deployment in scope (none exist today; kept optional for
    the same reason `tenant` is), in which case that function falls back to
    workspace-only defaults.

    `version` is the `Version` document `deployment.spec.version` resolved
    to (docs/work/promotion.md Phase 4), already reflecting `--pin` when
    one was given (`value_controller.resolve_version()`'s own job) — needed
    by `write_resolved_manifest()`'s optional `promotion` section. `None`
    when the deployment names no version, same as `tenant`.

    `resolved_assets` (docs/design/asset-management.md Phase 4) is keyed by
    asset name, populated by `build_controller.py`'s asset-sync step
    (Phase 3) before the provisioning step loop — not by
    `terraform_projection.py`, which never touches a remote or the
    filesystem (ADR-0021 D2). Deliberately keyed only by names this
    build's execution steps actually reference, not the full
    `workspace.spec.assets` pool — see that doc's own "Deliberate
    deviation" note.
    """

    workspace: WorkspaceModel
    providers: dict[str, ProviderModel] = field(default_factory=dict)
    resources: dict[str, ResourceModel] = field(default_factory=dict)
    namespaces: dict[str, NamespaceModel] = field(default_factory=dict)
    firewalls: dict[str, FirewallModel] = field(default_factory=dict)
    dns: dict[str, DnsModel] = field(default_factory=dict)
    networks: dict[str, NetworkModel] = field(default_factory=dict)
    modules: dict[str, ModuleModel] = field(default_factory=dict)
    variable_refs: list[ValueReference] = field(default_factory=list)
    feature_refs: list[ValueReference] = field(default_factory=list)
    secret_refs: list[ValueReference] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    custom: dict[str, Any] = field(default_factory=dict)
    tenant: TenantModel | None = None
    deployment: DeploymentModel | None = None
    version: VersionModel | None = None
    resolved_assets: dict[str, "ResolvedAssetInfo"] = field(default_factory=dict)
    images: list[EffectiveImage] = field(default_factory=list)
    """Every image the deployment runs, pin-resolved (`effective_images()`); empty when the caller did not
    compute them. The SBOM's image collector reads the `artifact` rows from here."""
    step_root: str = "."
    """The directory the step reading this graph runs in, relative to the build
    root (POSIX, e.g. 'terraform/apim'); '.' when no step is involved.
    `build_strata_assets()` derives each asset's `path_from_step` from it."""


@dataclass(frozen=True)
class ResolvedModule:
    """One namespace module, resolved and materialised — the workload
    pipeline's (ADR-0022 D5-D7) equivalent of a provisioner's already-synced
    source directory.

    Carries `reference` alongside `module` (not just a bare `ModuleModel`,
    which is all D6's original pseudocode passed) because `module` alone
    cannot answer "where does this one render to": `ModuleReferenceModel`'s
    own docstring notes one Module document can be attached more than once
    under different reference names within a namespace, so only the
    reference's `name` (unique within its namespace,
    `NamespaceSpecModel.validate_namespace_spec()`) is safe to key a build
    directory on — `module.meta.name` is not.

    `source_path` is already-materialised (or, for a chart-based `source`,
    simply an empty directory reserved for `values.yaml`/`meta.yaml` — a
    registry chart is pulled by the deployer, not copied) by the
    orchestrator before `InfraIntegration.prepare_namespace()` is called,
    the same "controller resolves paths and remotes, the integration never
    touches `DocumentIndex`/remotes directly" split `sync_source()`/
    `prepare()` already establish for the provisioner path (ADR-0021 D2).
    """

    reference: ModuleReferenceModel
    module: ModuleModel
    source_path: Path
    chart_pin: ChartPin | None = None
    """A registry chart `build run` pinned into `.strata/helm/` (docs/work/helm-registry-chart-checks.md);
    `None` for a local chart, an unpinned one, and at deploy."""
    chart_dependencies: tuple[DependencyRecord, ...] = ()
    """A local chart's dependencies `build run` placed in (or found in) its `charts/`
    (docs/work/helm-chart-dependencies.md); empty at deploy."""
    unpinned_dependencies: tuple[str, ...] = ()
    """Names of the local chart's dependencies the build could not fetch; the deploy refuses the chart."""


@dataclass(frozen=True)
class ResolvedAssetInfo:
    """One asset's build-time resolution (docs/design/asset-management.md
    Phase 4) — computed once by `build_controller.py` before the step
    loop, exactly like `ResolvedModule.source_path` is for namespace
    modules. Carries no remote/`DocumentIndex` access itself;
    `terraform_projection.py` only ever reads the plain fields below.
    """

    path: str
    """Build-relative, e.g. 'assets/openapi' (keyed by the workspace slot)."""
    asset: str
    """The Asset document bound to the slot for this deployment."""
    files: list[str] = field(default_factory=list)
    """Sorted filenames at that path."""
