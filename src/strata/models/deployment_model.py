#!/usr/bin/env python3
"""Pydantic model for deployment configuration validation.

A `Deployment` is the "container instance" of ADR-0011's image/container
framing: it executes a `Workspace`'s provisioning recipe against a specific
`Environment`, for a specific `Tenant`. The Workspace says *what* runs and in
what order; the Deployment says *where, with which values, and with what
runtime behaviour*.

Ported from the **15 real deployment documents** across two production repos.
Spec field usage there:

==================  =====
field               uses
==================  =====
``environments``      15
``locking``           12
``layers``            10
``stages``             9
``workspace``          9
``extends``            6
``partial``            4
``tenant``             4
``configurations``     3
``lifecycle``          3
``properties``         3
``versions``           0
``promotion``          0
``gates``              0
==================  =====

`versions`, `promotion` and `gates` are not ported — zero usage, same
treatment Environment's unused subtree got.

**Stages reference the recipe rather than redefining it.** v1's
`DeploymentStageModel` carried `provisioner` and `topology`, i.e. it bound
tools at deploy time — and v1's workspaces have no recipe at all, so stages
*were* the recipe. v2 moved that to `Workspace.execution` (ADR-0011), and
the real usage supports it: the stage list lives on the reusable
`partial: true` base and never varies per leaf, while leaves supply only
layers/environments/locking. So here a stage names a
`ProvisioningStepModel` via `step` and carries only runtime knobs — the same
class/instance split as `WorkspaceResourceModel.resource` and
`ModuleReferenceModel.module`.
"""

from typing import Annotated, Any, ClassVar

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    ScriptsModel,
    validate_kind_matches,
)
from strata.models.reference_fields import References
from strata.utils.names import check_unique_names


class DeploymentHealthCheckModel(PlatformBaseModel):
    """A reachability probe to run after a stage: an `http` check needs `url`, a `tcp` check needs `host`
    and `port`. Accepted and validated, but strata does not use it yet.

    Design notes:
    A post-stage reachability probe.
    """

    schema_rules: ClassVar[tuple[str, ...]] = ("An `http` check needs `url`; a `tcp` check needs `host` and `port`.",)

    name: PlatformName = Field(description="Unique health check name within the stage")
    type: str = Field(description="Check type: `http` or `tcp`.\n\nDesign notes: Check type: 'http' or 'tcp'")
    url: str | None = Field(None, description="URL to GET (http type)")
    expect_status: int = Field(default=200, description="Expected HTTP status code (http type)")
    host: str | None = Field(None, description="Hostname or IP (tcp type)")
    port: int | None = Field(None, ge=1, le=65535, description="TCP port (tcp type)")
    timeout: int = Field(default=10, ge=1, description="Connection/request timeout in seconds")

    @field_validator("type")
    @classmethod
    def validate_check_type(cls, v: str) -> str:
        """Only http and tcp probes exist."""
        if v not in ("http", "tcp"):
            raise ValueError(f"Health check type must be 'http' or 'tcp', got '{v}'")
        return v

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> "DeploymentHealthCheckModel":
        """An http check needs a url; a tcp check needs host and port."""
        if self.type == "http" and self.url is None:
            raise ValueError(f"Health check '{self.name}': 'url' is required for type 'http'.")
        if self.type == "tcp" and (self.host is None or self.port is None):
            raise ValueError(f"Health check '{self.name}': 'host' and 'port' are required for type 'tcp'.")
        return self

    # v1 additionally had `output_key`, binding a check's url/host to a
    # preceding stage's provisioner output. Not ported for the same reason
    # as DnsRecordModel.output_key (ADR-0006): it needs the shared runtime
    # Context store, which does not exist in v2.


class DeploymentStageTimeoutsModel(PlatformBaseModel):
    """Per-phase timeouts in seconds that override a provisioner's defaults. The environment variable
    `STRATA_TIMEOUT_<STEP>_<PHASE>` overrides them for a single run.

    Design notes:
    Per-phase timeouts in seconds, overriding provisioner defaults.
    """

    setup: int | None = Field(None, ge=1, description="Timeout for the setup phase")
    check: int | None = Field(None, ge=1, description="Timeout for the check/validate phase")
    plan: int | None = Field(None, ge=1, description="Timeout for the plan phase")
    apply: int | None = Field(None, ge=1, description="Timeout for the apply phase")
    destroy: int | None = Field(None, ge=1, description="Timeout for the destroy phase")


