#!/usr/bin/env python3
"""Pydantic models for resource configuration validation."""

import re
from pathlib import PurePosixPath
from typing import Annotated, Any, Dict, List, Optional

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from strata.models.common_models import (
    CommonLifecycleModel,
    FeatureRefs,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    SecretRefs,
    VariableRefs,
    check_unique_names,
)


class ResourceDependencyModel(PlatformBaseModel):
    """
    Capability-based resource dependency model.

    Resources declare what TYPE/CATEGORY of resource they need,
    allowing the workspace to resolve to specific resource instances.
    This enables resource reusability across different workspaces and providers.

    Examples:
        - category: "networking", subcategory: "virtual_network"
          → Satisfied by Azure VNet, AWS VPC, or GCP VPC
        - category: "database", subcategory: "nosql"
          → Satisfied by Cosmos DB, DynamoDB, or Firestore
        - category: "networking", subcategory: "private_dns"
          → Satisfied by Azure Private DNS, Route53, or Cloud DNS
    """

    category: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Resource category required (e.g., networking, compute, database, storage, security)"
    )
    subcategory: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = Field(
        None,
        description="Optional subcategory for more specific requirements (e.g., virtual_network, private_dns, nosql, blob)",
    )
    resource_type: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = Field(
        None,
        description="Optional specific resource type for even more granular matching (e.g., cosmosdb_account, storage_account)",
    )
    description: Optional[str] = Field(None, description="Human-readable explanation of why this dependency exists")
    optional: bool = Field(False, description="If True, workspace can deploy without this dependency")

    @model_validator(mode="after")
    def validate_specificity(self) -> "ResourceDependencyModel":
        """Ensure at least category is provided and warn about specificity."""
        if not self.category:
            raise ValueError("Dependency must specify at least a category")

        return self


class ResourceVolumesModel(PlatformBaseModel):
    """
    Model for defining resource volume mounts (name, path).
    """

    name: Optional[PlatformName] = Field(None, description="Volume name")
    path: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Mount path for the volume"
    )


class ResourceDiskModel(PlatformBaseModel):
    """
    Model for defining resource disk configuration (size, label, mount).
    Validates label format and mount path.
    """

    name: Optional[PlatformName] = None
    size: Annotated[int, Field(gt=0, description="Disk size must be greater than 0 GB")]
    label: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Disk label for identification"
    )
    mount: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Mount path for the disk"
    )

    @field_validator("label")
    @classmethod
    def validate_label(cls, v: str) -> str:
        # Check for parameter substitution patterns
        param_pattern = r"\$\{([^}]+)\}"
        params = re.findall(param_pattern, v)

        for param in params:
            if param != ".name":
                raise ValueError(f"Unsupported parameter '${{{param}}}' in label. Only '${{.name}}' is supported.")

        # Check that the label would still be valid after parameter substitution
        # Replace ${.name} with a placeholder to validate the overall structure
        test_label = re.sub(r"\$\{\.name\}", "test-vm", v)
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-_]*$", test_label):
            raise ValueError(
                f"Label '{v}' contains invalid characters. Use only alphanumeric, hyphens, and underscores."
            )

        return v

    @field_validator("mount")
    @classmethod
    def validate_mount_path(cls, v: str) -> str:
        """Validate mount path is absolute and not a system directory."""
        # Must be an absolute Linux path
        if not v.startswith("/"):
            raise ValueError(f"Mount path must be absolute (start with '/'): {v}")

        try:
            # Use PurePosixPath to validate Linux path structure
            path = PurePosixPath(v)

            # Check for invalid characters or patterns
            if ".." in path.parts:
                raise ValueError(f"Mount path cannot contain '..' components: {v}")

            # Ensure it's not just the root directory
            if str(path) == "/":
                raise ValueError("Mount path cannot be the root directory '/'")

            # Check for common system directories that shouldn't be mount points
            system_dirs = {
                "/bin",
                "/boot",
                "/dev",
                "/etc",
                "/lib",
                "/lib64",
                "/proc",
                "/run",
                "/sbin",
                "/sys",
                "/usr",
                "/var/run",
                "/var/lock",
            }
            if str(path) in system_dirs:
                raise ValueError(f"Mount path cannot be a system directory: {v}")

        except Exception as e:
            if isinstance(e, ValueError):
                raise e
            raise ValueError(f"Invalid mount path format: {v}") from e

        return v


