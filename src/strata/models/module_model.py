#!/usr/bin/env python3
"""Pydantic models for module configuration validation."""

from typing import Any

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    WORKLOAD_DEPLOYER_TYPES,
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    ProvisionerType,
    SourceModel,
    check_unique_names,
    validate_file_ref_no_traversal,
    validate_value_tokens,
)


class ModuleFileModel(PlatformBaseModel):
    """One file or glob pattern to copy verbatim into the module's build output directory.

    Template substitution (STRATA_* variables) is applied to all copied text files.

    Examples::

        files:
          - source: "services/traefik/traefik.yaml"
            target: "traefik.yaml"
          - source: "@infra/services/traefik/*"
            target: "config/"
    """

    source: str = Field(
        description="Source file path or glob pattern. Supports @repo/ cross-repository references. "
        "Examples: 'services/traefik/traefik.yaml', '@infra/configs/*', 'scripts/**/*.sh'"
    )
    target: str = Field(
        description="Relative destination path inside the module's build output directory. "
        "Use a trailing '/' to indicate a directory (required when source is a glob). "
        "Examples: 'traefik.yaml', 'config/', 'conf.d/'"
    )

    @field_validator("source", "target")
    @classmethod
    def validate_no_traversal(cls, v: str) -> str:
        """Reject absolute paths / '..' — both land relative to the module's build
        output directory (or a referenced repo's root, for '@repo/' sources).
        """
        validate_file_ref_no_traversal(v)
        return v

    @model_validator(mode="after")
    def validate_glob_requires_dir_target(self) -> "ModuleFileModel":
        """Glob source patterns require target to be a directory (trailing '/')."""
        if any(c in self.source for c in ("*", "?", "[")) and not self.target.endswith("/"):
            raise ValueError(
                f"source '{self.source}' is a glob pattern — target must be a directory path "
                f"ending with '/' (got '{self.target}')"
            )
        return self


class ModuleEndpointModel(PlatformBaseModel):
    """Model for a module endpoint configuration."""

    name: PlatformName | None = Field(None, description="Name of the endpoint")
    label: str | None = Field(None, description="Label for the endpoint")
    url: str | None = Field(None, description="URL or address of the endpoint")
    description: str | None = Field(None, description="Human-readable description of the endpoint")
    type: str | None = Field(None, description="Type of endpoint (e.g., http, tcp)")
    port: int | None = Field(None, description="Port number for the endpoint")
    protocol: str | None = Field(None, description="Protocol for the endpoint (e.g., tcp, udp)")


class ModuleCheckModel(PlatformBaseModel):
    """Model for a module health check configuration."""

    name: PlatformName = Field(description="Name of the health check")
    label: str | None = Field(None, description="Label for the health check")
    target: str | None = Field(None, description="Target resource for the health check")
    type: str | None = Field(None, description="Type of health check (e.g., http, tcp, command)")
    interval: str | None = Field(None, description="Interval between health checks (e.g., '30s')")
    timeout: str | None = Field(None, description="Timeout for each health check (e.g., '5s')")
    retries: int | None = Field(None, description="Number of retries before marking as unhealthy")
    endpoint: str | None = Field(None, description="Endpoint URL for HTTP-type health checks")
    command: list[str] | None = Field(None, description="Command to run for 'command' type health checks")