class DeploymentStageModel(PlatformBaseModel):
    """Runtime parameters for one provisioning step of the workspace, named by `step`. A stage never
    redefines what runs. Today strata applies only `timeouts`; the other fields are accepted and
    validated but not used yet.

    Design notes:
    Runtime parameters for one provisioning step.

    Names a `ProvisioningStepModel` from the workspace's recipe via `step`;
    it does not redefine what runs (see module docstring).
    """

    schema_rules: ClassVar[tuple[str, ...]] = ("Health check names must be unique within the stage.",)

    step: PlatformName = Field(
        description="Name of the workspace `execution` step these parameters apply to.\n\n"
        "Design notes: Name of the workspace execution step these parameters apply to "
        "(WorkspaceSpecModel.execution[].name)"
    )
    description: str | None = Field(None, description="Optional description for documentation purposes")
    enabled: bool | str = Field(
        default=True,
        description="Intended to switch the stage on or off. Accepted and validated, but strata "
        "does not use it yet. Every step runs. A string is a conditional expression, "
        "which is not evaluated either.\n\n"
        "Design notes: Whether this stage runs. A bool is used directly; a string is a "
        "conditional expression evaluated at deploy time. The expression engine is NOT "
        "built in v2 — a string is accepted and carried, but nothing evaluates it yet, "
        "so it must not be relied on for behaviour.",
    )
    on_failure: str = Field(
        default="stop",
        description="What a failed stage should do: `stop` (default), `rollback` or `continue`. "
        "Accepted and validated, but strata does not use it yet.\n\n"
        "Design notes: What to do when this stage fails: 'stop', 'rollback' or "
        "'continue'",
    )
    timeouts: DeploymentStageTimeoutsModel | None = Field(
        None,
        description="Per-phase timeout overrides, applied by `deploy run`.\n\n"
        "Design notes: Per-phase timeout overrides for this stage",
    )
    health_checks: list[DeploymentHealthCheckModel] | None = Field(
        None,
        description="Reachability probes to run after this stage succeeds. Accepted and validated, "
        "but strata does not use it yet.\n\n"
        "Design notes: Reachability probes run after this stage succeeds",
    )
    scripts: ScriptsModel | None = Field(
        None,
        description="Additional scripts for this stage. Accepted and validated, but strata does "
        "not use it yet.\n\n"
        "Design notes: Additional scripts to run for this stage",
    )
    secrets: list[str] | None = Field(
        None,
        description="Secret keys this stage needs injected from the Environment. Accepted and "
        "validated, but strata does not use it yet.\n\n"
        "Design notes: Secret keys this stage needs injected (resolved from the "
        "Environment)",
    )
    namespace: PlatformName | None = Field(
        None,
        description="Restrict this stage to a single namespace. Accepted and validated, but strata "
        "does not use it yet.\n\n"
        "Design notes: Restrict this stage to a single namespace",
    )
    helm_namespaces: list[PlatformName] | None = Field(
        None,
        description="Restrict a Helm or sync stage to these namespaces. Accepted and validated, "
        "but strata does not use it yet. A Helm step deploys every namespace of its "
        "step.\n\n"
        "Design notes: Restrict a Helm/sync stage to these namespaces. Omit to deploy "
        "all of them.",
    )

    @field_validator("on_failure")
    @classmethod
    def validate_on_failure(cls, v: str) -> str:
        """Only three failure behaviours exist."""
        allowed = ("stop", "rollback", "continue")
        if v not in allowed:
            raise ValueError(f"on_failure must be one of {allowed}, got '{v}'")
        return v

    @field_validator("enabled")
    @classmethod
    def validate_enabled_expression(cls, v: bool | str) -> bool | str:
        """A string `enabled` must at least be non-empty.

        Full expression validation needs the engine, which is deliberately
        not built yet — this only catches an obviously-empty value.
        """
        if isinstance(v, str) and not v.strip():
            raise ValueError("enabled: a conditional expression must not be empty")
        return v

    @model_validator(mode="after")
    def validate_unique_health_check_names(self) -> "DeploymentStageModel":
        """Health check names must be unique within the stage."""
        if self.health_checks:
            check_unique_names([h.name for h in self.health_checks], f"health check names in stage '{self.step}'")
        return self


