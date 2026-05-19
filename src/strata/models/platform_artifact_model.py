#!/usr/bin/env python3
"""Pydantic models for platform build output structure.

Defines the complete schema for the generated platform configuration
(e.g. workspace.json) consumed by IaC tools. Uses a hybrid approach:
reuses input models where possible, with separate flattened models for
output-specific structures.
"""

from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, Field, RootModel, StringConstraints

# Input models (consumed by from_*_model classmethods)
from strata.models.auth_models import AuthenticationModel

# Core shared types
from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    ScriptsModel,
    SourceModel,
)
from strata.models.deployment_model import (
    DeploymentApprovalModel,
    DeploymentMetaModel,
    DeploymentSpecModel,
    DeploymentStageModel,
)
from strata.models.firewall_model import (
    FirewallDefaultsModel,
    FirewallRuleModel,
)
from strata.models.firewall_model import (
    FirewallModel as InputFirewallModel,
)
from strata.models.module_model import (
    ModuleModel,
    ModulePropertiesModel,
    ModuleReferenceModel,
)
from strata.models.namespace_model import (
    NamespaceModel as InputNamespaceModel,
)
from strata.models.provider_model import (
    ProviderModel,
    ProviderPropertiesModel,
    ProviderReferencesModel,
)
from strata.models.resource_model import (
    ResourceDependencyModel,
    ResourceModel,
    ResourcePropertiesModel,
    ResourceReferencesModel,
    ResourceStorageModel,
)

# Store models (variables, secrets, features)
from strata.models.store_models import (
    FeatureStoreModel,
    SecretStoreModel,
    VariableStoreModel,
)
from strata.models.workspace_model import (
    WorkspaceIacModel,
    WorkspaceModel,
    WorkspaceTopologyModel,
)

# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------


class PlatformFirewallModel(BaseModel):
    """Flattened firewall model for platform output (meta + spec combined)."""

    name: PlatformName = Field(description="Unique name for the firewall resource.")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering).",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags for the firewall resource.")
    reset: Optional[bool] = Field(
        None,
        description="Whether to reset/clear all existing firewall rules before applying these rules",
    )
    defaults: Optional[List[FirewallDefaultsModel]] = Field(
        None,
        description="Default policies for inbound/outbound traffic (unique directions)",
    )
    deny: Optional[List[FirewallRuleModel]] = Field(
        None, description="Explicit deny rules (processed before allow rules)"
    )
    allow: Optional[List[FirewallRuleModel]] = Field(
        None, description="Explicit allow rules (processed after deny rules)"
    )

    @classmethod
    def from_firewall_model(cls, model: InputFirewallModel) -> "PlatformFirewallModel":
        """Create from input FirewallModel (merges meta + spec)."""
        return cls(
            name=model.meta.name,
            annotations=model.meta.annotations,
            labels=model.meta.labels,
            tags=model.meta.tags,
            reset=model.spec.reset,
            defaults=model.spec.defaults,
            deny=model.spec.deny,
            allow=model.spec.allow,
        )


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class PlatformModuleModel(BaseModel):
    """Flattened module model for platform output (meta + spec combined)."""

    name: PlatformName = Field(description="Unique module name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags")
    source: SourceModel = Field(description="Module source location")
    lifecycle: Optional[CommonLifecycleModel] = Field(None, description="Module-specific lifecycle hooks")
    properties: Optional[ModulePropertiesModel] = Field(
        None, description="Module-specific properties and configurations"
    )
    references: Optional[ModuleReferenceModel] = Field(
        None, description="Module references for variable and secret injection"
    )
    configuration: Optional[Dict[str, Any]] = Field(None, description="Module-specific configuration data")

    @classmethod
    def from_module_model(cls, model: ModuleModel) -> "PlatformModuleModel":
        """Create from input ModuleModel (merges meta + spec)."""
        return cls(
            name=model.meta.name,
            annotations=model.meta.annotations,
            labels=model.meta.labels,
            tags=model.meta.tags,
            source=model.spec.source,
            lifecycle=model.spec.lifecycle,
            properties=model.spec.properties,
            references=model.spec.references,
            configuration=model.spec.configuration,
        )


# ---------------------------------------------------------------------------
# Namespace
# ---------------------------------------------------------------------------


class PlatformNamespaceModuleModel(BaseModel):
    """Reference to a module within a namespace."""

    module: PlatformName = Field(description="Unique module name")


