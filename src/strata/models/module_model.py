#!/usr/bin/env python3
"""Pydantic models for module configuration validation."""

from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    Field,
    model_validator,
)

from strata.models.common_models import (
    CommonLifecycleModel,
    FeatureRefs,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    SecretRefs,
    ServiceDeployerType,
    SourceModel,
    VariableRefs,
)


class ModuleReferenceModel(BaseModel):
    """
    References to variables, secrets, and features required by this module.

    Lists the keys that must be defined in the environment configuration.
    Actual values and store backends are defined at environment/workspace level.

    Similar to how SourceModel references repositories by name, this model
    references variable/secret/feature keys by name.
    """

    variables: VariableRefs = Field(None, description="List of variable keys this module requires from environment")
    secrets: SecretRefs = Field(None, description="List of secret keys this module requires from environment")
    features: FeatureRefs = Field(
        None,
        description="List of feature flag keys this module requires from environment",
    )


class ModuleEndpointModel(BaseModel):
    """Model for a module endpoint configuration."""

    name: Optional[PlatformName] = Field(None, description="Name of the endpoint")
    label: Optional[str] = Field(None, description="Label for the endpoint")
    url: Optional[str] = Field(None, description="URL or address of the endpoint")
    type: Optional[str] = Field(None, description="Type of endpoint (e.g., http, tcp)")
    port: Optional[int] = Field(None, description="Port number for the endpoint")
    protocol: Optional[str] = Field(None, description="Protocol for the endpoint (e.g., tcp, udp)")


class ModuleCheckModel(BaseModel):
    """Model for a module health check configuration."""

    name: PlatformName = Field(description="Name of the health check")
    label: Optional[str] = Field(None, description="Label for the health check")
    target: Optional[str] = Field(None, description="Target resource for the health check")
    type: Optional[str] = Field(None, description="Type of health check (e.g., http, tcp, command)")
    interval: Optional[str] = Field(None, description="Interval between health checks (e.g., '30s')")
    timeout: Optional[str] = Field(None, description="Timeout for each health check (e.g., '5s')")
    retries: Optional[int] = Field(None, description="Number of retries before marking as unhealthy")
    command: Optional[List[str]] = Field(None, description="Command to run for 'command' type health checks")


class ModuleMountModel(BaseModel):
    """Model for a module mount configuration."""

    name: Optional[PlatformName] = Field(None, description="Name of the mount")
    type: Optional[str] = Field(None, description="Type of the mount (e.g., volume, bind)")
    change_mod: Optional[str] = Field(None, description="Permissions for the mount (e.g., '755')")
    target_path: Optional[str] = Field(None, description="Path inside the module")
    source_path: Optional[str] = Field(None, description="Source path of the mount (bind mount host path)")
    description: Optional[str] = Field(None, description="Description of the mount")

    # Compose: reference a named volume declared in workspace topology
    volume_ref: Optional[str] = Field(
        None,
        description="Name of a WorkspaceVolumeModel to mount. Builder emits a Docker named volume. "
        "Mutually exclusive with storage_class.",
    )

    # Helm / K8s: PersistentVolumeClaim fields
    storage_class: Optional[str] = Field(
        None,
        description="Kubernetes StorageClass name (e.g. 'standard', 'fast-ssd'). "
        "When set, builder generates a PVC. Mutually exclusive with volume_ref.",
    )
    access_mode: Optional[str] = Field(
        None,
        description="PVC access mode (e.g. 'ReadWriteOnce', 'ReadWriteMany'). Defaults to 'ReadWriteOnce'.",
    )
    storage_size: Optional[str] = Field(
        None,
        description="PVC storage size (e.g. '10Gi'). Required when storage_class is set.",
    )

    @model_validator(mode="after")
    def validate_mount_mode(self) -> "ModuleMountModel":
        """volume_ref and storage_class are mutually exclusive."""
        if self.volume_ref is not None and self.storage_class is not None:
            raise ValueError(
                "ModuleMountModel: volume_ref (Docker named volume) and storage_class (PVC) "
                "are mutually exclusive. Use one or the other."
            )
        if self.storage_class is not None and self.storage_size is None:
            raise ValueError("ModuleMountModel: storage_size is required when storage_class is set.")
        return self


class ModuleServiceEnvironmentModel(BaseModel):
    """
    One environment variable on a service container.

    Exactly one of value / var / secret / feature must be set:
      - value:   literal string written directly into the artifact
      - var:     key in module spec.references.variables — resolved at build time
      - secret:  key in module spec.references.secrets — emitted as ${KEY} substitution,
                 injected via .env file (compose) or --set flag (helm) at deploy time
      - feature: key in module spec.references.features — resolved to "true"/"false"

    Example::

        environment:
          - key: POSTGRES_PASSWORD
            secret: DB_PASSWORD
          - key: TZ
            value: Europe/Brussels
          - key: APP_VERSION
            var: APP_VERSION
    """

    key: str = Field(description="Environment variable name (e.g. POSTGRES_PASSWORD)")
    value: Optional[str] = Field(None, description="Literal environment variable value")
    var: Optional[str] = Field(
        None,
        alias="variable",
        description="Variable key from module references.variables — resolved at build time",
    )
    secret: Optional[str] = Field(
        None,
        description="Secret key from module references.secrets — emitted as ${KEY} substitution",
    )
    feature: Optional[str] = Field(
        None,
        description="Feature flag key from module references.features — resolved to 'true'/'false'",
    )

    @model_validator(mode="after")
    def validate_exactly_one_source(self) -> "ModuleServiceEnvironmentModel":
        """Exactly one of value / var / secret / feature must be set."""
        sources = [f for f in (self.value, self.var, self.secret, self.feature) if f is not None]
        if len(sources) == 0:
            raise ValueError(
                f"Environment variable '{self.key}' must have exactly one of: value, var, secret, feature."
            )
        if len(sources) > 1:
            raise ValueError(
                f"Environment variable '{self.key}' has multiple sources set. "
                "Use exactly one of: value, var, secret, feature."
            )
        return self