class DeploymentLockingModel(PlatformBaseModel):
    """Pipeline-level state locking, to stop concurrent runs from colliding. Accepted and validated, but
    strata does not use it yet.

    Design notes:
    Pipeline-level state locking, preventing concurrent runs from colliding.
    """

    enabled: bool = Field(default=False, description="Enable pipeline-level state locking")
    strategy: str = Field(
        default="delegate",
        description="'wrap' makes strata take its own lock around the whole run; 'delegate' leaves locking "
        "to the provisioner's own state backend (e.g. Terraform's).",
    )
    wait_timeout: str | None = Field(None, description="How long to wait for a held lock before failing (e.g. '10m')")
    force_unlock_after: str | None = Field(None, description="Age after which a stale lock may be broken (e.g. '1h')")

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        """Only two locking strategies exist."""
        if v not in ("wrap", "delegate"):
            raise ValueError(f"Locking strategy must be 'wrap' or 'delegate', got '{v}'")
        return v


class DeploymentLayersModel(PlatformBaseModel):
    """Position of this deployment in a hierarchy. `follows` names a path convention from the
    Configuration's `spec.paths`, and `segments` gives the values for that convention's segments.

    Strata compares `segments` with the values captured from the directory the document lives in: an
    unknown `follows` is an error, and a disagreeing value is a warning or an error depending on the
    convention's `enforcement`. The segments also shape the audit output path. They are never filled in
    from the directory when absent.

    Design notes:
    Position of this deployment in a hierarchy (v1 ADR-0072).

    **Cross-checked in v2** (docs/design/path-conventions.md,
    `semantic_checks.py`'s `_check_deployment_layers()`): `follows` names a
    `Configuration.spec.paths` convention (`PathConventionModel`); when both
    it and `segments` are declared, `segments` is compared against the
    values captured from this deployment document's own real containing
    directory (`PathConventionModel.pattern`) — an unknown `follows` name
    is an error, a disagreeing segment value is a warning/error per that
    convention's own `enforcement`. Validation only — `segments` is never
    auto-populated from the real path when absent (no consumer of a
    derived value exists yet).
    """

    follows: PlatformName | None = Field(
        None, description="Name of the path convention this deployment's hierarchy follows"
    )
    segments: dict[str, str] | None = Field(
        None, description="Resolved values for that convention's segments (e.g. {'control': 'dev'})"
    )


class DeploymentProviderBindingModel(PlatformBaseModel):
    """Rebind one workspace provider slot to another Provider for this deployment, for example the same
    workspace deployed to a second Azure region. The bound document is used as it is, never patched.
    There is no bare-string shorthand, because a binding only matters when it differs from the
    workspace's default.

    Design notes:
    Rebind one workspace provider slot to another Provider document for this
    deployment (docs/design/provider-slots.md) — e.g. the same workspace
    deployed to a second Azure region. Rebind only: the bound document is used
    as-is, never field-patched. No bare-string shorthand: a binding is only
    meaningful when it differs from the workspace's default.
    """

    slot: PlatformName = Field(description="Workspace provider slot to rebind (spec.providers[].slot)")
    provider: Annotated[PlatformName, References(PlatformKind.PROVIDER)] = Field(
        description="Provider document bound to the slot for this deployment"
    )


class DeploymentAssetBindingModel(PlatformBaseModel):
    """Rebind one workspace asset slot to another Asset for this deployment, for example a customer's own
    API definitions. The bound document is used as it is, never patched. There is no bare-string
    shorthand.

    Design notes:
    Rebind one workspace asset slot to another Asset document for this
    deployment (docs/design/asset-management.md) — e.g. a customer's own API
    definitions. Rebind only: the bound document is used as-is, never
    field-patched. No bare-string shorthand, same as provider bindings.
    """

    slot: PlatformName = Field(description="Workspace asset slot to rebind (spec.assets[].slot)")
    asset: Annotated[PlatformName, References(PlatformKind.ASSET)] = Field(
        description="Asset document bound to the slot for this deployment"
    )