class ResourceStorageModel(PlatformBaseModel):
    """
    Generic storage configuration for resources that need persistent storage.
    Applies to VMs, containers, databases, etc.

    Examples:
        - VM: OS disk + data disks + volume mounts
        - Container: Persistent volumes + bind mounts
        - Database: Data disk + backup disk + log disk
        - Storage Account: Containers + file shares + queues
    """

    install_path: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = Field(
        None, description="Installation directory inside the VM"
    )
    disks: Optional[List[ResourceDiskModel]] = Field(None, description="List of disk configurations")
    volumes: Optional[List[ResourceVolumesModel]] = Field(None, description="List of volume mounts")
    parameters: Optional[Dict[str, Any]] = Field(
        None, description="Optional parameters (key-value pairs for scripting)"
    )

    @model_validator(mode="after")
    def validate_unique_names_and_relationships(self) -> "ResourceStorageModel":
        """Validate unique disk/volume names and volume-disk mount relationships."""
        errors = []

        # Validate unique disk names, labels, mount points
        if self.disks:
            for label, items in [
                ("disk names", [disk.name for disk in self.disks if disk.name]),
                ("disk labels", [disk.label for disk in self.disks]),
                ("disk mount points", [disk.mount for disk in self.disks]),
            ]:
                try:
                    check_unique_names(items, label)
                except ValueError as e:
                    errors.append(str(e))

        # Validate unique volume names
        if self.volumes:
            try:
                check_unique_names([vol.name for vol in self.volumes if vol.name is not None], "volume names")
            except ValueError as e:
                errors.append(str(e))

            # Validate volumes are mounted under disk mount points
            if self.disks:
                disk_mounts = [disk.mount for disk in self.disks]
                for volume in self.volumes:
                    # Check if volume path starts with any disk mount
                    is_under_disk = any(volume.path.startswith(mount) for mount in disk_mounts)
                    if not is_under_disk:
                        errors.append(
                            f"Volume '{volume.name}' path '{volume.path}' is not under any disk mount point. "
                            f"Available disk mounts: {disk_mounts}"
                        )

        if errors:
            raise ValueError("; ".join(errors))

        return self


class ResourcePropertiesModel(PlatformBaseModel):
    """
    Model for resource properties (unit cost, installpoint, configuration).
    """

    provider_type: PlatformName = Field(
        ...,
        description="Cloud/infrastructure provider. Must be a known provider string matching PlatformName pattern.",
    )
    resource_type: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Type of the resource"
    )
    unit_cost: Optional[float] = Field(default=0.0, description="Unit cost for the resource")
    category: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = Field(
        None,
        description="Resource category for organization (e.g., networking, compute, database, storage)",
    )
    subcategory: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = Field(
        None,
        description="Resource subcategory for finer classification (e.g., vnet, nosql, blob)",
    )

    @field_validator("provider_type")
    @classmethod
    def validate_provider_type(cls, v: str) -> str:
        """Validate provider type format (Phase 1: static validation only)."""
        # Phase 2: Dynamic validation against configuration happens in service layer
        # Service will validate that provider_type exists in configuration.spec.providers
        return v

    @field_validator("resource_type")
    @classmethod
    def validate_resource_type(cls, v: str, info) -> str:
        """Validate resource type format (Phase 1: static validation only)."""
        # Phase 2: Dynamic validation against configuration happens in service layer
        # Service will validate that resource_type exists for the provider_type
        # in configuration.spec.providers[provider_type].resources
        return v


class ResourceReferencesModel(PlatformBaseModel):
    """
    References to variables, secrets, and features required by this resource.

    Lists the keys that must be defined in the environment configuration.
    Actual values and store backends are defined at environment/workspace level.
    """

    variables: VariableRefs = Field(
        None,
        description="List of variable keys this resource requires from environment",
    )
    secrets: SecretRefs = Field(None, description="List of secret keys this resource requires from environment")
    features: FeatureRefs = Field(
        None,
        description="List of feature flag keys this resource requires from environment",
    )


class ResourceSpecModel(PlatformBaseModel):
    """
    Resource specification containing properties, references, and lifecycle configuration.
    """

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

    @model_validator(mode="after")
    def validate_configuration_schema(self) -> "ResourceSpecModel":
        """Validate configuration block (Phase 1: basic checks only)."""
        # Phase 2: Dynamic validation against configuration happens in service layer
        # Service will validate that configuration fields match the schema defined in
        # configuration.spec.providers[provider_type].resources[resource_type].configuration
        if self.configuration:
            # Basic validation: ensure it's a dict
            if not isinstance(self.configuration, dict):
                raise ValueError("Configuration must be a dictionary")
        return self


class ResourceMetaModel(PlatformBaseModel):
    """Model for resource metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique resource name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")


class ResourceModel(PlatformBaseModel):
    """
    Top-level model for a resource definition.
    Includes metadata, specification, and validation for provider configuration requirements.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for resource configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.RESOURCE,
        frozen=True,
        description="Platform kind (always 'Resource')",
    )
    meta: ResourceMetaModel = Field(description="Resource metadata (name, annotations, labels, tags)")
    spec: ResourceSpecModel = Field(description="Resource specification (properties, lifecycle, ...)")