class ModuleServiceModel(BaseModel):
    """
    One container (or sub-chart component) within a module.

    For compose modules, each service maps to a Docker Compose service entry.
    For helm modules, each service maps to a values section / sub-chart configuration.
    Service names are prefixed with the module name by the builder to avoid collisions
    (e.g. service 'redis' in module 'authentik' becomes 'authentik-redis' in compose).
    Exception: if module.name == service.name the prefix is omitted.

    ``depends_on`` is intra-module only — list short service names within THIS module.
    The builder rewrites them to the prefixed form automatically.
    """

    name: PlatformName = Field(description="Service identifier within the module")
    image: Optional[str] = Field(
        None,
        description="Container image and tag (e.g. 'postgres:16-alpine'). "
        "Omit for Helm charts that define their own image.",
    )
    command: Optional[List[str]] = Field(
        None,
        description="Override the container entrypoint/command (e.g. ['worker'] for Authentik worker)",
    )
    restart: Optional[str] = Field(
        None,
        description="Compose restart policy (e.g. 'unless-stopped'). Ignored by helm/argocd.",
    )
    environment: Optional[List[ModuleServiceEnvironmentModel]] = Field(
        None,
        description="Environment variables for this service. Use var/secret/feature refs to avoid plaintext secrets.",
    )
    ports: Optional[List[str]] = Field(
        None,
        description="Port mappings in '\"host:container\"' format (e.g. '8080:80'). "
        "Compose only — ignored by helm/argocd (Kubernetes Services handle exposure).",
    )
    mounts: Optional[List[ModuleMountModel]] = Field(
        None,
        description="Volume and bind mounts for this service. Use volume_ref for named volumes, "
        "storage_class for Kubernetes PVCs.",
    )
    depends_on: Optional[List[str]] = Field(
        None,
        description="Short service names within this module that must start before this service. "
        "Intra-module only. Builder rewrites to prefixed names automatically.",
    )
    healthcheck: Optional[ModuleCheckModel] = Field(
        None,
        description="Health check for this service. Maps to Docker Compose healthcheck or "
        "Kubernetes readinessProbe depending on module type.",
    )
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description="Deployer-specific overrides merged verbatim. For compose: merged into the service block. "
        "For helm: merged into values.{service.name}.",
    )


class ModulePropertiesModel(BaseModel):
    """Model for module-specific properties and configurations."""

    mounts: Optional[List[ModuleMountModel]] = Field(None, description="List of module mount configurations")
    checks: Optional[List[ModuleCheckModel]] = Field(None, description="List of module health check configurations")
    endpoints: Optional[List[ModuleEndpointModel]] = Field(None, description="List of module endpoint configurations")


class ModuleSpecModel(BaseModel):
    """Model for module spec (lifecycle, modules, validation)."""

    source: SourceModel = Field(description="Module deployment configuration")
    type: Optional[ServiceDeployerType] = Field(
        None,
        description="Service deployer type for this module (helm, compose, argocd, script). Required for service deployment commands.",
    )
    lifecycle: Optional[CommonLifecycleModel] = Field(None, description="Module-specific lifecycle hooks")
    properties: Optional[ModulePropertiesModel] = Field(
        None, description="Module-specific properties and configurations"
    )
    references: Optional[ModuleReferenceModel] = Field(
        None, description="Module references for variable and secret injection"
    )
    configuration: Optional[Dict[str, Any]] = Field(None, description="Module-specific configuration data")

    # Multi-container service definitions
    services: Optional[List[ModuleServiceModel]] = Field(
        None,
        description="List of services (containers/sub-charts) that make up this module. "
        "When absent, the module is treated as single-service using the properties shape (backward compatible). "
        "When present, each entry defines one container (compose) or sub-chart section (helm).",
    )

    # Helm / ArgoCD deploy identity
    release_name: Optional[str] = Field(
        None,
        description="Helm release name or ArgoCD Application name. Defaults to module.meta.name when not set.",
    )
    kubernetes_namespace: Optional[str] = Field(
        None,
        description="Kubernetes namespace to deploy this module into. "
        "Defaults to the strata namespace name when not set.",
    )

    @model_validator(mode="after")
    def validate_service_names_unique(self) -> "ModuleSpecModel":
        """Service names must be unique within a module."""
        if self.services:
            names = [s.name for s in self.services]
            duplicates = [n for n in names if names.count(n) > 1]
            if duplicates:
                raise ValueError(
                    f"Duplicate service names in module: {', '.join(set(duplicates))}. "
                    "Each service must have a unique name within the module."
                )
        return self


class ModuleMetaModel(BaseModel):
    """Model for module metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique module name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags for the module")


class ModuleModel(BaseModel):
    """Top-level model for a module resource."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for module configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.MODULE,
        frozen=True,
        description="Resource kind (always 'Module')",
    )
    meta: ModuleMetaModel = Field(description="Module metadata (name, annotations, labels, tags)")
    spec: ModuleSpecModel = Field(description="Module specification (lifecycle, modules, variables, secrets)")
