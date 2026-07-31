#!/usr/bin/env python3
"""Pydantic model for deployment configuration validation."""

from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    ScriptsModel,
    check_unique_names,
)
from strata.models.gate_model import DeploymentGateModel
from strata.models.promotion_model import DeploymentPromotionModel


class DeploymentFileReference(PlatformBaseModel):
    """Reference to a local deployment file that must exist."""

    name: Annotated[
        str,
        StringConstraints(min_length=1, pattern=r"^[a-z][a-z0-9_]*$", strip_whitespace=True),
    ] = Field(description="Unique reference name")
    file: str = Field(description="Path to the file (resolved and validated at load time)")
    description: Optional[str] = Field(None, description="Optional description of the referenced file")


class DeploymentConfigurationModel(DeploymentFileReference):
    """Model for a deployment configuration file reference."""

    pass


class DeploymentEnvironmentModel(DeploymentFileReference):
    """Model for a deployment environment file reference."""

    pass


class DeploymentEnvironmentRef(PlatformBaseModel):
    """Entry in a deployment's ``spec.environments`` list.

    Supports both shorthand (bare string path) and full object form.
    A bare string is automatically coerced to ``DeploymentEnvironmentRef(file=<path>)``.

    The optional ``scope`` annotation is used by the promotion system to identify
    which file to edit during wave execution:

    - ``"shared"`` — the environment file covers all deployments for this environment
      (typically the shared ``environments/<name>.yaml``).  This is the file edited by
      the final (all) wave.
    - A layer name (e.g. ``"tenant"``) — the file is a per-layer override specific to
      this deployment.  Promotion edits this file for canary/early waves.
    - ``None`` (default) — no scope annotation; the promotion system treats this entry
      as a regular environment file without wave-specific targeting.

    Example — shorthand (backward-compatible)::

        environments:
          - environments/production.yaml

    Example — full object with scope::

        environments:
          - file: environments/production.yaml
            scope: shared
          - file: environments/tenants/acme.yaml
            scope: tenant
    """

    file: str = Field(description="Path to the environment YAML file")
    scope: Optional[str] = Field(
        None,
        description=(
            "Promotion scope annotation. Use 'shared' for the shared environment file, "
            "or a layer name (e.g. 'tenant') for per-layer override files. "
            "Omit for unannotated entries."
        ),
    )


class DeploymentVersionRef(PlatformBaseModel):
    """Entry in a deployment's ``spec.versions`` list.

    Supports both shorthand (bare string path) and full object form.
    A bare string is automatically coerced to ``DeploymentVersionRef(file=<path>)``.

    Files are applied in list order — later entries win over earlier entries.
    The convention is to list the human-edited manifest first and the machine-generated
    lock file second, so the lock always takes precedence.

    Example — shorthand::

        versions:
          - versions/dev.manifest.yaml
          - versions/dev.yaml

    Example — full object (reserved for future fields)::

        versions:
          - file: "@config/versions/prd.manifest.yaml"
          - file: "@config/versions/prd.yaml"
    """

    file: str = Field(description="Path to the version-manifest or version-lock YAML file")


class DeploymentWorkspaceModel(DeploymentFileReference):
    """Model for deployment workspace file reference."""

    pass


