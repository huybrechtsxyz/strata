#!/usr/bin/env python3
"""Pydantic models for workspace configuration validation."""

from enum import Enum
from typing import Annotated, Any, Dict, List, Optional

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


class WorkspaceNamespaceModel(PlatformBaseModel):
    """Model for a workspace namespace."""

    name: PlatformName = Field(description="Unique namespace name")
    file: str = Field(description="File reference for the namespace configuration")


class WorkspaceFirewallModel(PlatformBaseModel):
    """Model for a workspace firewall."""

    name: PlatformName = Field(description="Unique firewall name")
    file: str = Field(description="File reference for the firewall configuration")


class WorkspaceDnsModel(PlatformBaseModel):
    """Model for a workspace DNS zone configuration reference."""

    name: PlatformName = Field(description="Unique DNS zone configuration name")
    file: str = Field(description="File reference for the DNS zone configuration")


class WorkspaceVolumeModel(PlatformBaseModel):
    """Model for a workspace volume."""

    name: PlatformName = Field(description="Unique volume name within the topology")
    type: WorkspaceVolumeType = Field(default=WorkspaceVolumeType.local, description="Type of the volume")


class WorkspaceModuleReferenceModel(PlatformBaseModel):
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


class WorkspaceComponentModel(PlatformBaseModel):
    """Component model - simple resource name reference."""

    resource: Annotated[
        str,
        StringConstraints(min_length=1, strip_whitespace=True),
        Field(description="Resource name reference (must match a resource defined in spec.resources)"),
    ]


class WorkspaceNamespaceReferenceModel(PlatformBaseModel):
    """Namespace reference within a topology - links a namespace to this topology."""

    namespace: Annotated[
        str,
        StringConstraints(min_length=1, strip_whitespace=True),
        Field(description="Namespace name reference (must match a namespace defined in spec.namespaces)"),
    ]


class WorkspaceTopologyModel(PlatformBaseModel):
    name: PlatformName = Field(..., description="Unique topology name")
    provider: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        ..., description="Provider name used for this topology"
    )
    provisioner: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        ..., description="IaC provisioner name reference (must match a provisioner defined in spec.provisioners)"
    )
    type: PlatformName = Field(..., description="Topology type (e.g., dockerswarm, kubernetes, azure-native)")
    components: Annotated[
        List[WorkspaceComponentModel],
        Field(min_length=1, description="Topology components"),
    ]
    namespaces: Optional[List[WorkspaceNamespaceReferenceModel]] = Field(
        None, description="Namespace references deployed on this topology"
    )
    volumes: Optional[List[WorkspaceVolumeModel]] = Field(None, description="Topology volumes")

    @model_validator(mode="after")
    def validate_unique_names_within_topology(self) -> "WorkspaceTopologyModel":
        """Validate unique component, namespace, and volume names within this topology."""
        errors = []

        # Validate unique resource references within topology
        if self.components:
            resource_refs = [comp.resource for comp in self.components]
            if len(resource_refs) != len(set(resource_refs)):
                duplicates = [ref for ref in resource_refs if resource_refs.count(ref) > 1]
                errors.append(f"Duplicate resource references in topology '{self.name}': {set(duplicates)}")

        # Validate unique namespace references within topology
        if self.namespaces:
            namespace_refs = [ns.namespace for ns in self.namespaces]
            if len(namespace_refs) != len(set(namespace_refs)):
                duplicates = [ref for ref in namespace_refs if namespace_refs.count(ref) > 1]
                errors.append(f"Duplicate namespace references in topology '{self.name}': {set(duplicates)}")

        # Validate unique volume names within topology
        if self.volumes:
            volume_names = [vol.name for vol in self.volumes]
            if len(volume_names) != len(set(volume_names)):
                duplicates = [name for name in volume_names if volume_names.count(name) > 1]
                errors.append(f"Duplicate volume names in topology '{self.name}': {set(duplicates)}")

        if errors:
            raise ValueError("; ".join(errors))

        return self


class WorkspaceResourceModel(PlatformBaseModel):
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

    @field_validator("depends_on", mode="before")
    @classmethod
    def coerce_depends_on(cls, v):
        if isinstance(v, str):
            return [v]
        return v

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


