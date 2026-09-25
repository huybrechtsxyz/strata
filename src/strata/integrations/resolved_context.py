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
from pathlib import Path
from typing import Any

from strata.models.common_models import ModuleReferenceModel
from strata.models.dns_model import DnsModel
from strata.models.firewall_model import FirewallModel
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.network_model import NetworkModel
from strata.models.provider_model import ProviderModel
from strata.models.resource_model import ResourceModel
from strata.models.store_model import VariableValueType
from strata.models.topology_model import TopologyModel
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
class ResolvedWorkspaceGraph:
    """Already-resolved documents a workspace references.

    Named for what it *is* — the resolved workspace's own document graph —
    not for Terraform's default projection (ADR-0023) being its first
    consumer; a future deploy-manifest feature is expected to reuse this
    same type rather than a parallel one (ADR-0023 Consequences).

    `providers`/`topologies`/`resources`/`namespaces`/`firewalls`/`dns`/
    `networks` are keyed by document name (the same names
    `WorkspaceSpecModel.providers`/`.topology`/`.resources[].resource`/
    `.namespaces`/`.firewalls`/`.dns_zones`/`.networks` reference), so a
    consumer never re-does its own name lookup.

    `variable_refs`/`feature_refs`/`secret_refs`/`properties`/`custom`
    (docs/design/build-time-value-categories.md, Q3/Q4) are computed once
    by `build_controller.py`, before the per-provisioner loop — derived,
    minimal data, not raw `DeploymentModel`/`EnvironmentModel` instances.
    """

    workspace: WorkspaceModel
    providers: dict[str, ProviderModel] = field(default_factory=dict)
    topologies: dict[str, TopologyModel] = field(default_factory=dict)
    resources: dict[str, ResourceModel] = field(default_factory=dict)
    namespaces: dict[str, NamespaceModel] = field(default_factory=dict)
    firewalls: dict[str, FirewallModel] = field(default_factory=dict)
    dns: dict[str, DnsModel] = field(default_factory=dict)
    networks: dict[str, NetworkModel] = field(default_factory=dict)
    variable_refs: list[ValueReference] = field(default_factory=list)
    feature_refs: list[ValueReference] = field(default_factory=list)
    secret_refs: list[ValueReference] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    custom: dict[str, Any] = field(default_factory=dict)


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

