#!/usr/bin/env python3
"""Pydantic models for resource configuration validation."""

import re
from pathlib import PurePosixPath
from typing import Annotated, Any

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
    validate_kind_matches,
)
from strata.utils.names import check_unique_names


class ResourceDependencyModel(PlatformBaseModel):
    """
    Capability-based resource dependency model.

    Resources declare what TYPE/CATEGORY of resource they need,
    allowing the workspace to resolve to specific resource instances.
    This enables resource reusability across different workspaces and providers.

    Examples:
        - category: "networking", subcategory: "virtual_network"
          -> Satisfied by Azure VNet, AWS VPC, or GCP VPC
        - category: "database", subcategory: "nosql"
          -> Satisfied by Cosmos DB, DynamoDB, or Firestore
    """

    category: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Resource category required (e.g., networking, compute, database, storage, security)"
    )
    subcategory: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None,
        description="Optional subcategory for more specific requirements (e.g., virtual_network, private_dns, nosql, blob)",
    )
    resource_type: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None,
        description="Optional specific resource type for even more granular matching (e.g., cosmosdb_account, storage_account)",
    )
    description: str | None = Field(None, description="Human-readable explanation of why this dependency exists")
    optional: bool = Field(False, description="If True, workspace can deploy without this dependency")

    @model_validator(mode="after")
    def validate_specificity(self) -> "ResourceDependencyModel":
        """Ensure at least category is provided."""
        if not self.category:
            raise ValueError("Dependency must specify at least a category")
        return self


class ResourceVolumesModel(PlatformBaseModel):
    """Model for defining resource volume mounts (name, path)."""

    name: PlatformName | None = Field(None, description="Volume name")
    path: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Mount path for the volume"
    )


class ResourceDiskModel(PlatformBaseModel):
    """
    Model for defining resource disk configuration (size, label, mount).
    Validates label format and mount path.
    """

    name: PlatformName | None = None
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
        """Validate label format, allowing only the `${.name}` substitution parameter."""
        param_pattern = r"\$\{([^}]+)\}"
        params = re.findall(param_pattern, v)

        for param in params:
            if param != ".name":
                raise ValueError(f"Unsupported parameter '${{{param}}}' in label. Only '${{.name}}' is supported.")

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
        if not v.startswith("/"):
            raise ValueError(f"Mount path must be absolute (start with '/'): {v}")

        try:
            path = PurePosixPath(v)

            if ".." in path.parts:
                raise ValueError(f"Mount path cannot contain '..' components: {v}")

            if str(path) == "/":
                raise ValueError("Mount path cannot be the root directory '/'")

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
    """

    install_path: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None, description="Installation directory inside the VM"
    )
    disks: list[ResourceDiskModel] | None = Field(None, description="List of disk configurations")
    volumes: list[ResourceVolumesModel] | None = Field(None, description="List of volume mounts")
    parameters: dict[str, Any] | None = Field(None, description="Optional parameters (key-value pairs for scripting)")

    @model_validator(mode="after")
    def validate_unique_names_and_relationships(self) -> "ResourceStorageModel":
        """Validate unique disk/volume names and volume-disk mount relationships."""
        errors = []

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

        if self.volumes:
            try:
                check_unique_names([vol.name for vol in self.volumes if vol.name is not None], "volume names")
            except ValueError as e:
                errors.append(str(e))

            if self.disks:
                disk_mounts = [disk.mount for disk in self.disks]
                for volume in self.volumes:
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
    """Model for resource properties (provider, type, cost, category)."""

    provider_type: PlatformName = Field(
        description="Cloud/infrastructure provider. Must be a known provider string matching PlatformName pattern."
    )
    resource_type: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Type of the resource"
    )
    unit_cost: float | None = Field(default=0.0, description="Unit cost for the resource")
    category: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None,
        description="Resource category for organization (e.g., networking, compute, database, storage)",
    )
    subcategory: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None,
        description="Resource subcategory for finer classification (e.g., vnet, nosql, blob)",
    )

    @field_validator("provider_type")
    @classmethod
    def validate_provider_type(cls, v: str) -> str:
        """
        Validate provider type format (Phase 1: static validation only).
        Dynamic validation against configuration happens in service layer (Phase 2).
        """
        return v

    @field_validator("resource_type")
    @classmethod
    def validate_resource_type(cls, v: str) -> str:
        """
        Validate resource type format (Phase 1: static validation only).
        Dynamic validation against configuration happens in service layer (Phase 2).
        """
        return v


class ResourceSpecModel(PlatformBaseModel):
    """Resource specification containing properties and lifecycle configuration."""

    lifecycle: CommonLifecycleModel | None = Field(
        None,
        description="IaC workflow lifecycle phases, keyed by phase name",
    )
    properties: ResourcePropertiesModel = Field(
        description="Configuration properties (provider, resource type, category, cost)"
    )
    dependencies: list[ResourceDependencyModel] | None = Field(None, description="List of resource dependencies")
    storage: ResourceStorageModel | None = Field(None, description="Virtual machine specific configuration")
    configuration: dict[str, Any] | None = Field(
        None,
        description="Raw provisioner/resource-specific passthrough configuration, cross-checked in Phase 2 "
        "against the schema declared in the referenced ProviderConfig document's "
        "spec.resources[resource_type].configuration (see provider_config_model.py, ADR-0014).",
    )
    custom: dict[str, Any] | None = Field(None, description="Custom user-defined data for scripts or extensions")
    default_tags: dict[str, str] = Field(
        description="Required baseline cloud provider tags for this resource (e.g. cost-center, environment, "
        "owner — key-value, applied to the actual provisioned infrastructure). Deliberately distinct from "
        "meta.tags (a free-form list used for strata-internal categorization/documentation, not cloud tags). "
        "Strata does not enforce a maximum tag count — cloud provider/resource-type tag limits vary too much "
        "to bake into the schema; keeping default_tags + custom_tags within your target provider's limit is "
        "the resource author's responsibility."
    )
    custom_tags: dict[str, str] | None = Field(
        None,
        description="Optional additional cloud provider tags beyond default_tags, for ad-hoc/one-off tagging "
        "needs that don't belong in the required baseline set.",
    )


class ResourceMetaModel(PlatformBaseModel):
    """Model for resource metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique resource name")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")


class ResourceModel(PlatformBaseModel):
    """
    Top-level model for a resource definition.
    Includes metadata, specification, and validation for provider configuration requirements.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for resource configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.RESOURCE,
        frozen=True,
        description="Platform kind (always 'resource')",
    )
    meta: ResourceMetaModel = Field(description="Resource metadata (name, annotations, labels, tags)")
    spec: ResourceSpecModel = Field(description="Resource specification (properties, lifecycle, ...)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.RESOURCE)
