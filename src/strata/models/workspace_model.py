#!/usr/bin/env python3
"""Pydantic models for workspace configuration validation."""

from enum import Enum
from typing import Annotated, Any, Dict, List, Optional

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
    ProvisionerType,
    SourceModel,
    validate_slot_type,
)


# Enumeration of supported workspace volume types.
class WorkspaceVolumeType(str, Enum):
    """Supported workspace volume types."""

    local = "local"
    replicated = "replicated"
    distributed = "distributed"


class WorkspaceNamespaceModel(BaseModel):
    """Model for a workspace namespace."""

    name: PlatformName = Field(description="Unique namespace name")
    file: str = Field(description="File reference for the namespace configuration")


class WorkspaceFirewallModel(BaseModel):
    """Model for a workspace firewall."""

    name: PlatformName = Field(description="Unique firewall name")
    file: str = Field(description="File reference for the firewall configuration")


class WorkspaceVolumeModel(BaseModel):
    """Model for a workspace volume."""

    name: PlatformName = Field(description="Unique volume name within the topology")
    type: WorkspaceVolumeType = Field(default=WorkspaceVolumeType.local, description="Type of the volume")


class WorkspaceModuleReferenceModel(BaseModel):
    """Module reference for a resource - links code/app to infrastructure."""

    name: PlatformName = Field(description="Unique module name within this resource")
    file: str = Field(description="File reference to the module configuration (module YAML file)")
    slot_type: Optional[str] = Field(
        "main",
        description="Deployment slot type: 'main' (primary/production), 'staging', 'canary', 'sidecar', 'init'. Defaults to 'main'. Custom values allowed but may generate warnings.",
    )
    enabled: bool = Field(default=True, description="Whether this module is enabled/deployed")
    configuration: Optional[Dict[str, Any]] = Field(
        None, description="Module-specific configuration overrides for this workspace"
    )

    @field_validator("slot_type")
    @classmethod
    def validate_slot_type_value(cls, v: Optional[str]) -> Optional[str]:
        """Validate slot_type using common validator."""
        return validate_slot_type(v)


class WorkspaceComponentModel(BaseModel):
    """Component model - simple resource name reference."""

    resource: Annotated[
        str,
        StringConstraints(min_length=1, strip_whitespace=True),
        Field(description="Resource name reference (must match a resource defined in spec.resources)"),
    ]


class WorkspaceTopologyModel(BaseModel):
    name: PlatformName = Field(..., description="Unique topology name")
    provider: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        ..., description="Provider name used for this topology"
    )
    provisioner: ProvisionerType = Field(..., description="IaC tool used for provisioning")
    type: PlatformName = Field(..., description="Topology type (e.g., dockerswarm, kubernetes, azure-native)")
    components: Annotated[
        List[WorkspaceComponentModel],
        Field(min_length=1, description="Topology components"),
    ]
    volumes: Optional[List[WorkspaceVolumeModel]] = Field(None, description="Topology volumes")

    @model_validator(mode="after")
    def validate_unique_names_within_topology(self) -> "WorkspaceTopologyModel":
        """Validate unique component and volume names within this topology."""
        errors = []

        # Validate unique resource references within topology
        if self.components:
            resource_refs = [comp.resource for comp in self.components]
            if len(resource_refs) != len(set(resource_refs)):
                duplicates = [ref for ref in resource_refs if resource_refs.count(ref) > 1]
                errors.append(f"Duplicate resource references in topology '{self.name}': {set(duplicates)}")

        # Validate unique volume names within topology
        if self.volumes:
            volume_names = [vol.name for vol in self.volumes]
            if len(volume_names) != len(set(volume_names)):
                duplicates = [name for name in volume_names if volume_names.count(name) > 1]
                errors.append(f"Duplicate volume names in topology '{self.name}': {set(duplicates)}")

        if errors:
            raise ValueError("; ".join(errors))

        return self