class WorkspaceIacBackendModel(PlatformBaseModel):
    """Model for IaC backend configuration (state storage)."""

    type: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Backend type (e.g., 'terraform_cloud', 's3', 'azurerm', 'gcs', 'local', 'remote')"
    )
    configuration: Dict[str, Any] = Field(
        description="Backend-specific configuration (supports either a constant value or references like ${var:tf_org}, ${secret:tf_token}, ${feat:enable_encryption})"
    )


class WorkspaceIacAnsiblePropertiesModel(PlatformBaseModel):
    """Typed properties for an Ansible provisioner entry."""

    playbook: Optional[str] = Field(
        None,
        description="Playbook file to run, relative to the playbook directory (default: site.yml)",
    )
    inventory: Optional[str] = Field(
        None,
        description="Static inventory file path, relative to the playbook directory",
    )
    ssh_private_key_secret: Optional[str] = Field(
        None,
        description="Name of the secret holding the SSH private key (default: ssh_private_key)",
    )
    extra_vars: Optional[Dict[str, str]] = Field(
        None,
        description="Extra variables passed to ansible-playbook via --extra-vars",
    )


class WorkspaceIacModel(PlatformBaseModel):
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
    properties: Optional[WorkspaceIacAnsiblePropertiesModel] = Field(
        None,
        description="Provisioner-specific typed properties (currently supported: ansible)",
    )
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description="Tool-specific configuration (e.g. playbook, inventory, ssh_private_key_secret for Ansible; backend overrides for Terraform).",
    )

    @model_validator(mode="after")
    def validate_properties_provisioner_type(self) -> "WorkspaceIacModel":
        """Ensure properties is only set for provisioner types that support it."""
        if self.properties is not None and self.provisioner != ProvisionerType.ANSIBLE:
            raise ValueError(
                f"Provisioner '{self.name}': 'properties' is only supported for ansible provisioners "
                f"(got provisioner='{self.provisioner.value}')"
            )
        return self


class WorkspaceProviderModel(PlatformBaseModel):
    name: PlatformName = Field(description="Unique provider name")
    file: str = Field(description="Path to the provider configuration file")
    description: Optional[str] = Field(
        None,
        description="Optional description of the provider for documentation purposes",
    )


class WorkspaceSpecModel(PlatformBaseModel):
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
    dns_zones: Optional[List[WorkspaceDnsModel]] = Field(None, description="DNS zone file references")

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

    # Validate unique DNS zone names
    @model_validator(mode="after")
    def validate_unique_dns_zones(self) -> "WorkspaceSpecModel":
        """Validate that all DNS zone configuration names are unique."""
        if self.dns_zones:
            dns_names = [dz.name for dz in self.dns_zones]
            duplicates = [name for name in dns_names if dns_names.count(name) > 1]
            if duplicates:
                raise ValueError(f"Duplicate DNS zone names found: {', '.join(set(duplicates))}")
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

    # Validate topology namespace references
    @model_validator(mode="after")
    def validate_topology_namespace_references(self) -> "WorkspaceSpecModel":
        """Validate that all namespace references in topology exist in namespaces section."""
        if not self.topology:
            return self

        # Get all defined namespace names
        namespace_names = set()
        if self.namespaces:
            namespace_names = {ns.name for ns in self.namespaces}

        # Check all topology namespace references
        invalid_refs = []
        for topology in self.topology:
            if not topology.namespaces:
                continue

            for ns_ref in topology.namespaces:
                if ns_ref.namespace not in namespace_names:
                    invalid_refs.append(
                        f"Topology '{topology.name}' references undefined namespace '{ns_ref.namespace}'"
                    )

        if invalid_refs:
            raise ValueError(f"Invalid namespace references in topology: {'; '.join(invalid_refs)}")

        return self

    # Validate topology provisioner name references
    @model_validator(mode="after")
    def validate_topology_provisioner_references(self) -> "WorkspaceSpecModel":
        """Validate that all provisioner references in topology exist in spec.provisioners by name."""
        if not self.topology:
            return self

        provisioner_names = {prov.name for prov in self.provisioners}

        invalid_refs = []
        for topology in self.topology:
            if topology.provisioner not in provisioner_names:
                invalid_refs.append(
                    f"Topology '{topology.name}' references undefined provisioner '{topology.provisioner}'"
                )

        if invalid_refs:
            raise ValueError(f"Invalid provisioner references in topology: {'; '.join(invalid_refs)}")

        return self


class WorkspaceMetaModel(PlatformBaseModel):
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


class WorkspaceModel(PlatformBaseModel):
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