class HealthCheckModel(PlatformBaseModel):
    """A single health check applied to a deployment stage after provisioning.

    Two check types:
    - ``http``  — HTTP(S) GET; passes if response code matches ``expect_status``.
    - ``tcp``   — TCP connect to ``host:port``; passes if connection succeeds.

    Both types also support checking Terraform output values:
    - ``output_key`` — name of a Terraform output whose value should be used as
      the URL (http) or ``host:port`` (tcp).  Takes precedence over ``url`` /
      ``host`` + ``port`` when present.

    Examples (YAML)::

        health_checks:
          - name: api-endpoint
            type: http
            output_key: api_url
            expect_status: 200
            timeout: 10
          - name: db-port
            type: tcp
            host: 10.0.0.5
            port: 5432
    """

    name: Annotated[
        str,
        StringConstraints(min_length=1, strip_whitespace=True),
    ] = Field(description="Unique check name within the stage")
    type: Literal["http", "tcp"] = Field(description="Check type: 'http' or 'tcp'")

    # --- HTTP fields ---
    url: Optional[str] = Field(None, description="URL to GET (http type). Overridden by output_key.")
    expect_status: int = Field(default=200, description="Expected HTTP status code (http type).")

    # --- TCP fields ---
    host: Optional[str] = Field(None, description="Hostname or IP (tcp type). Overridden by output_key.")
    port: Optional[int] = Field(None, description="TCP port (tcp type).")

    # --- Shared ---
    output_key: Optional[str] = Field(
        None,
        description="Terraform output key whose value provides the URL or host:port target.",
    )
    timeout: int = Field(default=10, description="Connection / request timeout in seconds.")

    @model_validator(mode="after")
    def validate_check_fields(self) -> "HealthCheckModel":
        if self.type == "http" and not self.url and not self.output_key:
            raise ValueError("Health check type 'http' requires 'url' or 'output_key'.")
        if self.type == "tcp":
            if not self.output_key and (not self.host or self.port is None):
                raise ValueError("Health check type 'tcp' requires 'host'+'port' or 'output_key'.")
        return self


class DeploymentStageTimeoutsModel(PlatformBaseModel):
    """Per-step subprocess timeouts for a deployment stage (all deployer types).

    Field names match deployer step names so the same schema works for
    Terraform, Ansible, and script-based stages.  All fields are optional;
    omitting a field keeps the deployer's built-in default.

    Examples (YAML)::

        stages:
          - name: infrastructure
            type: infrastructure
            timeouts:
              setup: 120   # fail fast if backend is unreachable
              plan: 300
              apply: 1200  # 20 min — tighter than the 30 min default
    """

    setup: Optional[int] = Field(
        None,
        description="Timeout in seconds for the setup step (tf init / galaxy install / deploy_setup). Default varies by deployer.",
    )
    check: Optional[int] = Field(
        None,
        description="Timeout in seconds for the check step (tf validate / syntax-check / deploy_check). Default varies by deployer.",
    )
    plan: Optional[int] = Field(
        None,
        description="Timeout in seconds for the plan step. Default varies by deployer.",
    )
    apply: Optional[int] = Field(
        None,
        description="Timeout in seconds for the apply step. Default varies by deployer.",
    )
    destroy: Optional[int] = Field(
        None,
        description="Timeout in seconds for the destroy step. Default varies by deployer.",
    )


class SyncBackendModel(PlatformBaseModel):
    """Backend configuration for a sync provisioner stage (argocd, flux).

    Specifies which integration instance handles this stage and where rendered
    output is committed. Analogous to the terraform backend block — the stage
    decides where its output goes, not the integration itself.
    """

    integration: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description=(
            "Integration name — references configuration.spec.integrations[].name. "
            "The named integration must have the 'sync' capability. "
            "Allows multiple instances of the same type (e.g., 'argocd-prod' vs 'argocd-staging')."
        )
    )
    remote: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description=(
            "Remote name — references a strata remote (strata repo add) where the "
            "rendered controller input file is committed. The provisioner commits to "
            "this remote during deploy."
        )
    )


