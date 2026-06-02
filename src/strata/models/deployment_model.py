#!/usr/bin/env python3
"""Pydantic model for deployment configuration validation."""

from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    ScriptsModel,
)


class DeploymentFileReference(BaseModel):
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


class DeploymentWorkspaceModel(DeploymentFileReference):
    """Model for deployment workspace file reference."""

    pass


class ApproverType(str, Enum):
    """Supported approver identity types."""

    GITHUB_TEAM = "github-team"
    ADO_GROUP = "ado-group"
    USER = "user"


class ApproverRef(BaseModel):
    """A single named approver entry."""

    type: ApproverType = Field(description="Approver identity type: github-team | ado-group | user")
    value: str = Field(description="Approver identifier — team slug, group name, or user address")


class DeploymentApprovalModel(BaseModel):
    """Deployment-level approval metadata.

    Presence of this block signals that approvals are declared for this deployment.
    An empty ``approvers`` dict is silently treated as no gate.
    The CLI emits this metadata for audit; enforcement is done by the CI/CD system.
    """

    approvers: Dict[str, ApproverRef] = Field(
        default_factory=dict,
        description="Named approver entries. Key is a short identifier used as a cross-reference from stage overrides.",
    )


class DeploymentStageApprovalModel(BaseModel):
    """Per-stage approval override: restricts which spec-level approvers apply to this stage."""

    approvers: List[str] = Field(
        description="List of approver keys from spec.approvals.approvers that apply to this stage"
    )


class HealthCheckModel(BaseModel):
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


class DeploymentStageTimeoutsModel(BaseModel):
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


class DeploymentStageModel(BaseModel):
    """Model for a deployment stage (pipeline execution step).

    Stages enable:
    - Sequential multi-step execution (e.g., provision → configure → verify)
    - Execution ordering and dependency management (DAG execution via depends_on)
    - Failure handling per stage

    Provisioner Selection (mutually exclusive — exactly one required at runtime):
    - 'provisioner': name of a workspace provisioner entry (explicit, no filtering)
    - 'topology':    name of a workspace topology entry; derives the provisioner
                     type from the topology definition and filters to its resources

    Stages do NOT define environments or approvals - those are deployment-level.
    Stages do NOT affect artifact paths - use deployment.spec.deployment for layering.
    """

    name: PlatformName = Field(
        description="Unique stage name - pipeline step identifier (e.g., 'provision', 'configure', 'verify')"
    )
    type: str = Field(
        ...,  # Required
        description="Stage type - determines provisioner selection strategy and execution semantics",
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
    approval: Optional[DeploymentStageApprovalModel] = Field(
        None,
        description="Per-stage approval override: list of approver keys from spec.approvals.approvers. "
        "Absent means no stage-level restriction — spec-level approvers apply as-is.",
    )
    timeouts: Optional[DeploymentStageTimeoutsModel] = Field(
        None,
        description="Per-operation subprocess timeouts. Overrides TerraformIntegration defaults "
        "(init=300s, validate=60s, plan=600s, apply=1800s, destroy=1800s).",
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


class DeploymentSpecModel(BaseModel):
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
    workspace: DeploymentWorkspaceModel = Field(description="Name of the associated workspace for this deployment")
    environments: Annotated[
        List[str],
        Field(
            min_length=1,
            description="List of environment file paths for this deployment (later files override earlier ones)",
        ),
    ]
    approvals: Optional[DeploymentApprovalModel] = Field(None, description="Approval configuration for this deployment")
    stages: Optional[List[DeploymentStageModel]] = Field(
        None,
        description="Optional deployment stages for multi-step execution (if not specified, single-step execution)",
    )

    @model_validator(mode="after")
    def validate_unique_names(self) -> "DeploymentSpecModel":
        """Validate that stage and configuration names are unique."""
        # Validate unique stage names
        if self.stages:
            stage_names = [stage.name for stage in self.stages]
            if len(stage_names) != len(set(stage_names)):
                duplicates = [name for name in stage_names if stage_names.count(name) > 1]
                raise ValueError(f"Duplicate stage names found: {set(duplicates)}")

        if self.configurations:
            config_names = [config.name for config in self.configurations]
            if len(config_names) != len(set(config_names)):
                duplicates = [name for name in config_names if config_names.count(name) > 1]
                raise ValueError(f"Duplicate configuration names found: {set(duplicates)}")

        return self

    @model_validator(mode="after")
    def validate_stage_approval_refs(self) -> "DeploymentSpecModel":
        """Validate that stage approval keys reference declared spec-level approvers."""
        if not self.stages:
            return self
        known_keys: set[str] = set(self.approvals.approvers.keys()) if self.approvals else set()
        for stage in self.stages:
            if stage.approval:
                for key in stage.approval.approvers:
                    if key not in known_keys:
                        raise ValueError(
                            f"Stage '{stage.name}' references unknown approver key '{key}'. "
                            f"Known keys: {sorted(known_keys) or '(none — spec.approvals not declared)'}"
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


class DeploymentMetaModel(BaseModel):
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


class DeploymentModel(BaseModel):
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