class ModuleMountModel(PlatformBaseModel):
    """Model for a module mount configuration."""

    name: PlatformName | None = Field(None, description="Name of the mount")
    type: str | None = Field(None, description="Type of the mount (e.g., volume, bind)")
    chmod: str | None = Field(None, description="Permissions for the mount (e.g., '0644', '0600')")
    target_path: str | None = Field(None, description="Path inside the module")
    source_path: str | None = Field(None, description="Source path of the mount (bind mount host path)")
    description: str | None = Field(None, description="Description of the mount")

    # Compose: reference a named volume declared in workspace topology
    volume_ref: str | None = Field(
        None,
        description="Name of a WorkspaceVolumeModel to mount. Builder emits a Docker named volume. "
        "Mutually exclusive with storage_class.",
    )

    # Helm / K8s: PersistentVolumeClaim fields
    storage_class: str | None = Field(
        None,
        description="Kubernetes StorageClass name (e.g. 'standard', 'fast-ssd'). "
        "When set, builder generates a PVC. Mutually exclusive with volume_ref.",
    )
    access_mode: str | None = Field(
        None,
        description="PVC access mode (e.g. 'ReadWriteOnce', 'ReadWriteMany'). Defaults to 'ReadWriteOnce'.",
    )
    storage_size: str | None = Field(
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


class ModuleServiceEnvironmentModel(PlatformBaseModel):
    """One environment variable on a service container.

    ``value`` is a Value binding (ADR-0002): a literal string (written
    directly into the artifact) or a string containing ``${var:KEY}``/
    ``${secret:KEY}``/``${feature:KEY}`` tokens, resolved once the
    build/deploy layer exists.

    Example::

        environment:
          - key: POSTGRES_PASSWORD
            value: "${secret:DB_PASSWORD}"
          - key: TZ
            value: Europe/Brussels
          - key: APP_VERSION
            value: "${var:APP_VERSION}"
    """

    key: str = Field(description="Environment variable name (e.g. POSTGRES_PASSWORD)")
    value: str = Field(
        ...,
        min_length=1,
        description="Literal value, or a string containing '${var:KEY}'/'${secret:KEY}'/'${feature:KEY}' tokens.",
    )

    @field_validator("value")
    @classmethod
    def validate_value_token_syntax(cls, v: str) -> str:
        """Reject malformed '${...}' tokens immediately (Phase 1)."""
        validate_value_tokens(v)
        return v


class ModuleServiceModel(PlatformBaseModel):
    """One container (or sub-chart component) within a module.

    For compose modules, each service maps to a Docker Compose service entry.
    For helm modules, each service maps to a values section / sub-chart configuration.
    Service names are prefixed with the module name by the builder to avoid collisions
    (e.g. service 'redis' in module 'authentik' becomes 'authentik-redis' in compose).
    Exception: if module.name == service.name the prefix is omitted.

    ``depends_on`` is intra-module only — list short service names within THIS module.
    The builder rewrites them to the prefixed form automatically.
    """

    name: PlatformName = Field(description="Service identifier within the module")
    image: str | None = Field(
        None,
        description="Container image and tag (e.g. 'postgres:16-alpine'). "
        "Omit for Helm charts that define their own image.",
    )
    command: list[str] | None = Field(
        None,
        description="Override the container entrypoint/command (e.g. ['worker'] for Authentik worker)",
    )
    restart: str | None = Field(
        None,
        description="Compose restart policy (e.g. 'unless-stopped'). Ignored by helm/argocd.",
    )
    environment: list[ModuleServiceEnvironmentModel] | None = Field(
        None,
        description="Environment variables for this service. Use '${secret:}'/'${var:}'/'${feature:}' "
        "tokens to avoid plaintext secrets.",
    )
    ports: list[str] | None = Field(
        None,
        description="Port mappings in '\"host:container\"' format (e.g. '8080:80'). "
        "Compose only — ignored by helm/argocd (Kubernetes Services handle exposure).",
    )
    mounts: list[ModuleMountModel] | None = Field(
        None,
        description="Volume and bind mounts for this service. Use volume_ref for named volumes, "
        "storage_class for Kubernetes PVCs.",
    )
    depends_on: list[str] | None = Field(
        None,
        description="Services that must start before this service. "
        "Use short names for intra-module deps (e.g. 'redis'). "
        "Use @module/service for cross-module deps within the same namespace "
        "(e.g. '@mod_auth/server'). Use @module when module name equals service name. "
        "Builder rewrites all entries to prefixed names. "
        "Intra-module refs are validated at load time; cross-module refs at build time.",
    )
    healthcheck: ModuleCheckModel | None = Field(
        None,
        description="Health check for this service. Maps to Docker Compose healthcheck or "
        "Kubernetes readinessProbe depending on module type.",
    )
    configuration: dict[str, Any] | None = Field(
        None,
        description="Deployer-specific overrides merged verbatim. For compose: merged into the service block. "
        "For helm: merged into values.{service.name}.",
    )


class ModulePropertiesModel(PlatformBaseModel):
    """Model for module-specific properties and configurations."""

    mounts: list[ModuleMountModel] | None = Field(None, description="List of module mount configurations")
    checks: list[ModuleCheckModel] | None = Field(None, description="List of module health check configurations")
    endpoints: list[ModuleEndpointModel] | None = Field(None, description="List of module endpoint configurations")


class ModuleSpecModel(PlatformBaseModel):
    """Model for module spec (source, lifecycle, services, configuration).

    No ``references`` field (ADR-0002): environment variable Value bindings
    (``${var:}``/``${secret:}``/``${feature:}`` tokens, see
    `ModuleServiceEnvironmentModel`) are checked against a real Environment in
    Phase 2, not an internal declared-keys list.
    """

    source: SourceModel = Field(description="Module deployment configuration")
    type: PlatformName | None = Field(
        None,
        description="Deployer tool for this module (e.g. helm, compose, argocd, script, or a custom "
        "provisioner plugin name). Required for service deployment commands.",
    )
    lifecycle: CommonLifecycleModel | None = Field(None, description="Module-specific lifecycle hooks")
    properties: ModulePropertiesModel | None = Field(None, description="Module-specific properties and configurations")
    configuration: dict[str, Any] | None = Field(None, description="Module-specific configuration data")

    # Multi-container service definitions
    services: list[ModuleServiceModel] | None = Field(
        None,
        description="List of services (containers/sub-charts) that make up this module. "
        "When absent, the module is treated as single-service using the properties shape (backward compatible). "
        "When present, each entry defines one container (compose) or sub-chart section (helm).",
    )

    # Compose pass-through mode
    compose_file: str | None = Field(
        None,
        description="Explicit path reference to an external docker-compose.yml file "
        "(@repo/path/to/docker-compose.yml or workspace-relative path). "
        "When set, ComposeBuilder copies this file verbatim to the build path instead of "
        "generating one from spec.services. Mutually exclusive with spec.services.",
    )

    # Extra files to copy into the build output
    files: list[ModuleFileModel] | None = Field(
        None,
        description="Extra files to copy verbatim into the module's build output directory. "
        "Supports glob patterns and @repo/ cross-repository references. "
        "Template substitution (STRATA_* variables) is applied to all copied text files.",
    )

    # Helm / ArgoCD deploy identity
    release_name: str | None = Field(
        None,
        description="Helm release name or ArgoCD Application name. Defaults to module.meta.name when not set.",
    )
    kubernetes_namespace: str | None = Field(
        None,
        description="Kubernetes namespace to deploy this module into. "
        "Defaults to the strata namespace name when not set.",
    )

    @model_validator(mode="after")
    def validate_service_names_unique(self) -> "ModuleSpecModel":
        """Service names must be unique within a module."""
        if self.services:
            check_unique_names([s.name for s in self.services], "service names in module")
        return self

    @field_validator("type")
    @classmethod
    def validate_type_is_workload_deployer(cls, v: str | None) -> str | None:
        """Reject `type` only when it's a *known* infra-provisioning built-in.

        `type` is an open string, not a closed `ProvisionerType` enum — v1's
        real `DeployerFactory` supports user-registered provisioner plugins
        (`.strata/provisioners/*.py`, optionally with a `provisioner.yaml`
        manifest) beyond the built-in tools, so a custom name must remain
        schema-valid here. If `v` matches a recognized built-in
        (`ProvisionerType`) that's NOT in `WORKLOAD_DEPLOYER_TYPES` (i.e. a
        known infra tool like terraform/ansible/bicep), reject it — that's a
        real, known mismatch. If `v` doesn't match any known built-in, allow
        it through unchecked: it may be a custom plugin, and validating that
        requires the plugin registry (deferred to Phase 2, mirroring
        `DeployerFactory.is_known_type()` — not built in v2 yet).
        """
        if v is not None:
            try:
                known = ProvisionerType(v)
            except ValueError:
                return v  # unrecognized — may be a custom provisioner plugin
            if known not in WORKLOAD_DEPLOYER_TYPES:
                allowed = ", ".join(sorted(t.value for t in WORKLOAD_DEPLOYER_TYPES))
                raise ValueError(
                    f"Module type '{v}' is a known infra-provisioning tool, not a workload deployer. "
                    f"Allowed built-ins: {allowed} (or a custom provisioner plugin name)."
                )
        return v

    @model_validator(mode="after")
    def validate_compose_file_exclusive(self) -> "ModuleSpecModel":
        """compose_file and services are mutually exclusive."""
        if self.compose_file and self.services:
            raise ValueError(
                "compose_file and services are mutually exclusive — "
                "use compose_file to reference an external docker-compose.yml, "
                "or services to generate one from the module spec."
            )
        return self

    @model_validator(mode="after")
    def validate_depends_on(self) -> "ModuleSpecModel":
        """Validate services[].depends_on.

        Intra-module entries must reference a real service name in this
        module. Cross-module '@module/service' entries are syntax-checked
        only here — actual resolution happens at build time.
        """
        if not self.services:
            return self

        service_names = {s.name for s in self.services}
        errors: list[str] = []
        for service in self.services:
            for dep in service.depends_on or []:
                if dep.startswith("@"):
                    ref = dep[1:]
                    parts = ref.split("/", 1)
                    mod_part = parts[0]
                    svc_part = parts[1] if len(parts) > 1 else None
                    if not mod_part:
                        errors.append(
                            f"service '{service.name}': depends_on '{dep}' has invalid syntax — "
                            f"expected @module or @module/service."
                        )
                    elif svc_part is not None and not svc_part:
                        errors.append(
                            f"service '{service.name}': depends_on '{dep}' has empty service name after '/'."
                        )
                elif dep not in service_names:
                    errors.append(
                        f"service '{service.name}': depends_on '{dep}' is not a service defined in this "
                        f"module. Available services: {sorted(service_names)}."
                    )
        if errors:
            raise ValueError("; ".join(errors))
        return self


class ModuleMetaModel(PlatformBaseModel):
    """Module metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique module name")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags for the module")


class ModuleModel(PlatformBaseModel):
    """Root model for a module resource."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for module configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.MODULE,
        frozen=True,
        description="Platform kind (always 'module')",
    )
    meta: ModuleMetaModel = Field(description="Module metadata (name, annotations, labels, tags)")
    spec: ModuleSpecModel = Field(description="Module specification (source, lifecycle, services, configuration)")