class DeploymentStageModel(PlatformBaseModel):
    """Model for a deployment stage (pipeline execution step).

    Stages enable:
    - Sequential multi-step execution (e.g., provision → configure → verify)
    - Execution ordering and dependency management (DAG execution via depends_on)
    - Failure handling per stage

    Provisioner Selection (mutually exclusive — exactly one required at runtime):
    - 'provisioner': name of a workspace provisioner entry (explicit, no filtering)
    - 'topology':    name of a workspace topology entry; derives the provisioner
                     type from the topology definition and filters to its resources

    Stages do NOT define environments or gates - those are deployment-level.
    Stages do NOT affect artifact paths - use deployment.spec.deployment for layering.
    """

    name: PlatformName = Field(
        description="Unique stage name - pipeline step identifier (e.g., 'provision', 'configure', 'verify')"
    )
    description: Optional[str] = Field(
        None,
        description="Optional human-readable description of what this stage does (informational only — does not affect routing)",
    )
    provisioner: Optional[str] = Field(
        None,
        description="Explicit provisioner name from workspace (required if multiple provisioners match type)",
    )
    topology: Optional[str] = Field(
        None,
        description=(
            "Topology name from workspace (mutually exclusive with 'provisioner'). "
            "Derives the provisioner type from the topology definition and scopes "
            "execution to that topology's resources."
        ),
    )
    scope: Optional[str] = Field(
        None,
        description=(
            "Free-form label for CLI-level stage filtering. "
            "Pass --scope <label> to strata deploy run to execute only stages "
            "whose scope matches the supplied value. Omit to run all stages."
        ),
    )
    scripts: Optional[ScriptsModel] = Field(
        None,
        description="Additional scripts to execute for this stage (extends provisioner behavior)",
    )
    depends_on: Optional[List[str]] = Field(
        None,
        description="List of stage names this stage depends on (enables DAG execution)",
    )

    @field_validator("depends_on", mode="before")
    @classmethod
    def coerce_depends_on(cls, v):
        if isinstance(v, str):
            return [v]
        return v

    on_failure: Literal["stop", "rollback", "continue"] = Field(
        default="stop",
        description="Action to take on stage failure: 'stop' halts pipeline, 'rollback' reverts, 'continue' proceeds",
    )
    health_checks: Optional[List[HealthCheckModel]] = Field(
        None,
        description="Health checks to run against this stage after provisioning.",
    )
    timeouts: Optional[DeploymentStageTimeoutsModel] = Field(
        None,
        description="Per-operation subprocess timeouts. Overrides TerraformIntegration defaults "
        "(init=300s, validate=60s, plan=600s, apply=1800s, destroy=1800s).",
    )
    secrets: Optional[List[str]] = Field(
        None,
        description=(
            "Allowlist of secret keys this stage may access from STRATA_SENSITIVE. "
            "Only these keys are passed to the deployer. "
            "Omit or set to null for no secret access. "
            "Use ['*'] to grant access to all secrets (escape hatch)."
        ),
    )
    namespace: Optional[str] = Field(
        None,
        description=(
            "Namespace name to scope this sync stage — references workspace.spec.namespaces[].name. "
            "When set, the sync provisioner filters modules to those declared in this namespace "
            "and injects 'namespace' as a single object into the Jinja2 template context. "
            "Omit to include all namespaces (template is responsible for iteration). "
            "Only meaningful for sync provisioners (argocd, flux)."
        ),
    )
    backend: Optional[SyncBackendModel] = Field(
        None,
        description=(
            "Sync backend configuration — specifies the integration instance and remote "
            "for sync provisioners (argocd, flux). "
            "Analogous to terraform backend: the stage controls where output is committed."
        ),
    )

    @model_validator(mode="after")
    def validate_provisioner_selection(self) -> "DeploymentStageModel":
        """Validate provisioner/topology selection logic.

        Rules:
        1. Cannot specify both 'provisioner' and 'topology' (mutually exclusive)
        2. Workspace-level existence checks happen at runtime in the deployer
           (workspace context is not available inside the model validator)
        """
        if self.provisioner and self.topology:
            raise ValueError(
                f"Stage '{self.name}': 'provisioner' and 'topology' are mutually exclusive — "
                "use 'topology' to derive the provisioner from the workspace topology definition, "
                "or 'provisioner' to name a workspace provisioner entry directly."
            )
        return self


