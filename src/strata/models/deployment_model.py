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

from typing import Any

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
from strata.utils.names import check_unique_names


class DeploymentHealthCheckModel(PlatformBaseModel):
    """A post-stage reachability probe."""

    name: PlatformName = Field(description="Unique health check name within the stage")
    type: str = Field(description="Check type: 'http' or 'tcp'")
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
    """Per-phase timeouts in seconds, overriding provisioner defaults."""

    setup: int | None = Field(None, ge=1, description="Timeout for the setup phase")
    check: int | None = Field(None, ge=1, description="Timeout for the check/validate phase")
    plan: int | None = Field(None, ge=1, description="Timeout for the plan phase")
    apply: int | None = Field(None, ge=1, description="Timeout for the apply phase")
    destroy: int | None = Field(None, ge=1, description="Timeout for the destroy phase")


class DeploymentStageModel(PlatformBaseModel):
    """Runtime parameters for one provisioning step.

    Names a `ProvisioningStepModel` from the workspace's recipe via `step`;
    it does not redefine what runs (see module docstring).
    """

    step: PlatformName = Field(
        description="Name of the workspace execution step these parameters apply to "
        "(WorkspaceSpecModel.execution[].name)"
    )
    description: str | None = Field(None, description="Optional description for documentation purposes")
    enabled: bool | str = Field(
        default=True,
        description="Whether this stage runs. A bool is used directly; a string is a conditional expression "
        "evaluated at deploy time. The expression engine is NOT built in v2 — a string is accepted and "
        "carried, but nothing evaluates it yet, so it must not be relied on for behaviour.",
    )
    on_failure: str = Field(
        default="stop",
        description="What to do when this stage fails: 'stop', 'rollback' or 'continue'",
    )
    timeouts: DeploymentStageTimeoutsModel | None = Field(
        None, description="Per-phase timeout overrides for this stage"
    )
    health_checks: list[DeploymentHealthCheckModel] | None = Field(
        None, description="Reachability probes run after this stage succeeds"
    )
    scripts: ScriptsModel | None = Field(None, description="Additional scripts to run for this stage")
    secrets: list[str] | None = Field(
        None, description="Secret keys this stage needs injected (resolved from the Environment)"
    )
    namespace: PlatformName | None = Field(None, description="Restrict this stage to a single namespace")
    helm_namespaces: list[PlatformName] | None = Field(
        None, description="Restrict a Helm/sync stage to these namespaces. Omit to deploy all of them."
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
    """Pipeline-level state locking, preventing concurrent runs from colliding."""

    enabled: bool = Field(default=False, description="Enable pipeline-level state locking")
    strategy: str = Field(
        default="delegate",
        description="'wrap' makes strata take its own lock around the whole run; 'delegate' leaves locking "
        "to the provisioner's own state backend (e.g. Terraform's).",
    )
    wait_timeout: str | None = Field(
        None, description="How long to wait for a held lock before failing (e.g. '10m')"
    )
    force_unlock_after: str | None = Field(
        None, description="Age after which a stale lock may be broken (e.g. '1h')"
    )

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        """Only two locking strategies exist."""
        if v not in ("wrap", "delegate"):
            raise ValueError(f"Locking strategy must be 'wrap' or 'delegate', got '{v}'")
        return v


class DeploymentLayersModel(PlatformBaseModel):
    """Position of this deployment in a hierarchy (v1 ADR-0072).

    **Inert in v2.** `follows` names a path convention from
    `configuration.spec.paths`, which v2 has not ported (ADR-0003 defers it).
    Modelled anyway because 10 of 15 real deployments declare it, so dropping
    it would block migration — but nothing resolves or validates it yet.
    """

    follows: PlatformName | None = Field(
        None, description="Name of the path convention this deployment's hierarchy follows"
    )
    segments: dict[str, str] | None = Field(
        None, description="Resolved values for that convention's segments (e.g. {'control': 'dev'})"
    )


class DeploymentSpecModel(PlatformBaseModel):
    """Deployment specification: what to deploy, where, and how it behaves at runtime."""

    partial: bool = Field(
        default=False,
        description="When True this is a reusable base, not deployable on its own: required fields may be "
        "absent and Phase 2 validation is skipped. Referenced by another deployment's `extends`.",
    )
    extends: PlatformName | None = Field(
        None,
        description="Name of a base Deployment document whose spec is merged into this one before "
        "validation. Child always wins: top-level fields are replaced, `stages` merge by `step`, "
        "`environments` append after the base's. Circular chains are rejected. Distinct from "
        "`environments` layering, which composes *values*; this composes *structure*.",
    )
    workspace: PlatformName | None = Field(
        None, description="Name of the Workspace document whose provisioning recipe this deployment runs"
    )
    environments: list[PlatformName] | None = Field(
        None,
        description="Names of Environment documents supplying values, merged in order — later entries win. "
        "A tenant's own `environments` merge in before these.",
    )
    tenant: PlatformName | None = Field(
        None,
        description="Name of the Tenant this deployment belongs to. Omit for shared/platform deployments "
        "that serve all tenants.",
    )
    configurations: list[PlatformName] | None = Field(
        None, description="Names of additional Configuration documents that apply to this deployment"
    )
    layers: DeploymentLayersModel | None = Field(
        None, description="Hierarchy position (inert in v2 — see DeploymentLayersModel)"
    )
    locking: DeploymentLockingModel | None = Field(None, description="Pipeline-level state locking")
    stages: list[DeploymentStageModel] | None = Field(
        None, description="Runtime parameters per workspace provisioning step"
    )
    lifecycle: CommonLifecycleModel | None = Field(None, description="Deployment lifecycle phases")
    properties: dict[str, Any] | None = Field(
        None, description="Deployment properties, the last merge layer over tenant and environment properties"
    )
    custom: dict[str, Any] | None = Field(
        None, description="Custom user-defined data for scripts or extensions (e.g. becomes env vars)"
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
            field
            for field, value in (("workspace", self.workspace), ("environments", self.environments))
            if not value
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
    """Root model for a deployment configuration file."""

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