class DeploymentSpecModel(PlatformBaseModel):
    """What to deploy, where and how: the `workspace` whose recipe runs, the `environments` supplying
    values, an optional `tenant` and `version` pins, provider and asset rebinding, and per-step runtime
    `stages`. `partial` and `extends` share a base between deployments.

    Design notes:
    Deployment specification: what to deploy, where, and how it behaves at runtime.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "A step may have `stages` parameters declared only once.",
        "An environment may be listed only once, and a provider slot or asset slot may be bound only once.",
        "A deployable document needs `workspace` and at least one entry in `environments`. A "
        "`partial: true` document, or one with `extends`, is exempt because its missing fields "
        "come from the base.",
    )

    partial: bool = Field(
        default=False,
        description="When true this is a reusable base that is not deployed on its own: required "
        "fields may be absent and it is not checked against the rest of the solution. "
        "Another deployment uses it through `extends`.\n\n"
        "Design notes: When True this is a reusable base, not deployable on its own: "
        "required fields may be absent and Phase 2 validation is skipped. Referenced "
        "by another deployment's `extends`.",
    )
    extends: Annotated[PlatformName, References(PlatformKind.DEPLOYMENT)] | None = Field(
        None,
        description="Name of a base Deployment document whose spec is merged into this one before "
        "validation. Child always wins: nested blocks deep-merge per leaf key, `stages` merge by `step`, "
        "`environments` append after the base's, other lists are replaced. Circular chains are rejected. "
        "Distinct from "
        "`environments` layering, which composes *values*; this composes *structure*.",
    )
    workspace: Annotated[PlatformName, References(PlatformKind.WORKSPACE)] | None = Field(
        None, description="Name of the Workspace document whose provisioning recipe this deployment runs"
    )
    environments: list[Annotated[PlatformName, References(PlatformKind.ENVIRONMENT)]] | None = Field(
        None,
        description="Names of Environment documents supplying values, merged in order — later entries win. "
        "A tenant's own `environments` merge in before these.",
    )
    tenant: Annotated[PlatformName, References(PlatformKind.TENANT)] | None = Field(
        None,
        description="Name of the Tenant this deployment belongs to. Omit for shared/platform deployments "
        "that serve all tenants.",
    )
    configurations: list[Annotated[PlatformName, References(PlatformKind.CONFIGURATION)]] | None = Field(
        None,
        description="Names of additional Configuration documents that apply to this deployment. "
        "Accepted and validated, but strata does not use it yet.\n\n"
        "Design notes: Names of additional Configuration documents that apply to this "
        "deployment",
    )
    version: Annotated[PlatformName, References(PlatformKind.VERSION)] | None = Field(
        None,
        description="Name of the Version document supplying pins for this deployment. When a pinned target "
        "(image, chart, remote ref, tool) is declared elsewhere, the pin wins. Omit to use each document's "
        "own declared versions.",
    )
    layers: DeploymentLayersModel | None = Field(
        None,
        description="Hierarchy position: a path convention to follow and the segment values for "
        "it.\n\n"
        "Design notes: Hierarchy position (inert in v2 — see DeploymentLayersModel)",
    )
    locking: DeploymentLockingModel | None = Field(
        None,
        description="Pipeline-level state locking. Accepted and validated, but strata does not use "
        "it yet.\n\n"
        "Design notes: Pipeline-level state locking",
    )
    stages: list[DeploymentStageModel] | None = Field(
        None, description="Runtime parameters per workspace provisioning step"
    )
    providers: list[DeploymentProviderBindingModel] | None = Field(
        None,
        description="Rebind workspace provider slots to other Provider documents for this deployment. "
        "Merged by `slot` across `extends` (child wins), so a partial base can bind a region once.",
    )
    assets: list[DeploymentAssetBindingModel] | None = Field(
        None,
        description="Rebind workspace asset slots to other Asset documents for this deployment. "
        "Merged by `slot` across `extends` (child wins), so a partial base can bind some slots and a "
        "leaf others.",
    )
    lifecycle: CommonLifecycleModel | None = Field(
        None,
        description="Hook scripts per phase name. Accepted and validated, but strata does not run "
        "lifecycle hooks yet.\n\n"
        "Design notes: Deployment lifecycle phases",
    )
    properties: dict[str, Any] | None = Field(
        None,
        description="Deployment properties, the last merge layer over the tenant's and the "
        "environments' properties, passed to Terraform as `strata.properties`. Add an "
        "`attributeconfig` with `target.kind: deployment` to validate them.\n\n"
        "Design notes: Deployment properties, the last merge layer over tenant and "
        "environment properties",
    )
    custom: dict[str, Any] | None = Field(
        None,
        description="Free-form values merged over the tenant's and the environments' `custom` "
        "values (the deployment wins), passed to Terraform as `strata.custom`. Strata "
        "does not interpret them.\n\n"
        "Design notes: Custom user-defined data for scripts or extensions. Merges "
        "across the Tenant/Environment/Deployment chain like `properties`, delivered "
        "as one JSON-encoded `TF_VAR_custom` env var for the whole merged dict — not "
        "split into a separate env var per key.",
    )

    @model_validator(mode="after")
    def validate_unique_stage_steps(self) -> "DeploymentSpecModel":
        """Each provisioning step may have parameters declared only once."""
        if self.stages:
            check_unique_names([s.step for s in self.stages], "stage step references in deployment")
        return self

    @model_validator(mode="after")
    def validate_unique_environments(self) -> "DeploymentSpecModel":
        """A repeated environment reference would merge twice."""
        if self.environments:
            check_unique_names(self.environments, "environment references in deployment")
        return self

    @model_validator(mode="after")
    def validate_unique_provider_slots(self) -> "DeploymentSpecModel":
        """A slot bound twice would be ambiguous."""
        if self.providers:
            check_unique_names([b.slot for b in self.providers], "provider slot bindings in deployment")
        return self

    @model_validator(mode="after")
    def validate_unique_asset_slots(self) -> "DeploymentSpecModel":
        """A slot bound twice would be ambiguous."""
        if self.assets:
            check_unique_names([b.slot for b in self.assets], "asset slot bindings in deployment")
        return self

    @model_validator(mode="after")
    def validate_complete_unless_partial(self) -> "DeploymentSpecModel":
        """A deployable document needs a workspace and at least one environment.

        Two kinds of document are exempt, because neither is deployable as
        written:

        - a `partial: true` base — that is exactly what the flag means;
        - a document with `extends` — its missing fields are expected to come
          from the base. The merged result is validated without `extends`
          (`merge_deployment_specs` strips it), so the check still applies to
          whatever is actually deployed.
        """
        if self.partial or self.extends:
            return self
        missing = [
            field for field, value in (("workspace", self.workspace), ("environments", self.environments)) if not value
        ]
        if missing:
            raise ValueError(
                f"Deployment is missing required field(s) {missing}. Set 'partial: true' if this is a "
                "reusable base, or 'extends' if another document supplies them."
            )
        return self


class DeploymentMetaModel(PlatformBaseModel):
    """Deployment metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique deployment name")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")


class DeploymentModel(PlatformBaseModel):
    """A deployment of a workspace: runs the workspace's recipe with the values of its environments, for an
    optional tenant and pinned version. Create one per place a workspace is deployed, such as a
    customer, a ring or a region, and share structure through a `partial: true` base and `extends`.

    Design notes:
    Root model for a deployment configuration file.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "A deployment cannot extend itself; longer extends cycles are rejected when the chain is resolved.",
    )

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for deployment configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.DEPLOYMENT,
        frozen=True,
        description="Platform kind (always 'deployment')",
    )
    meta: DeploymentMetaModel = Field(description="Deployment metadata (name, annotations, labels, tags)")
    spec: DeploymentSpecModel = Field(description="Deployment specification (workspace, environments, stages)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.DEPLOYMENT)

    @model_validator(mode="after")
    def validate_not_self_extending(self) -> "DeploymentModel":
        """A deployment cannot extend itself — the trivial cycle.

        Longer chains need the document index and are checked by the
        controller during extends resolution.
        """
        if self.spec.extends and self.spec.extends == self.meta.name:
            raise ValueError(f"Deployment '{self.meta.name}' cannot extend itself.")
        return self