class PlatformNamespaceModel(BaseModel):
    """Flattened namespace model for platform output (meta + spec combined)."""

    name: PlatformName = Field(description="Unique namespace name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags")
    lifecycle: Optional[CommonLifecycleModel] = Field(None, description="Namespace lifecycle phases")
    modules: Optional[List[PlatformNamespaceModuleModel]] = Field(
        None,
        description="List of modules that belong to this namespace. Each module must match the name of a module defined in the modules section.",
    )

    @classmethod
    def from_namespace_model(cls, model: InputNamespaceModel) -> "PlatformNamespaceModel":
        """Create from input NamespaceModel (merges meta + spec)."""
        return cls(
            name=model.meta.name,
            annotations=model.meta.annotations,
            labels=model.meta.labels,
            tags=model.meta.tags,
            lifecycle=model.spec.lifecycle,
            modules=(
                [PlatformNamespaceModuleModel(module=m.name) for m in model.spec.modules]
                if model.spec.modules
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


class PlatformResourceModel(BaseModel):
    """Flattened resource model for platform output (meta + spec combined).

    This is an output-specific model that may differ from input resource models
    used for parsing and validation of platform definition files.
    """

    name: PlatformName = Field(description="Unique resource name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")
    lifecycle: Optional[CommonLifecycleModel] = Field(
        None,
        description="IaC workflow lifecycle phases",
    )
    references: Optional[ResourceReferencesModel] = Field(None, description="Variable, and secret references")
    properties: ResourcePropertiesModel = Field(
        description="Configuration properties (provider, resources, disks, volumes)"
    )
    dependencies: Optional[List[ResourceDependencyModel]] = Field(None, description="List of resource dependencies")
    storage: Optional[ResourceStorageModel] = Field(None, description="Virtual machine specific configuration")
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional configuration block for resource-specific settings",
    )
    custom: Optional[Dict[str, Any]] = Field(None, description="Custom user-defined data for scripts or extensions")
    default_tags: Optional[Dict[str, str]] = Field(
        None,
        description="Default tags to apply to all resources created by this provider (ignored if provider doesn't support tagging)",
    )
    firewalls: Optional[List[str]] = Field(
        None,
        description="List of original firewall references (from workspace resource definition)",
    )
    firewall: Optional[str] = Field(
        None,
        description="Reference to merged firewall name (if resource has multiple firewalls merged)",
    )
    role: Optional[str] = Field(
        None,
        description="Role of this resource in the workspace topology (e.g., manager, worker)",
    )
    count: int = Field(
        default=1,
        description="Number of instances of this resource to provision",
    )

    @classmethod
    def from_resource_model(
        cls,
        model: ResourceModel,
        firewalls: Optional[List[str]] = None,
        firewall: Optional[str] = None,
        role: Optional[str] = None,
        count: int = 1,
    ) -> "PlatformResourceModel":
        """Create from input ResourceModel (merges meta + spec).

        Args:
            model: Resource model to convert
            firewalls: Optional list of original firewall names from workspace definition
            firewall: Optional merged firewall name (when multiple firewalls are combined)

        Returns:
            PlatformResourceModel with all fields populated

        Note:
            Category/subcategory are populated at resource load time by
            ResourceService._populate_category_from_configuration(), so no
            resolution is needed here.
        """
        return cls(
            name=model.meta.name,
            annotations=model.meta.annotations,
            labels=model.meta.labels,
            tags=model.meta.tags,
            lifecycle=model.spec.lifecycle,
            references=model.spec.references,
            properties=model.spec.properties,
            dependencies=model.spec.dependencies,
            storage=model.spec.storage,
            configuration=model.spec.configuration,
            custom=model.spec.custom,
            default_tags=None,
            firewalls=firewalls,
            firewall=firewall,
            role=role,
            count=count,
        )


# ---------------------------------------------------------------------------
# Stereotype (grouping of resources by type)
# ---------------------------------------------------------------------------


class PlatformStereotypeResourceModel(BaseModel):
    """Reference to a resource within a stereotype group."""

    resource: Annotated[
        str,
        StringConstraints(min_length=1, strip_whitespace=True),
        Field(
            description="Name of the resource that belongs to this stereotype. Must match the name of a resource defined in the resources section.",
        ),
    ]


class PlatformStereotypeModel(BaseModel):
    """Model representing a list of resources grouped by their type."""

    type: str = Field(description="Type of the stereotype (e.g. virtualmachine, database, kubernetes_cluster)")
    category: Optional[str] = Field(
        None,
        description="Optional category for the stereotype (e.g. compute, storage, network)",
    )
    subcategory: Optional[str] = Field(
        None,
        description="Optional subcategory for the stereotype (e.g. general_purpose, memory_optimized, gpu)",
    )
    description: Optional[str] = Field(
        None,
        description="Optional description of the stereotype for documentation purposes",
    )
    resources: Optional[List[PlatformStereotypeResourceModel]] = Field(
        None,
        description="List of resources that belong to this stereotype. Each resource must match the name of a resource defined in the resources section.",
    )


# ---------------------------------------------------------------------------
# Topology / Provisioner (pass-through wrappers)
# ---------------------------------------------------------------------------


class PlatformComponentModel(BaseModel):
    """Enriched topology component for platform output — adds role and count."""

    resource: Annotated[
        str,
        StringConstraints(min_length=1, strip_whitespace=True),
        Field(description="Resource name reference"),
    ]
    role: Optional[str] = Field(None, description="Role of this resource within the topology")
    count: int = Field(1, description="Number of instances of this resource")


class PlatformTopologyModel(WorkspaceTopologyModel):
    """Platform topology — enriches components with role and count."""

    components: List[PlatformComponentModel] = Field(  # type: ignore[assignment]
        default_factory=list, description="Topology components with role and count"
    )


class PlatformProvisionerModel(WorkspaceIacModel):
    """Platform provisioner — delegates to WorkspaceIacModel."""

    pass


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class PlatformProviderModel(BaseModel):
    """Flattened provider model for platform output (meta + spec combined)."""

    name: PlatformName = Field(description="Unique provider name")
    description: Optional[str] = Field(
        None,
        description="Optional description of the provider for documentation purposes",
    )
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")
    lifecycle: Optional[CommonLifecycleModel] = Field(
        None,
        description="IaC workflow lifecycle phases (setup, validate, plan, apply, output, destroy)",
    )
    properties: ProviderPropertiesModel = Field(
        description="Provider configuration (cloud provider, IaC tool, datacenter location)"
    )
    authentication: Optional[AuthenticationModel] = Field(
        None, description="Authentication configuration for cloud provider access"
    )
    references: Optional[ProviderReferencesModel] = Field(
        None,
        description="Variable and secret mappings for runtime configuration injection",
    )

    @classmethod
    def from_provider_model(cls, model: ProviderModel) -> "PlatformProviderModel":
        """Create from input ProviderModel (merges meta + spec)."""
        return cls(
            name=model.meta.name,
            description=(model.meta.annotations.get("description") if model.meta.annotations else None),
            annotations=model.meta.annotations,
            labels=model.meta.labels,
            tags=model.meta.tags,
            lifecycle=model.spec.lifecycle,
            properties=model.spec.properties,
            authentication=model.spec.authentication,
            references=model.spec.references,
        )


# ---------------------------------------------------------------------------
# Workspace (core identity section)
# ---------------------------------------------------------------------------


class PlatformWorkspaceModel(BaseModel):
    """Core workspace identity for the platform output (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique workspace name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")

    @classmethod
    def from_workspace_model(cls, model: WorkspaceModel) -> "PlatformWorkspaceModel":
        """Create from input WorkspaceModel meta."""
        return cls(
            name=model.meta.name,
            annotations=model.meta.annotations,
            labels=model.meta.labels,
            tags=model.meta.tags,
        )


# ---------------------------------------------------------------------------
# Lifecycle (output level)
# ---------------------------------------------------------------------------


class PlatformLifecyclePhaseModel(ScriptsModel):
    """A single lifecycle phase definition with script paths."""

    pass


class PlatformLifecycleModel(RootModel):
    """Lifecycle phases map: phase-name → PlatformLifecyclePhaseModel."""

    root: Dict[str, PlatformLifecyclePhaseModel] = {}


# ---------------------------------------------------------------------------
# Spec (the body of PlatformModel)
# ---------------------------------------------------------------------------


class PlatformSpecModel(BaseModel):
    """Complete specification of the platform output artifact.

    Aggregates all workspace sections (providers, provisioners, topologies,
    stereotypes, resources, namespaces, modules, firewalls) together with
    deployment-level settings (lifecycle, stages, approvals, variables, secrets,
    features, properties, custom).
    """

    lifecycle: Optional[PlatformLifecycleModel] = Field(
        None,
        description="Lifecycle phases for deployment commands (deploy). Maps phase names to phase configurations. Phase names follow pattern: {command}_{action}. Examples: deploy_provision, deploy_configure, deploy_health",
    )
    deployment: Optional[Dict[str, str]] = Field(
        None,
        description="Deployment layer values (keys match configuration.spec.layering[].name). Defines the artifact path location for this deployment.",
    )
    artifact_path: Optional[str] = Field(
        None,
        description="Computed artifact path from deployment layer values (e.g., 'eu/contoso/default/production'). Constructed by joining layer values in order.",
    )
    stages: Optional[List[DeploymentStageModel]] = Field(
        None,
        description="Deployment stages defining the execution plan and provisioning steps",
    )
    approvals: Optional[DeploymentApprovalModel] = Field(
        None,
        description="Deployment approval configuration (auto or manual approval gates)",
    )
    properties: Optional[Dict[str, Any]] = Field(None, description="Deployment-specific properties and configurations")
    custom: Optional[Dict[str, Any]] = Field(
        None, description="Deployment-specific custom properties and configurations"
    )
    workspace: PlatformWorkspaceModel = Field(description="Core workspace configuration for the deployment")
    providers: Optional[List[PlatformProviderModel]] = Field(
        None, description="List of cloud providers and their configurations"
    )
    provisioners: Optional[List[PlatformProvisionerModel]] = Field(
        None, description="List of provisioners and their configurations"
    )
    topologies: Optional[List[PlatformTopologyModel]] = Field(
        None, description="List of topologies and their configurations"
    )
    stereotypes: Optional[List[PlatformStereotypeModel]] = Field(
        None, description="List of stereotypes and their configurations"
    )
    resources: Optional[List[PlatformResourceModel]] = Field(
        None, description="List of resources and their configurations"
    )
    features: Optional[List[FeatureStoreModel]] = Field(None, description="Deployment-specific features and flags")
    variables: Optional[List[VariableStoreModel]] = Field(None, description="List of deployment variables")
    secrets: Optional[List[SecretStoreModel]] = Field(None, description="List of deployment secrets")
    namespaces: Optional[List[PlatformNamespaceModel]] = Field(
        None, description="List of namespaces and their configurations"
    )
    modules: Optional[List[PlatformModuleModel]] = Field(None, description="List of modules and their configurations")
    firewalls: Optional[List[PlatformFirewallModel]] = Field(
        None, description="List of firewalls and their configurations"
    )

    @classmethod
    def from_deployment_model(
        cls, model: DeploymentSpecModel, artifact_path: Optional[str] = None
    ) -> "PlatformSpecModel":
        """Seed spec from a DeploymentSpecModel (deployment-level fields only).

        The workspace, providers, provisioners, resources, etc. are populated
        separately by the builder after resolving all referenced files.

        Args:
            model: DeploymentSpecModel to convert
            artifact_path: Optional computed artifact path from deployment layer values

        Returns:
            PlatformSpecModel pre-populated with deployment fields
        """
        return cls(
            lifecycle=None,
            deployment=None,
            artifact_path=artifact_path,
            stages=model.stages,
            approvals=model.approvals,
            properties=model.properties,
            custom=model.custom,
            workspace=PlatformWorkspaceModel(
                name="__pending__",
                annotations=None,
                labels=None,
                tags=None,
            ),
            providers=None,
            provisioners=None,
            topologies=None,
            stereotypes=None,
            resources=None,
            features=None,
            variables=None,
            secrets=None,
            namespaces=None,
            modules=None,
            firewalls=None,
        )


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------


class PlatformMetaModel(BaseModel):
    """Metadata about the platform output artifact (name, annotations, labels, tags)."""

    name: PlatformName = Field(
        ...,
        description="Unique name of the platform (from deployment)",
    )
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")

    @classmethod
    def from_deployment_meta(
        cls, meta: DeploymentMetaModel, environment_service: Optional[Any] = None
    ) -> "PlatformMetaModel":
        """Create from deployment metadata with optional environment label injection.

        If the deployment labels do not include an 'environment' key and an
        environment_service is provided, the environment value is sourced from
        the environment model's labels.

        Args:
            meta: Deployment metadata
            environment_service: Optional environment service for environment label fallback

        Returns:
            PlatformMetaModel with labels enhanced by environment value if available
        """
        labels: Optional[Dict[str, Any]] = meta.labels

        # If deployment doesn't carry an environment label, pull it from the
        # environment service model when available.
        if environment_service and isinstance(labels, dict) and "environment" not in labels:
            environment_model = getattr(environment_service, "model", None)
            if environment_model and hasattr(environment_model, "meta"):
                env_labels = getattr(environment_model.meta, "labels", None)
                if isinstance(env_labels, dict) and "environment" in env_labels:
                    labels = {**labels, "environment": env_labels["environment"]}

        return cls(
            name=meta.name,
            annotations=meta.annotations,
            labels=labels,
            tags=meta.tags,
        )


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------


class PlatformArtifactModel(BaseModel):
    """Root model for the generated platform configuration artifact.

    This is the complete output structure written as workspace.json (or similar)
    and consumed by IaC tools and builders.  It is assembled by the builder layer
    from the individual service-loaded input models (workspace, providers, resources,
    modules, namespaces, firewalls, …) plus the deployment overlay.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for platform configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.PLATFORM_MODEL,
        frozen=True,
        description="Platform model kind (always 'platform_model')",
    )
    meta: PlatformMetaModel = Field(
        description="Metadata about the platform artifact (name, annotations, labels, tags)"
    )
    spec: PlatformSpecModel = Field(
        description="Full specification including workspace, providers, provisioners, resources, namespaces, modules, firewalls, deployment settings."
    )