class WorkspaceResourceModel(BaseModel):
    """Model for workspace resource definition (gluing layer)."""

    name: PlatformName = Field(description="Unique resource name")
    file: str = Field(description="Path to the resource configuration file")
    description: Optional[str] = Field(
        None,
        description="Optional description of the resource for documentation purposes",
    )

    # Conditional inclusion
    enabled: bool = Field(
        default=True,
        description="Whether this resource is enabled/deployed in this workspace",
    )
    condition: Optional[str] = Field(
        None,
        description="Conditional expression for resource inclusion (e.g., '${environment} == production')",
    )

    # Resource metadata
    role: Optional[PlatformName] = Field(None, description="Role of the resource (e.g., networking, database, api)")
    count: Annotated[
        int,
        Field(
            ge=1,
            le=100,
            description="Number of resource instances (must be greater than 0)",
        ),
    ] = 1

    # Dependencies and references
    depends_on: Optional[List[str]] = Field(
        None,
        description="List of resource names this resource depends on (workspace-specific gluing)",
    )
    references: Optional[Dict[str, str]] = Field(
        None,
        description="Cross-resource value references (e.g., {'storage_connection': 'contoso_storage.connection_string'})",
    )
    firewalls: Optional[List[str]] = Field(
        None,
        description="References to firewall/NSG resource names for network security",
    )

    # Configuration overrides
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description="Workspace-specific configuration overrides (merged with resource file configuration)",
    )
    custom: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional additional properties for the resource (key-value pairs)",
    )

    # Module references (code/apps that run on this resource)
    modules: Optional[List[WorkspaceModuleReferenceModel]] = Field(
        None,
        description="Optional module references (code/apps) - links apps to infrastructure (e.g., web app code on Azure Web App, function code on Function App, containers on AKS)",
    )

    # Metadata
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")


class WorkspaceIacBackendModel(BaseModel):
    """Model for IaC backend configuration (state storage)."""

    type: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Backend type (e.g., 'terraform_cloud', 's3', 'azurerm', 'gcs', 'local', 'remote')"
    )
    configuration: Dict[str, Any] = Field(
        description="Backend-specific configuration (supports either a constant value or references like ${var:tf_org}, ${secret:tf_token}, ${feat:enable_encryption})"
    )


class WorkspaceIacModel(BaseModel):
    name: PlatformName
    description: Optional[str] = Field(
        None,
        description="Optional description of the provisioner for documentation purposes",
    )
    provisioner: ProvisionerType = Field(..., description="IaC tool used for provisioning")
    source: SourceModel = Field(description="IaC deployment configuration (file path, variables, secrets)")
    backend: Optional[WorkspaceIacBackendModel] = Field(
        None,
        description="Backend configuration for state storage (e.g., Terraform Cloud, S3, Azure Storage)",
    )


class WorkspaceProviderModel(BaseModel):
    name: PlatformName = Field(description="Unique provider name")
    file: str = Field(description="Path to the provider configuration file")
    description: Optional[str] = Field(
        None,
        description="Optional description of the provider for documentation purposes",
    )


