#!/usr/bin/env python3
"""Pydantic model for deployment configuration validation."""

from typing import Annotated, List, Dict, Any, Optional, Literal

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    model_validator,
)

from xyz_platform.models.common_models import (
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
        StringConstraints(
            min_length=1, pattern=r"^[a-z][a-z0-9_]*$", strip_whitespace=True
        ),
    ] = Field(description="Unique reference name")
    file: str = Field(
        description="Path to the file (resolved and validated at load time)"
    )
    description: Optional[str] = Field(
        None, description="Optional description of the referenced file"
    )


class DeploymentConfigurationModel(DeploymentFileReference):
    """Model for a deployment configuration file reference."""

    pass


class DeploymentEnvironmentModel(DeploymentFileReference):
    """Model for a deployment environment file reference."""

    pass


class DeploymentWorkspaceModel(DeploymentFileReference):
    """Model for deployment workspace file reference."""

    pass


class DeploymentApprovalModel(BaseModel):
    """Model for stage approval configuration."""

    type: Literal["auto", "manual"] = Field(
        description="Approval type: 'auto' proceeds automatically, 'manual' requires explicit approval"
    )
    approvers: Optional[List[str]] = Field(
        None,
        description="List of approver identifiers for manual approval (e.g., team names, email addresses)",
    )
    timeout: Optional[str] = Field(
        None,
        description="Timeout for manual approval (e.g., '72h', '7d'). If exceeded, stage fails.",
    )

    @model_validator(mode="after")
    def validate_manual_approval(self) -> "DeploymentApprovalModel":
        """Validate that manual approvals have approvers defined."""
        if self.type == "manual" and not self.approvers:
            raise ValueError(
                "Manual approval requires 'approvers' to be specified (list of approver identifiers)"
            )
        return self


class DeploymentStageModel(BaseModel):
    """Model for a deployment stage (pipeline execution step).

    Stages enable:
    - Sequential multi-step execution (e.g., provision → configure → verify)
    - Execution ordering and dependency management (DAG execution via depends_on)
    - Failure handling per stage
    - Semantic provisioner selection via type with explicit override options

    Provisioner Selection Priority:
    1. Explicit 'provisioner' field (highest priority)
    2. Explicit 'topology' field (topology defines provisioner)
    3. Convention from 'type' field (auto-select based on stage type)
    4. ERROR if ambiguous (multiple provisioners match type)

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
        description="Explicit topology name from workspace (alternative to provisioner - topology defines provisioner and resources)",
    )
    scope: str = Field(
        default="all",
        description="Resource scope filter: 'all' (deploy everything), 'changed' (incremental), 'tagged' (filtered)",
    )
    scripts: Optional[ScriptsModel] = Field(
        None,
        description="Additional scripts to execute for this stage (extends provisioner behavior)",
    )
    depends_on: Optional[List[str]] = Field(
        None,
        description="List of stage names this stage depends on (enables DAG execution)",
    )
    on_failure: Literal["stop", "rollback", "continue"] = Field(
        default="stop",
        description="Action to take on stage failure: 'stop' halts pipeline, 'rollback' reverts, 'continue' proceeds",
    )

    @model_validator(mode="after")
    def validate_provisioner_selection(self) -> "DeploymentStageModel":
        """Validate provisioner/topology selection logic.

        Rules:
        1. Cannot specify both 'provisioner' and 'topology' (mutually exclusive)
        2. If type='custom', should have scripts defined (warning-level, not error)
        3. Actual provisioner existence validation happens in deployment controller
           (requires workspace context not available here)
        """
        # Rule 1: Mutually exclusive provisioner and topology
        if self.provisioner and self.topology:
            raise ValueError(
                f"Stage '{self.name}': Cannot specify both 'provisioner' and 'topology' - they are mutually exclusive. "
                f"Use 'topology' to scope to infrastructure, or 'provisioner' to specify IaC tool directly."
            )

        # Rule 2: Custom type should have scripts (soft validation)
        # This is enforced at deployment controller level with proper warning

        return self


class DeploymentSpecModel(BaseModel):
    """Model for deployment specification (properties, workspace, stages)."""

    configurations: Optional[List[DeploymentConfigurationModel]] = Field(
        None, description="Optional configuration list"
    )
    lifecycle: Optional[CommonLifecycleModel] = Field(
        None, description="Deployment lifecycle phases"
    )
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
    workspace: DeploymentWorkspaceModel = Field(
        description="Name of the associated workspace for this deployment"
    )
    environments: Annotated[
        List[str],
        Field(
            min_length=1,
            description="List of environment file paths for this deployment (later files override earlier ones)",
        ),
    ]
    approvals: Optional[DeploymentApprovalModel] = Field(
        None, description="Approval configuration for this deployment"
    )
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
                duplicates = [
                    name for name in stage_names if stage_names.count(name) > 1
                ]
                raise ValueError(f"Duplicate stage names found: {set(duplicates)}")

        if self.configurations:
            config_names = [config.name for config in self.configurations]
            if len(config_names) != len(set(config_names)):
                duplicates = [
                    name for name in config_names if config_names.count(name) > 1
                ]
                raise ValueError(
                    f"Duplicate configuration names found: {set(duplicates)}"
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
                        errors.append(
                            f"Stage '{stage.name}' depends on undefined stage '{dep}'"
                        )
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
                    raise ValueError(
                        f"Circular dependency detected in stages involving '{stage.name}'"
                    )

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
    tags: Optional[List[Any]] = Field(
        None, description="Optional tags (list of values for categorization)"
    )


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
    meta: DeploymentMetaModel = Field(
        description="Deployment metadata (name, annotations, labels, tags)"
    )
    spec: DeploymentSpecModel = Field(
        description="Deployment specification (properties, workspace, environment)"
    )