class DeploymentLockingModel(PlatformBaseModel):
    """Locking behaviour for spec.locking.

    Connection config is not declared here — it is derived automatically from
    ``provisioner.backend`` on the workspace IaC provisioner used by each stage.
    The lock backend type is inferred from ``provisioner.backend.type``
    (e.g. ``azurerm``, ``terraform_cloud``, ``s3``, ``consul``).
    """

    enabled: bool = Field(default=False, description="Enable pipeline-level state locking")
    strategy: Literal["wrap", "delegate"] = Field(
        default="wrap",
        description=(
            "wrap: strata acquires the lock before the first stage and holds it for the entire pipeline. "
            "delegate: strata does not lock — relies on the backend's native locking "
            "(TFC run queue, Terraform state lock). Only safe for pure-TFC remote execution pipelines."
        ),
    )
    wait_timeout: str = Field(
        default="30m",
        description="How long to wait for a held lock before failing (e.g. 30m, 1h). Supports h/m/s suffixes.",
    )
    force_unlock_after: str = Field(
        default="8h",
        description="Stale lock TTL — auto-release a lock held longer than this duration (e.g. 8h). "
        "Supports h/m/s suffixes.",
    )


class DeploymentSpecModel(PlatformBaseModel):
    """Model for deployment specification (properties, workspace, stages)."""

    configurations: Optional[List[DeploymentConfigurationModel]] = Field(
        None, description="Optional configuration list"
    )
    lifecycle: Optional[CommonLifecycleModel] = Field(None, description="Deployment lifecycle phases")
    properties: Optional[Dict[str, Any]] = Field(
        None,
        description="Deployment-specific properties and configurations. "
        "Validated against configuration.spec.deployment.properties schema if defined.",
    )
    custom: Optional[Dict[str, Any]] = Field(
        None, description="Deployment-specific custom properties and configurations"
    )
    layers: Optional[Dict[str, str]] = Field(
        None,
        description="Deployment layer values (keys must match configuration.spec.layering[].name). "
        "Defines the artifact path location for this deployment.",
    )
    tenant: Optional[PlatformName] = Field(
        None,
        description=(
            "Optional tenant code this deployment belongs to. "
            "Must match a tenant YAML file in tenants/<code>.yaml. "
            "Validated against the filesystem during Phase 2. "
            "Omit for shared/platform deployments that serve all tenants."
        ),
    )
    partial: bool = Field(
        False,
        description=(
            "When True, this file is a reusable base that is not deployable in isolation. "
            "Phase 2 (semantic) validation is skipped; required fields such as "
            "'workspace' and 'environments' need not be present. "
            "strata deploy rejects partial files outright."
        ),
    )
    extends: Optional[str] = Field(
        None,
        description=(
            "@repo/path reference to a base deployment file whose spec is merged into "
            "this file before validation and execution. Top-level fields are replaced; "
            "stages are merged by name; environments are appended after the base list. "
            "Circular references are rejected at load time."
        ),
    )
    workspace: Optional[DeploymentWorkspaceModel] = Field(
        None, description="Name of the associated workspace for this deployment"
    )
    environments: Optional[
        Annotated[
            List[DeploymentEnvironmentRef],
            Field(
                min_length=1,
                description="List of environment file paths (or scoped refs) for this deployment (later files override earlier ones)",
            ),
        ]
    ] = None

    @field_validator("environments", mode="before")
    @classmethod
    def coerce_environment_strings(cls, v: Any) -> Any:
        """Coerce bare strings to DeploymentEnvironmentRef dicts for backward compatibility."""
        if isinstance(v, list):
            return [{"file": item} if isinstance(item, str) else item for item in v]
        return v

    versions: Optional[List[DeploymentVersionRef]] = Field(
        None,
        description=(
            "Optional list of version file paths (version-manifest or version-lock) for this deployment. "
            "Applied in list order — later entries win. Convention: manifest first, lock second. "
            "Omit entirely for deployments that have not adopted the version system."
        ),
    )

    @field_validator("versions", mode="before")
    @classmethod
    def coerce_version_strings(cls, v: Any) -> Any:
        """Coerce bare strings to DeploymentVersionRef dicts."""
        if isinstance(v, list):
            return [{"file": item} if isinstance(item, str) else item for item in v]
        return v

    locking: Optional[DeploymentLockingModel] = Field(
        None, description="Pipeline locking behaviour for concurrent deploy protection"
    )
    promotion: Optional[DeploymentPromotionModel] = Field(
        None,
        description="Promotion wave assignment for this deployment (opt-in; defaults to last wave when absent)",
    )
    stages: Optional[List[DeploymentStageModel]] = Field(
        None,
        description="Optional deployment stages for multi-step execution (if not specified, single-step execution)",
    )
    gates: Optional[List[DeploymentGateModel]] = Field(
        None,
        description="Hand-off gates for this deployment (approval, cost_review, security_review, verify, "
        "scheduled, incident, cab). See ADR-0057/ADR-0059.",
    )

    @model_validator(mode="after")
    def validate_unique_names(self) -> "DeploymentSpecModel":
        """Validate that stage, gate, and configuration names are unique."""
        if self.stages:
            check_unique_names([stage.name for stage in self.stages], "stage names")

        if self.gates:
            check_unique_names([gate.name for gate in self.gates], "gate names")

        if self.configurations:
            check_unique_names([config.name for config in self.configurations], "configuration names")

        return self

    @model_validator(mode="after")
    def validate_gate_scope_refs(self) -> "DeploymentSpecModel":
        """Validate that a gate's scope references declared stage names."""
        if not self.gates:
            return self
        known_stages: set[str] = {stage.name for stage in self.stages} if self.stages else set()
        for gate in self.gates:
            if gate.scope == "all":
                continue
            for stage_name in gate.scope:
                if stage_name not in known_stages:
                    raise ValueError(
                        f"Gate '{gate.name}' scope references unknown stage '{stage_name}'. "
                        f"Known stages: {sorted(known_stages) or '(none declared)'}"
                    )
        return self

    @model_validator(mode="after")
    def validate_stage_dependencies(self) -> "DeploymentSpecModel":
        """Validate that stage dependencies form a valid DAG (no cycles, valid references)."""
        if not self.stages:
            return self

        stage_names = {stage.name for stage in self.stages}
        errors = []

        # Check that all depends_on references are valid
        for stage in self.stages:
            if stage.depends_on:
                for dep in stage.depends_on:
                    if dep not in stage_names:
                        errors.append(f"Stage '{stage.name}' depends on undefined stage '{dep}'")
                    if dep == stage.name:
                        errors.append(f"Stage '{stage.name}' cannot depend on itself")

        if errors:
            raise ValueError("; ".join(errors))

        # Check for circular dependencies (simple cycle detection)
        visited = set()
        rec_stack = set()

        def has_cycle(stage_name: str) -> bool:
            """DFS-based cycle detection."""
            visited.add(stage_name)
            rec_stack.add(stage_name)

            # Find stage object
            stage = next((s for s in self.stages or [] if s.name == stage_name), None)
            if stage and stage.depends_on:
                for dep in stage.depends_on:
                    if dep not in visited:
                        if has_cycle(dep):
                            return True
                    elif dep in rec_stack:
                        return True

            rec_stack.remove(stage_name)
            return False

        for stage in self.stages:
            if stage.name not in visited:
                if has_cycle(stage.name):
                    raise ValueError(f"Circular dependency detected in stages involving '{stage.name}'")

        return self


class DeploymentMetaModel(PlatformBaseModel):
    """Model for deployment metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique deployment name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")


class DeploymentModel(PlatformBaseModel):
    """Root model for a deployment configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for workspace configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.DEPLOYMENT,
        frozen=True,
        description="Platform kind (always 'Deployment')",
    )
    meta: DeploymentMetaModel = Field(description="Deployment metadata (name, annotations, labels, tags)")
    spec: DeploymentSpecModel = Field(description="Deployment specification (properties, workspace, environment)")