class WorkspaceSpecModel(BaseModel):
    """Workspace specification model."""

    lifecycle: Optional[CommonLifecycleModel] = Field(
        None,
        description="Workspace workflow lifecycle phases",
    )
    properties: Optional[Dict[str, Any]] = Field(None, description="Workspace properties")
    custom: Optional[Dict[str, Any]] = Field(None, description="Optional additional properties (key-value pairs)")
    providers: Annotated[
        List[WorkspaceProviderModel],
        Field(min_length=1, description="Provider configurations"),
    ]
    provisioners: Annotated[
        List[WorkspaceIacModel],
        Field(min_length=1, description="IaC provisioner configurations"),
    ]
    topology: Annotated[
        List[WorkspaceTopologyModel],
        Field(min_length=1, description="Workspace topology configurations"),
    ]
    resources: Optional[List[WorkspaceResourceModel]] = Field(
        None,
        description="Workspace resources with dependencies (gluing layer for topology)",
    )
    namespaces: Optional[List[WorkspaceNamespaceModel]] = Field(None, description="Workspace namespaces")
    firewalls: Optional[List[WorkspaceFirewallModel]] = Field(None, description="Workspace firewalls")

    # Validate unique provider names
    @model_validator(mode="after")
    def validate_unique_providers(self) -> "WorkspaceSpecModel":
        """Validate that all provider names are unique."""
        if self.providers:
            provider_names = [provider.name for provider in self.providers]
            duplicates = [name for name in provider_names if provider_names.count(name) > 1]
            if duplicates:
                raise ValueError(f"Duplicate provider names found: {', '.join(set(duplicates))}")
        return self

    # Validate unique provisioner names
    @model_validator(mode="after")
    def validate_unique_provisioners(self) -> "WorkspaceSpecModel":
        """Validate that all provisioner names are unique."""
        if self.provisioners:
            provisioner_names = [prov.name for prov in self.provisioners]
            duplicates = [name for name in provisioner_names if provisioner_names.count(name) > 1]
            if duplicates:
                raise ValueError(f"Duplicate provisioner names found: {', '.join(set(duplicates))}")
        return self

    # Validate unique topology names
    @model_validator(mode="after")
    def validate_unique_topologies(self) -> "WorkspaceSpecModel":
        """Validate that all topology names are unique."""
        # Validate if topology names are unique
        if self.topology:
            topology_names = [topo.name for topo in self.topology]
            duplicates = [name for name in topology_names if topology_names.count(name) > 1]
            if duplicates:
                raise ValueError(f"Duplicate topology names found: {', '.join(set(duplicates))}")
        return self

    # Validate unique namespace names
    @model_validator(mode="after")
    def validate_unique_namespaces(self) -> "WorkspaceSpecModel":
        """Validate that all namespace names are unique."""
        if self.namespaces:
            namespace_names = [ns.name for ns in self.namespaces]
            duplicates = [name for name in namespace_names if namespace_names.count(name) > 1]
            if duplicates:
                raise ValueError(f"Duplicate namespace names found: {', '.join(set(duplicates))}")
        return self

    # Validate unique firewall names
    @model_validator(mode="after")
    def validate_unique_firewalls(self) -> "WorkspaceSpecModel":
        """Validate that all firewall names are unique."""
        if self.firewalls:
            firewall_names = [fw.name for fw in self.firewalls]
            duplicates = [name for name in firewall_names if firewall_names.count(name) > 1]
            if duplicates:
                raise ValueError(f"Duplicate firewall names found: {', '.join(set(duplicates))}")
        return self

    # Validate unique resource names
    @model_validator(mode="after")
    def validate_unique_resources(self) -> "WorkspaceSpecModel":
        """Validate that all resource names are unique."""
        if self.resources:
            resource_names = [res.name for res in self.resources]
            duplicates = [name for name in resource_names if resource_names.count(name) > 1]
            if duplicates:
                raise ValueError(f"Duplicate resource names found: {', '.join(set(duplicates))}")

            # Validate unique module names within each resource
            errors = []
            for resource in self.resources:
                if resource.modules:
                    module_names = [mod.name for mod in resource.modules]
                    if len(module_names) != len(set(module_names)):
                        duplicates = [name for name in module_names if module_names.count(name) > 1]
                        errors.append(f"Duplicate module names in resource '{resource.name}': {set(duplicates)}")

                    # Validate slot types: at least one 'main' if multiple modules
                    if len(resource.modules) > 1:
                        enabled_modules = [mod for mod in resource.modules if mod.enabled]
                        if enabled_modules:
                            main_slots = [mod for mod in enabled_modules if mod.slot_type == "main"]
                            if not main_slots:
                                errors.append(
                                    f"Resource '{resource.name}' has multiple enabled modules but no 'main' slot defined"
                                )
                            elif len(main_slots) > 1:
                                errors.append(
                                    f"Resource '{resource.name}' has multiple modules marked as 'main' slot: {[m.name for m in main_slots]}"
                                )

            if errors:
                raise ValueError("; ".join(errors))

        return self

    # Validate firewall references
    @model_validator(mode="after")
    def validate_firewall_references(self) -> "WorkspaceSpecModel":
        """Validate that all firewall references in resources exist in firewalls section."""
        if not self.resources:
            return self

        # Get all defined firewall names
        firewall_names = set()
        if self.firewalls:
            firewall_names = {fw.name for fw in self.firewalls}

        # Check all resource firewall references
        invalid_refs = []
        for resource in self.resources:
            if resource.firewalls:
                for firewall in resource.firewalls:
                    if firewall not in firewall_names:
                        invalid_refs.append(f"Resource '{resource.name}' references undefined firewall '{firewall}'")

        if invalid_refs:
            raise ValueError(f"Invalid firewall references found: {'; '.join(invalid_refs)}")

        return self

    # Validate topology component resource references
    @model_validator(mode="after")
    def validate_topology_resource_references(self) -> "WorkspaceSpecModel":
        """Validate that all resource references in topology exist in resources section."""
        if not self.topology:
            return self

        # Get all defined resource names
        resource_names = set()
        if self.resources:
            resource_names = {res.name for res in self.resources}

        # Check all topology component resource references
        invalid_refs = []
        for topology in self.topology:
            if not topology.components:
                continue

            for component in topology.components:
                # Check resource reference exists
                if component.resource not in resource_names:
                    invalid_refs.append(
                        f"Topology '{topology.name}' component references undefined resource '{component.resource}'"
                    )

        if invalid_refs:
            raise ValueError(f"Invalid resource references in topology: {'; '.join(invalid_refs)}")

        return self


class WorkspaceMetaModel(BaseModel):
    """Model for workspace metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique workspace name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")


class WorkspaceModel(BaseModel):
    """Root model for a workspace configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for workspace configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.WORKSPACE,
        frozen=True,
        description="Platform kind (always 'Workspace')",
    )
    meta: WorkspaceMetaModel = Field(description="Workspace metadata (name, annotations, labels, tags)")
    spec: WorkspaceSpecModel = Field(
        description="Workspace specification (properties, topology, providers, IaC, lifecycle)"
    )
