"""Service for loading and validating deployment configurations."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

if TYPE_CHECKING:
    from strata.models.environment_model import EnvironmentModel

from strata.exceptions import ServiceLoadError, ServiceNotValidatedError
from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_model import DeploymentModel
from strata.services.base_service import BaseService
from strata.services.configuration_service import ConfigurationService
from strata.services.environment_service import EnvironmentService
from strata.services.workspace_service import WorkspaceService
from strata.utils.merge_provenance import MergeProvenance


class DeploymentService(BaseService["DeploymentModel"]):
    """Service for handling deployment configurations."""

    def __init__(self, path=None, data=None) -> None:
        super().__init__(path, data)
        self.model = None
        # self._related_services: Optional[Dict[str, Optional[BaseService]]] = None
        self._environment_service: Optional[EnvironmentService] = None
        self._workspace_service: Optional[WorkspaceService] = None
        self._merge_provenance: Optional[MergeProvenance] = None
        self._validation_errors: List[str] = []
        self._validation_warnings: List[str] = []
        self._structured_errors: List = []
        self._objects_path: Optional[str] = None
        self._load_repo_map: Optional[Dict[str, str]] = None

    def on_init(self) -> None:
        """Lifecycle hook: called after __init__ completes."""
        pass

    def on_ready(self) -> None:
        """Lifecycle hook: called after validation succeeds."""
        pass

    def on_shutdown(self) -> None:
        """Lifecycle hook: called before cleanup/destruction."""
        pass

    def _get_model_class(self):
        """Return the DeploymentModel class for validation."""
        return DeploymentModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Validate deployment against dynamic configuration.

        Validates cross-references when related services are loaded:
        - Deployment layer values against configuration.spec.layering
        - Deployment properties against configuration.spec.deployment.properties schema
        - Environment variable/secret references against workspace
        - Deployment variable/secret references against workspace
        - Security policies for variable/secret/feature stores

        Note: Uniqueness validations (environments, configurations, variables, secrets)
        are already enforced by MODEL validators in DeploymentSpecModel.

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation
            work_path: Optional working directory for validating bundled source paths

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        errors = []

        # Validate deployment layers against configuration layering
        if configuration_model and configuration_model.spec.layering:
            layer_errors = self._validate_deployment_layers(configuration_model)
            errors.extend(layer_errors)

        # Validate deployment properties against configuration schema
        if configuration_model and configuration_model.spec.deployment:
            properties_errors = self._validate_deployment_properties(configuration_model)
            errors.extend(properties_errors)

        # Validate that environment and configuration file references exist on disk
        if work_path and self.model:
            # Merge solution-level repo_map (self._repo_map) with config-level repo_map.
            # Solution names (e.g. 'haven') take precedence for resolving @repo/... refs
            # in deployment files, which use solution repo names, not config repo names.
            config_repo_map = configuration_model.get_remote_map() if configuration_model else {}
            repo_map = {**config_repo_map, **(self._repo_map or {})}
            file_refs = []
            for i, env_ref in enumerate(self.model.spec.environments or []):
                file_refs.append((f"Environment[{i}]", env_ref.file))
            for cfg in self.model.spec.configurations or []:
                file_refs.append((f"Configuration '{cfg.name}'", cfg.file))
            if self.model.spec.tenant:
                file_refs.append(("Tenant", f"tenants/{self.model.spec.tenant}.yaml"))
            errors.extend(self._validate_file_refs(work_path, repo_map, file_refs))

        # Deep zone check: tenant zones must all exist in configuration.spec.zones
        if (
            work_path
            and self.model
            and self.model.spec.tenant
            and configuration_model
            and configuration_model.spec.zones
        ):
            from pathlib import Path as _Path

            from strata.services.tenant_service import TenantService

            tenant_file = _Path(work_path) / "tenants" / f"{self.model.spec.tenant}.yaml"
            if tenant_file.exists():
                tenant_svc = TenantService(str(tenant_file))
                is_valid_c, _ = tenant_svc.validate()
                if is_valid_c and tenant_svc.model:
                    config_zone_names = {z.name for z in configuration_model.spec.zones}
                    for zone in tenant_svc.model.spec.zones:
                        if zone not in config_zone_names:
                            errors.append(
                                f"Tenant '{self.model.spec.tenant}' references zone '{zone}' "
                                f"which is not defined in configuration.spec.zones. "
                                f"Available zones: {sorted(config_zone_names)}"
                            )

        # Shadowed-override check (non-fatal warnings, not errors)
        if work_path and self.model and self.model.spec.versions:
            _repo_map = {
                **(configuration_model.get_remote_map() if configuration_model else {}),
                **(self._repo_map or {}),
            }
            self._validation_warnings = self._check_version_pin_shadows(work_path, _repo_map)

        return len(errors) == 0, errors

    def _check_version_pin_shadows(
        self,
        work_path: str,
        repo_map: Optional[Dict[str, str]],
    ) -> List[str]:
        """Load env file(s) and version pins, return warning strings for shadowed overrides.

        Never raises — all failures are silently suppressed.  Warnings are non-fatal.
        """
        if not self.model or not self.model.spec.versions:
            return []
        try:
            from pathlib import Path as _Path

            from strata.services.environment_service import EnvironmentService
            from strata.services.version_service import VersionService

            _rm = repo_map or {}

            # Resolve env file paths
            env_paths = [
                self._resolve_file_path(env_ref.file, work_path, _rm)
                for env_ref in (self.model.spec.environments or [])
            ]
            valid_paths = [p for p in env_paths if _Path(p).exists()]
            if not valid_paths:
                return []

            # Load env model (single or merged)
            if len(valid_paths) == 1:
                env_svc = EnvironmentService.load(valid_paths[0], validate=True)
                if not env_svc.model:
                    return []
                env_model = env_svc.model
            else:
                env_model, _ = EnvironmentService.merge_envfiles(valid_paths, _Path(work_path))

            # Resolve version pins
            def _resolve_fn(file_ref: str, base: str) -> str:
                return self._resolve_file_path(file_ref, base, _rm)

            pins = VersionService.load_and_resolve(
                version_refs=self.model.spec.versions,
                objects_path=work_path,
                resolve_path_fn=_resolve_fn,
            )
            if not any(pins.values()):
                return []

            return VersionService.find_shadowed_overrides(env_model, pins)
        except Exception:
            return []  # warnings never abort validation

    def _validate_deployment_layers(self, configuration_model: ConfigurationModel) -> List[str]:
        """
        Validate deployment layer values against configuration layering definition.

        Args:
            configuration_model: Configuration model with layering definition

        Returns:
            List[str]: List of validation error messages
        """
        errors: List[str] = []

        # No validation if no layering configured
        if not configuration_model.spec.layering:
            return errors

        # CRITICAL: Validate last layer is named "environment" (should already be validated in model)
        if configuration_model.spec.layering[-1].name != "environment":
            errors.append(
                f"Configuration error: Last layer must be named 'environment', "
                f"got '{configuration_model.spec.layering[-1].name}'"
            )
            return errors  # Fatal error - can't proceed with other validations

        if not self.model:
            errors.append("Deployment model is not initialized")
            return errors

        deployment_values = self.model.spec.layers or {}

        # Validate all required layers are provided
        for layer in configuration_model.spec.layering:
            if layer.required and layer.name not in deployment_values:
                errors.append(f"Required layer '{layer.name}' not provided in deployment")

            # Validate pattern if value exists and pattern is defined
            if layer.name in deployment_values and layer.pattern:
                value = deployment_values[layer.name]
                try:
                    if not re.match(layer.pattern, value):
                        errors.append(f"Layer '{layer.name}' value '{value}' does not match pattern '{layer.pattern}'")
                except re.error as e:
                    errors.append(f"Invalid regex pattern for layer '{layer.name}': {layer.pattern} - {e}")

        # CRITICAL: Validate environment is provided (last layer must always have a value)
        if "environment" not in deployment_values:
            errors.append("Required layer 'environment' not provided in deployment. ")

        # Warn on unknown layers (not in configuration)
        configured_names = {layer.name for layer in configuration_model.spec.layering}
        unknown_layers = set(deployment_values.keys()) - configured_names
        if unknown_layers:
            self.logger.warning("Deployment contains unknown layers", unknown_layers=unknown_layers)

        return errors

    def _validate_deployment_properties(self, configuration_model: ConfigurationModel) -> List[str]:
        """
        Validate deployment properties against configuration schema.

        The schema is defined in configuration.spec.deployment.properties
        where each field maps to a regex pattern that values must match.

        When additional_properties=False, only fields in the schema are allowed.
        When additional_properties=True, extra fields are permitted.

        Args:
            configuration_model: Configuration model with deployment schema

        Returns:
            List[str]: List of validation error messages
        """
        errors: List[str] = []

        # No validation if no deployment schema configured
        if not self.model or not self.model.spec or not configuration_model.spec.deployment:
            return errors

        deployment_config = configuration_model.spec.deployment
        schema = deployment_config.properties or {}
        additional_allowed = deployment_config.additional_properties
        deployment_properties = self.model.spec.properties or {}

        # Validate each field in deployment.spec.properties
        for field_name, field_value in deployment_properties.items():
            if field_name not in schema:
                # Field not in schema
                if not additional_allowed:
                    errors.append(
                        f"Deployment property '{field_name}' is not allowed. "
                        f"additional_properties is False. Valid fields: {list(schema.keys())}"
                    )
                continue

            schema_def = schema[field_name]

            # Handle both string pattern (legacy) and structured field object
            if isinstance(schema_def, str):
                # Legacy: string pattern (always required)
                pattern = schema_def
            elif isinstance(schema_def, dict):
                # Structured field with pattern and required flag
                pattern = schema_def.get("pattern")
                if not pattern:
                    errors.append(f"Deployment property '{field_name}' schema is missing 'pattern' property")
                    continue
            else:
                # ConfigurationSchemaField model instance
                pattern = schema_def.pattern

            # Convert value to string for pattern matching
            value_str = str(field_value)

            # Validate against regex pattern
            try:
                if not re.match(pattern, value_str):
                    errors.append(
                        f"Deployment property '{field_name}' value '{value_str}' does not match "
                        f"required pattern '{pattern}'"
                    )
            except re.error as e:
                errors.append(
                    f"Invalid regex pattern '{pattern}' for property '{field_name}' in deployment schema: {str(e)}"
                )

        # Check for required fields (fields in schema but not in properties)
        for schema_field, schema_def in schema.items():
            if schema_field in deployment_properties:
                continue

            # Determine if field is required
            is_required = True  # Default to required
            if isinstance(schema_def, dict):
                is_required = schema_def.get("required", True)
            elif hasattr(schema_def, "required"):
                is_required = getattr(schema_def, "required", True)

            if is_required:
                # Get pattern for error message
                if isinstance(schema_def, str):
                    pattern = schema_def
                elif isinstance(schema_def, dict):
                    pattern = schema_def.get("pattern", "N/A")
                else:
                    pattern = schema_def.pattern

                errors.append(f"Required deployment property '{schema_field}' is missing. Pattern: {pattern}")

        return errors

    def get_artifact_path(self, configuration_model: Optional["ConfigurationModel"] = None) -> str:
        """
        Construct artifact path from deployment layer values.

        The artifact path is constructed from the deployment.spec.deployment dictionary
        following the order defined in configuration.spec.layering.

        Args:
            configuration_model: Configuration model with layering definition

        Returns:
            str: Artifact path (e.g., "zone-europe/acme/production/prd")

        Example:
            Configuration defines: zone → tenant → space → environment
            Deployment provides: {zone: "eu", tenant: "acme", space: "default", environment: "prod"}
            Result: "eu/acme/default/prod"

        Note:
            - If no layering configured, returns empty string
            - If deployment has no layer values, returns empty string
            - Missing optional layers use default values from configuration
            - Path components follow layer order from configuration
        """
        self._ensure_validated()

        # No artifact path if no configuration or no layering defined
        if not configuration_model or not configuration_model.spec.layering:
            return ""

        if self.model is None or self.model.spec is None or self.model.spec.layers is None:
            return ""

        deployment_values = self.model.spec.layers or {}
        if not deployment_values:
            return ""

        # Build path components in layer order
        path_components = []
        for layer in configuration_model.spec.layering:
            value = deployment_values.get(layer.name)

            # Use default if not provided and default exists
            if value is None and layer.default:
                value = layer.default

            # Skip if still no value (optional layer without default)
            if value is not None:
                path_components.append(value)

        return "/".join(path_components)

    def get_build_path(self, build_path: Path) -> Path:
        """
        Get the build path for the deployment.

        Constructs a build directory path using deployment name and version.

        Args:
            build_path: Base build directory path

        Returns:
            Path: Build path for this deployment (build_path / "{name}-{version}")

        Example:
            build_path = Path("/tmp/builds")
            deployment name = "test_deployment"
            deployment version = "1.0.0"
            returns: Path("/tmp/builds/test_deployment-1.0.0")
        """
        deployment_name = self.get_name()
        deployment_version = self.get_version()
        return build_path / f"{deployment_name}-{deployment_version}"

    def get_validation_errors(self) -> List[str]:
        """Return all validation errors: Phase 1 (Pydantic) errors from _errors plus
        dynamic errors accumulated in _validation_errors."""
        return self._errors + self._validation_errors

    def get_validation_warnings(self) -> List[str]:
        """Return non-fatal warnings accumulated during Phase 2 validation.

        Currently populated by the shadowed-override check: reports ``spec.overrides``
        entries that are silently overwritten by ``spec.versions`` pins.
        """
        return list(self._validation_warnings)

    def apply_environment_overrides(self) -> Tuple[bool, List[str]]:
        """
        Apply environment overrides to workspace resources, providers, and modules.

        This modifies the loaded workspace services in-place to reflect environment-specific
        configurations. Should be called after load_related_services() and validate_related_services().

        Overrides are applied in precedence order:
        1. Workspace base values (lowest priority)
        2. Environment properties (if any)
        3. Environment-specific overrides (highest priority)

        Note: Stages are pipeline metadata only and have no relationship to environment overrides.
              1 deployment = 1 workspace instance with 1 environment's overrides applied.

        Returns:
            Tuple[bool, List[str]]: (success, list of error/warning messages)

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        if self._workspace_service is None or self._environment_service is None:
            raise ServiceNotValidatedError(
                "DeploymentService",
                reason="Call load_deploy_services() before apply_environment_overrides()",
            )

        errors = []
        workspace = self._workspace_service
        environment = self._environment_service

        # Can't apply without workspace
        if not workspace:
            errors.append("Workspace service not loaded, cannot apply overrides")
            return False, errors

        # Can't apply without environment or if no overrides defined
        if not environment:
            self.logger.debug("No environment loaded, skipping override application")
            return True, []

        if not environment.has_overrides():
            self.logger.debug("No overrides defined in environment")
            return True, []

        # Get workspace services
        # resources = self.get_resource_services() or {}
        # providers = self.get_provider_services() or {}

        self.logger.info(
            "Applying environment overrides to workspace",
            deployment_name=self.get_name(),
            environment_name=environment.get_name(),
        )

        # Apply resource overrides
        for resource_name in environment.get_overridden_resource_names():
            resource_override = environment.get_resource_override(resource_name)
            if not resource_override:
                continue

            # Get the workspace resource model
            workspace_resource = None
            if workspace.model and workspace.model.spec and workspace.model.spec.resources:
                workspace_resource = next(
                    (r for r in workspace.model.spec.resources if r.name == resource_name),
                    None,
                )
            if not workspace_resource:
                errors.append(f"Resource override for non-existent resource '{resource_name}' (skipped)")
                self.logger.warning("Skipping override for non-existent resource", resource=resource_name)
                continue

            # Apply overrides (only override non-None values)
            if resource_override.description is not None:
                workspace_resource.description = resource_override.description
            if resource_override.enabled is not None:
                workspace_resource.enabled = resource_override.enabled
            if resource_override.condition is not None:
                workspace_resource.condition = resource_override.condition
            if resource_override.role is not None:
                workspace_resource.role = resource_override.role
            if resource_override.count is not None:
                workspace_resource.count = resource_override.count
            if resource_override.depends_on is not None:
                workspace_resource.depends_on = resource_override.depends_on
            if resource_override.references is not None:
                # Merge references (override wins)
                if workspace_resource.references:
                    workspace_resource.references.update(resource_override.references)
                else:
                    workspace_resource.references = resource_override.references
            if resource_override.firewalls is not None:
                workspace_resource.firewalls = resource_override.firewalls
            if resource_override.configuration is not None:
                # Deep merge configuration (override wins)
                if workspace_resource.configuration:
                    workspace_resource.configuration.update(resource_override.configuration)
                else:
                    workspace_resource.configuration = resource_override.configuration
            if resource_override.custom is not None:
                # Deep merge custom (override wins)
                if workspace_resource.custom:
                    workspace_resource.custom.update(resource_override.custom)
                else:
                    workspace_resource.custom = resource_override.custom
            if resource_override.labels is not None:
                # Merge labels (override wins)
                if workspace_resource.labels:
                    workspace_resource.labels.update(resource_override.labels)
                else:
                    workspace_resource.labels = resource_override.labels
            if resource_override.tags is not None:
                workspace_resource.tags = resource_override.tags

            self.logger.debug("Applied resource override", resource=resource_name)

        # Apply module overrides
        for module_key in environment.get_overridden_module_keys():
            if not isinstance(module_key, tuple) or len(module_key) < 1:
                continue
            module_name = module_key[0]
            override_resource = module_key[1] if len(module_key) > 1 else None
            override_namespace = module_key[2] if len(module_key) > 2 else None
            override_slot = module_key[3] if len(module_key) > 3 else None

            # Find all matching modules in workspace resources
            targets_found = False
            if workspace.model and workspace.model.spec and workspace.model.spec.resources:
                for ws_resource in workspace.model.spec.resources:
                    # Skip if override narrows to a specific resource that doesn't match
                    if override_resource and ws_resource.name != override_resource:
                        continue
                    if not ws_resource.modules:
                        continue
                    for target_module in ws_resource.modules:
                        if target_module.name != module_name:
                            continue
                        if override_slot and (target_module.slot_type or "main") != override_slot:
                            continue

                        module_override = environment.get_module_override(
                            module_name=module_name,
                            resource_name=ws_resource.name,
                            slot_type=target_module.slot_type or "main",
                        )
                        if not module_override:
                            continue

                        targets_found = True
                        if module_override.slot_type is not None:
                            target_module.slot_type = module_override.slot_type
                        if module_override.enabled is not None:
                            target_module.enabled = module_override.enabled
                        if module_override.configuration is not None:
                            if target_module.configuration:
                                target_module.configuration.update(module_override.configuration)
                            else:
                                target_module.configuration = module_override.configuration

                        self.logger.debug(
                            "Applied module override",
                            resource=ws_resource.name,
                            module=module_name,
                        )

            if not targets_found:
                scope = override_resource or override_namespace or "workspace"
                errors.append(f"Module override for '{module_name}' (not found in {scope} - skipped)")
                self.logger.warning(
                    "Skipping module override — module not found",
                    module=module_name,
                    resource=override_resource,
                    namespace=override_namespace,
                )

        # Apply provider overrides — description on workspace ref; file swap and configuration on the loaded service
        for provider_name in environment.get_overridden_provider_names():
            provider_override = environment.get_provider_override(provider_name)
            if not provider_override:
                continue

            # Get the workspace provider model (the reference entry, not the loaded file)
            workspace_provider = None
            if workspace.model and workspace.model.spec and workspace.model.spec.providers:
                workspace_provider = next(
                    (p for p in workspace.model.spec.providers if p.name == provider_name),
                    None,
                )
            if not workspace_provider:
                errors.append(f"Provider override for non-existent provider '{provider_name}' (skipped)")
                self.logger.warning("Skipping override for non-existent provider", provider=provider_name)
                continue

            # Apply description on the workspace reference
            if provider_override.description is not None:
                workspace_provider.description = provider_override.description

            # Apply file override — swap the entire provider binding and reload
            if provider_override.file is not None:
                if self._objects_path and self._load_repo_map is not None:
                    from strata.services.provider_service import ProviderService as _ProviderService

                    new_file = provider_override.file
                    resolved_path = self._resolve_file_path(new_file, self._objects_path, self._load_repo_map)
                    try:
                        new_provider_svc = _ProviderService.load(resolved_path, validate=True)
                        if new_provider_svc.is_validated():
                            # Validate meta.name matches the expected workspace provider name (hard error)
                            loaded_meta_name = (
                                str(new_provider_svc.model.meta.name)
                                if new_provider_svc.model and new_provider_svc.model.meta
                                else None
                            )
                            if loaded_meta_name != provider_name:
                                errors.append(
                                    f"Provider '{provider_name}' file override '{new_file}' has mismatched "
                                    f"meta.name: expected '{provider_name}', got '{loaded_meta_name}'"
                                )
                                self.logger.error(
                                    "Provider file override meta.name mismatch",
                                    provider=provider_name,
                                    expected=provider_name,
                                    got=loaded_meta_name,
                                    file=new_file,
                                )
                            else:
                                workspace.replace_provider_service(provider_name, new_provider_svc)
                                workspace_provider.file = new_file
                                self.logger.info(
                                    "Provider file override applied",
                                    provider=provider_name,
                                    file=new_file,
                                    resolved=resolved_path,
                                )
                        else:
                            errs = new_provider_svc.get_validation_errors()
                            errors.append(
                                f"Provider '{provider_name}' file override '{new_file}' failed validation: "
                                + "; ".join(errs)
                            )
                            self.logger.error(
                                "Provider file override validation failed",
                                provider=provider_name,
                                file=new_file,
                            )
                    except Exception as exc:
                        errors.append(
                            f"Provider '{provider_name}' file override '{new_file}' could not be loaded: {exc}"
                        )
                        self.logger.error(
                            "Failed to load provider file override",
                            provider=provider_name,
                            file=new_file,
                            exc_info=True,
                        )
                else:
                    errors.append(
                        f"Provider '{provider_name}' file override cannot be applied: "
                        "objects_path not available (call load_deploy_services first)"
                    )

            # Apply configuration (property overrides) to the loaded provider service
            # Applied AFTER file override so configuration can further tweak the replacement file
            if provider_override.configuration:
                provider_service = workspace.get_provider_service(provider_name)
                if provider_service and provider_service.model and provider_service.model.spec:
                    props = provider_service.model.spec.properties
                    known_fields = set(props.model_fields.keys())
                    for key, value in provider_override.configuration.items():
                        if key in known_fields:
                            setattr(props, key, value)
                            self.logger.debug(
                                "Applied provider property override",
                                provider=provider_name,
                                field=key,
                                value=value,
                            )
                        else:
                            self.logger.warning(
                                "Provider configuration override key is not a known property — skipped",
                                provider=provider_name,
                                key=key,
                                known=sorted(known_fields),
                            )

            self.logger.debug("Applied provider override", provider=provider_name)

        # Apply remote reference overrides (pin a remote to a specific version/tag/branch)
        for remote_name in environment.get_overridden_remote_names():
            remote_override = environment.get_remote_override(remote_name)
            if not remote_override:
                continue

            config_service = ConfigurationService.get_instance()
            config_remote = None
            if (
                config_service
                and config_service.model
                and config_service.model.spec
                and config_service.model.spec.remotes
            ):
                config_remote = next(
                    (r for r in config_service.model.spec.remotes if str(r.name) == remote_name),
                    None,
                )
            if not config_remote:
                # Phase 2 validation should have caught this; treat as critical if it slips through
                errors.append(f"Remote override for '{remote_name}' does not match any remote in configuration")
                self.logger.error(
                    "Remote override targets unknown remote",
                    remote=remote_name,
                )
                continue

            # Mutate in-place — safe per-process (each CLI invocation is an isolated Python process)
            old_ref = config_remote.reference
            config_remote.reference = remote_override.reference
            self.logger.info(
                "Applied remote reference override",
                remote=remote_name,
                old_reference=old_ref,
                new_reference=remote_override.reference,
            )

        # Success if no critical errors (skipped overrides are warnings, not failures)
        critical_errors = [e for e in errors if "skipped" not in e.lower()]
        success = len(critical_errors) == 0

        if success:
            self.logger.info(
                "Environment overrides applied successfully",
                deployment_name=self.get_name(),
                environment_name=environment.get_name(),
                warnings=len(errors),
            )
        else:
            self.logger.error(
                "Failed to apply environment overrides",
                deployment_name=self.get_name(),
                critical_error_count=len(critical_errors),
            )

        return success, errors

    def load_deploy_services(self, objects_path: str, repo_map: Optional[Dict[str, str]] = None) -> bool:
        """
        Load workspace and environment services for deployment.

        Architecture:
        - Infrastructure (providers, resources, namespaces, firewalls): Owned by workspace
        - Configuration layering: Handled by controller layer (future)
        - Environments: Deployment-level (merged if multiple files)

        Args:
            objects_path: Base directory for resolving relative file paths
            repo_map: Optional solution-level repo map for resolving @repo/... refs.
                      Merged with the config-service repo map; solution names take precedence.

        Returns:
            - bool: Success status

        Note:
            Infrastructure services (providers, resources, etc.) are accessed via
            workspace service delegation, not stored here.
        """
        # Return cached services if already loaded
        if self._workspace_service is not None and self._environment_service is not None:
            self.logger.debug("Returning cached related services")
            return True

        # Check objects_path validity
        if objects_path is None or not Path(objects_path).is_dir():
            self.logger.error("Invalid objects_path: not a directory", objects_path=objects_path)
            return False

        self._ensure_validated()
        self.logger.info("Loading related services for deployment", deployment_name=self.get_name())
        success = True

        # Build repo_map once for all @repo_name/... path resolutions in this call.
        # Merge solution-level map (caller-supplied) with config-service map.
        # Solution names take precedence so @haven/... refs resolve correctly.
        config_repo_map: Dict[str, str] = ConfigurationService.get_instance().get_remote_map()
        repo_map = {**config_repo_map, **(repo_map or {})}

        try:
            # Step 1: Load workspace service
            if not self.model or not self.model.spec.workspace:
                self.logger.error("Workspace not found in deployment")
                return False

            workspace_ref = self.model.spec.workspace
            workspace_name = workspace_ref.name
            workspace_path = self._resolve_file_path(str(workspace_ref.file), objects_path, repo_map)
            self.logger.debug(
                "Loading workspace", workspace_name=str(workspace_name), workspace_path=str(workspace_path)
            )

            # Use BaseService.load() which has caching built-in
            workspace_service: WorkspaceService = WorkspaceService.load(str(workspace_path), validate=True)

            if not workspace_service.is_validated():
                self.logger.error("Workspace validation failed", workspace_name=workspace_name)
                self._validation_errors.extend(workspace_service.get_validation_errors())
                return False

            # Step 2: Load workspace infrastructure services
            related_services, rel_success = workspace_service.load_workspace_services(
                objects_path=objects_path, repo_map=repo_map
            )

            if not rel_success or workspace_service is None or related_services is None:
                success = False
                errors = workspace_service.get_validation_errors()
                self._validation_errors.extend(errors)
                self.logger.warning(
                    "Some workspace services failed to load",
                    deployment_name=self.get_name(),
                    error_count=len(errors),
                )

            # Store workspace service (infrastructure accessed via delegation)
            # Apply tool version pins (spec.versions type:tool entries) to provisioner.version fields.
            # A patched copy is stored so the cached workspace is never mutated.
            self._workspace_service = self._apply_tool_version_pins(workspace_service, objects_path, repo_map)
            self._objects_path = objects_path
            self._load_repo_map = repo_map
            self.logger.debug(
                "Workspace loaded with infrastructure services",
                providers=len(related_services.get("providers", {})),
                resources=len(related_services.get("resources", {})),
                namespaces=len(related_services.get("namespaces", {})),
                firewalls=len(related_services.get("firewalls", {})),
            )

            # Step 3: Load and merge environment files
            # Tenant environments are prepended so deployment environments take precedence.
            tenant_env_paths: List[str] = []
            if self.model.spec.tenant:
                from strata.services.tenant_service import TenantService as _TenantService

                tenant_file = Path(objects_path) / "tenants" / f"{self.model.spec.tenant}.yaml"
                if tenant_file.exists():
                    tenant_svc = _TenantService(str(tenant_file))
                    is_valid_t, _ = tenant_svc.validate()
                    if is_valid_t and tenant_svc.model:
                        tenant_env_paths = [
                            self._resolve_file_path(env_path, objects_path, repo_map)
                            for env_path in tenant_svc.get_environments()
                        ]
                        if tenant_env_paths:
                            self.logger.debug(
                                "Prepending tenant environments",
                                tenant=self.model.spec.tenant,
                                count=len(tenant_env_paths),
                                paths=tenant_env_paths,
                            )

            env_paths = tenant_env_paths + [
                self._resolve_file_path(env_ref.file, objects_path, repo_map)
                for env_ref in self.model.spec.environments
            ]

            self.logger.debug("Loading deployment environments", count=len(env_paths), paths=env_paths)

            try:
                # If multiple environment files, merge them
                if len(env_paths) > 1:
                    self.logger.debug("Merging environment files for deployment", count=len(env_paths))
                    work_path = Path(objects_path)
                    merged_env, merge_provenance = EnvironmentService.merge_envfiles(env_paths, work_path)
                    self._merge_provenance = merge_provenance
                    # Apply version pins from spec.versions (layer 3 + 4 in resolution chain)
                    merged_env = self._apply_version_pins(merged_env, objects_path, repo_map)
                    # Create a service from the merged model
                    env_service = EnvironmentService(data=merged_env.model_dump())
                    # Validate the merged environment
                    is_valid, errors = env_service.validate()
                    if not is_valid:
                        self.logger.warning("Merged deployment environment validation failed", errors=errors)
                        success = False
                else:
                    # Single environment file - load directly
                    env_service = EnvironmentService.load(env_paths[0], validate=True)
                    # Apply version pins from spec.versions (layer 3 + 4 in resolution chain)
                    if env_service.model:
                        patched = self._apply_version_pins(env_service.model, objects_path, repo_map)
                        env_service = EnvironmentService(data=patched.model_dump())
                        env_service.validate()

                if not env_service.is_validated():
                    self.logger.warning("Deployment environment validation failed", paths=env_paths)
                    success = False
                else:
                    # Store the single environment service for this deployment
                    # Stages are pipeline metadata only, not linked to environments
                    self._environment_service = env_service
                    self.logger.debug("Environment loaded for deployment")

            except Exception as e:
                success = False
                self.logger.error(
                    "Failed to load deployment environments", paths=env_paths, error=str(e), exc_info=True
                )

        except Exception as e:
            success = False
            deployment_name = self.model.meta.name if self.model else "unknown"
            self.logger.error(
                "Failed to load related services for deployment",
                deployment_name=deployment_name,
                error_type=type(e).__name__,
                exc_info=True,
            )
            error = ServiceLoadError(
                service_name=deployment_name,
                reason=f"Failed to load related services: {str(e)}",
                cause=e,
            )
            self._structured_errors.append(error)
            self._validation_errors.append(str(error))
            return False

        if success:
            self.logger.info(
                "All related services loaded successfully for deployment",
                deployment_name=self.get_name(),
                workspace=self._workspace_service.get_name() if self._workspace_service else None,
                environment=self._environment_service.get_name() if self._environment_service else None,
            )
        else:
            self.logger.warning(
                "Some services failed to load",
                deployment_name=self.get_name(),
                error_count=len(self._validation_errors),
            )
            self._environment_service = None
            self._workspace_service = None
        return success

    def _ensure_workspace(self) -> WorkspaceService:
        """Return the workspace service, raising if not yet loaded."""
        if self._workspace_service is None:
            raise ServiceNotValidatedError(
                "DeploymentService",
                reason="Workspace service not loaded. Call load_deploy_services() first.",
            )
        return self._workspace_service

    # --- Workspace delegation (infrastructure services) ---

    def get_firewall_services(self) -> Optional[Dict[str, Any]]:
        """Get all firewall services keyed by name (delegates to workspace)."""
        return self._ensure_workspace().get_firewall_services()

    def get_firewall_service(self, firewall_name: str) -> Optional[BaseService]:
        """Get a specific firewall service by name (delegates to workspace)."""
        return self._ensure_workspace().get_firewall_service(firewall_name)

    def get_module_services(self) -> Optional[Dict[str, Any]]:
        """Get all module services keyed by name (delegates to workspace)."""
        return self._ensure_workspace().get_module_services()

    def get_module_service(self, resource_name: str, module_name: str) -> Optional[BaseService]:
        """Get a specific module service by resource and module name (delegates to workspace)."""
        return self._ensure_workspace().get_module_service(resource_name=resource_name, module_name=module_name)

    def get_namespace_services(self) -> Optional[Dict[str, Any]]:
        """Get all namespace services keyed by name (delegates to workspace)."""
        return self._ensure_workspace().get_namespace_services()

    def get_namespace_service(self, namespace_name: str) -> Optional[Union[BaseService, Dict[str, BaseService]]]:
        """Get a specific namespace service by name (delegates to workspace)."""
        return self._ensure_workspace().get_namespace_service(namespace_name)

    def get_provider_services(self) -> Optional[Dict[str, Any]]:
        """Get all provider services keyed by name (delegates to workspace)."""
        return self._ensure_workspace().get_provider_services()

    def get_provider_service(self, provider_name: str) -> Optional[BaseService]:
        """Get a specific provider service by name (delegates to workspace)."""
        return self._ensure_workspace().get_provider_service(provider_name)

    def get_resource_services(self) -> Optional[Dict[str, Any]]:
        """Get all resource services keyed by name (delegates to workspace)."""
        return self._ensure_workspace().get_resource_services()

    def get_resource_service(self, resource_name: str) -> Optional[BaseService]:
        """Get a specific resource service by name (delegates to workspace)."""
        return self._ensure_workspace().get_resource_service(resource_name)

    def _apply_version_pins(
        self,
        env_model: "EnvironmentModel",
        objects_path: str,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> "EnvironmentModel":
        """Apply version pins from ``spec.versions`` to a merged EnvironmentModel.

        Returns the (possibly modified) model.  If ``spec.versions`` is absent or
        empty, returns the model unchanged.
        """
        if not self.model or not self.model.spec.versions:
            return env_model

        from strata.services.version_service import VersionService

        _repo_map = repo_map or {}

        def resolve_fn(file_ref: str, base: str) -> str:
            return self._resolve_file_path(file_ref, base, _repo_map)

        pins = VersionService.load_and_resolve(
            version_refs=self.model.spec.versions,
            objects_path=objects_path,
            resolve_path_fn=resolve_fn,
        )

        if any(pins.values()):
            total = sum(len(v) for v in pins.values())
            self.logger.debug("Applying version pins to environment", pin_count=total)
            VersionService.apply_to_environment(env_model, pins)

        return env_model

    def _apply_tool_version_pins(
        self,
        workspace_service: "WorkspaceService",
        objects_path: str,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> "WorkspaceService":
        """Apply ``type: tool`` version pins from ``spec.versions`` to a WorkspaceService.

        Tool pins set ``provisioner.version`` on matching provisioner entries.  A new
        WorkspaceService is created from the modified data so the cached original is
        never mutated.

        Returns the original *workspace_service* unchanged when no tool pins exist.
        """
        if not self.model or not self.model.spec.versions or not workspace_service.model:
            return workspace_service

        from strata.models.version_lock_model import VersionPinTargetType
        from strata.services.version_service import VersionService

        _repo_map = repo_map or {}

        def resolve_fn(file_ref: str, base: str) -> str:
            return self._resolve_file_path(file_ref, base, _repo_map)

        pins = VersionService.load_and_resolve(
            version_refs=self.model.spec.versions,
            objects_path=objects_path,
            resolve_path_fn=resolve_fn,
        )

        tool_pins = pins.get(VersionPinTargetType.TOOL, {})
        if not tool_pins:
            return workspace_service

        self.logger.debug("Applying tool version pins to workspace provisioners", pin_count=len(tool_pins))

        # Dump to raw dict → patch → re-validate to avoid mutating the cached model
        patched_data = workspace_service.model.model_dump()
        for prov in patched_data.get("spec", {}).get("provisioners", []):
            if prov.get("name") in tool_pins:
                prov["version"] = tool_pins[prov["name"]]
                self.logger.debug("Applied tool pin", provisioner=prov["name"], version=prov["version"])

        patched_ws = WorkspaceService(data=patched_data)
        patched_ws.validate()
        return patched_ws

    def get_environment_service(self) -> Optional[EnvironmentService]:
        """
        Get the environment service for this deployment.

        Note: There is only one environment per deployment. Stages are pipeline
              metadata and have no relationship to environments.

        Returns:
            EnvironmentService instance or None if not loaded

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        if self._environment_service is None:
            raise ServiceNotValidatedError("EnvironmentService")

        return self._environment_service

    def get_merge_provenance(self) -> Optional[MergeProvenance]:
        """Return the merge provenance from the last :meth:`merge_envfiles` call.

        Returns ``None`` when the deployment uses a single environment file
        (no merge was performed).
        """
        return self._merge_provenance

    def get_workspace_service(self) -> Optional[WorkspaceService]:
        """
        Get the workspace service.

        Returns:
            WorkspaceService instance or None if not loaded

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        if self._workspace_service is None:
            raise ServiceNotValidatedError("DeploymentService")
        return self._workspace_service

    def check_digest_policy(
        self,
        work_path: str,
        config_model: Optional["ConfigurationModel"],
        verify_digests: bool = False,
    ) -> Tuple[List[str], List[str]]:
        """Check F-2 digest policy for this deployment's version pins.

        Two independent checks — either or both may be active:

        1. **Ring policy** (always active when ``ring.require_digests: true``):
           Every pin in ``spec.versions`` lock files must carry a ``resolved_sha``.
           Missing SHA → error (exit 3).

        2. **Format verification** (active only when ``verify_digests=True``):
           Pins that DO have ``resolved_sha`` must use a recognised format:
           - ``remote`` → 7–40 hex characters (git commit SHA)
           - ``image``/``helm_chart`` → ``sha256:<64 hex>`` (OCI content digest)
           Invalid format → warning (non-fatal).

        Returns:
            Tuple ``(errors, warnings)`` — both may be empty lists.
        """
        errors: List[str] = []
        warnings: List[str] = []

        if not self.model or not self.model.spec.versions:
            return errors, warnings

        try:
            from pathlib import Path as _Path

            from strata.models.version_lock_model import VersionLockModel
            from strata.services.environment_service import EnvironmentService
            from strata.services.version_service import VersionService

            _rm: Dict[str, str] = {
                **(config_model.get_remote_map() if config_model else {}),
                **(self._repo_map or {}),
            }

            def resolve_fn(f: str, b: str) -> str:
                return self._resolve_file_path(f, b, _rm)

            # ── Step 1: find the ring name from first loadable environment ──
            ring_name: Optional[str] = None
            for env_ref in self.model.spec.environments or []:
                env_path = resolve_fn(env_ref.file, work_path)
                if _Path(env_path).exists():
                    env_svc = EnvironmentService.load(env_path, validate=True)
                    if env_svc.model and env_svc.model.spec.promotion:
                        ring_name = env_svc.model.spec.promotion.ring
                        break

            # ── Step 2: check ring-level require_digests policy ──
            ring_require_digests = False
            if ring_name and config_model and config_model.spec.promotions:
                for progression in config_model.spec.promotions.progressions or []:
                    for ring in progression.rings:
                        if ring.name == ring_name and ring.require_digests:
                            ring_require_digests = True
                            break
                    if ring_require_digests:
                        break

            # Nothing to do if neither policy is active
            if not ring_require_digests and not verify_digests:
                return errors, warnings

            # ── Step 3: load version-lock pins (manifests don't carry resolved_sha) ──
            lock_pins: list = []
            for ref in self.model.spec.versions:
                try:
                    abs_path = resolve_fn(ref.file, work_path)
                    model = VersionService.load(abs_path)
                    if isinstance(model, VersionLockModel):
                        lock_pins.extend(model.spec.pins or [])
                except Exception:
                    pass

            if not lock_pins and ring_require_digests:
                # Policy requires digests but no lock files loaded
                if ring_name:
                    errors.append(
                        f"Ring '{ring_name}' requires digests (require_digests: true) but "
                        f"no version-lock file could be loaded from spec.versions. "
                        f"Run 'strata promote start' to generate the lock."
                    )
                return errors, warnings

            # ── Step 4: inspect each pin ──
            for pin in lock_pins:
                sha = pin.resolved_sha

                if ring_require_digests and not sha:
                    errors.append(
                        f"Ring '{ring_name}' requires digests (require_digests: true) but "
                        f"pin '{pin.target.name}' (type: {pin.target.type.value}) has no "
                        f"resolved_sha. Run 'strata promote start' to record digests."
                    )

                elif verify_digests and sha:
                    if not VersionService.validate_sha_format(pin.target.type, sha):
                        warnings.append(
                            f"Pin '{pin.target.name}' (type: {pin.target.type.value}) has "
                            f"resolved_sha '{sha}' with unrecognised format — expected "
                            f"git SHA (hex) for remote pins or 'sha256:<64hex>' for "
                            f"image/helm_chart pins."
                        )

        except Exception:
            pass  # digest check is never fatal to the validate pipeline

        return errors, warnings

    def check_require_lock_mode(
        self,
        work_path: Path,
        config_model: Optional["ConfigurationModel"],
        flag: bool = False,
    ) -> Optional[str]:
        """Check strict lock mode enforcement for build/deploy.

        Returns an error message string if the lock file is missing and
        enforcement is active, or ``None`` if the check passes (or is not applicable).

        Enforcement is active when either:
        - ``flag`` is ``True`` (``--require-lock`` CLI flag was passed), or
        - the resolved ring declares ``require_lock: true`` in the progression config.

        Lock file discovery (checked in order, first found wins):
        1. New-style: ``{versions_path}/{ring}.lock.yaml`` (from promotion strategy)
        2. Old-style: ``versions/{ring}.yaml``
        """
        env_svc = self._environment_service
        if env_svc is None or env_svc.model is None:
            return None  # environment not loaded — skip silently

        spec = env_svc.model.spec if env_svc.model else None
        promotion = spec.promotion if spec else None
        if not promotion:
            return None  # no promotion config on this environment — skip silently

        ring_name: str = promotion.ring
        strategy_name: str = promotion.strategy

        # Check ring-level require_lock from configuration progressions
        ring_require_lock = False
        versions_path_raw: Optional[str] = None
        if config_model and config_model.spec and config_model.spec.promotions:
            for progression in config_model.spec.promotions.progressions or []:
                for ring in progression.rings:
                    if ring.name == ring_name and ring.require_lock:
                        ring_require_lock = True
                        break
                if ring_require_lock:
                    break
            # Also pick up versions_path from the matching strategy
            for strategy in config_model.spec.promotions.strategies or []:
                if strategy.name == strategy_name and strategy.versions_path:
                    versions_path_raw = strategy.versions_path
                    break

        if not flag and not ring_require_lock:
            return None  # enforcement not active for this ring

        # Try new-style lock path first: {versions_path}/{ring}.lock.yaml
        if versions_path_raw:
            vp_raw = (
                versions_path_raw.lstrip("@").split("/", 1)[-1]
                if versions_path_raw.startswith("@")
                else versions_path_raw
            )
            new_lock_path = Path(work_path) / vp_raw / f"{ring_name}.lock.yaml"
            if new_lock_path.exists():
                return None  # lock exists — check passes

        # Fallback: old-style lock path versions/{ring}.yaml
        old_lock_path = Path(work_path) / "versions" / f"{ring_name}.yaml"
        if old_lock_path.exists():
            return None  # old-style lock exists — check passes

        # Neither exists — fail
        hint_path = (
            f"{versions_path_raw.rstrip('/')}/{ring_name}.lock.yaml"
            if versions_path_raw
            else f"versions/{ring_name}.yaml"
        )
        return (
            f"Ring '{ring_name}' has no lock file ({hint_path}). "
            f"Run 'strata promote {ring_name} <version-file> --promotion {strategy_name}' first, "
            "or remove --require-lock."
        )

    def validate_related_services(self) -> Tuple[bool, List[str]]:
        """
        Validate cross-service references after all services are loaded.

        This performs validations that require access to loaded workspace and environment
        services. Should be called after load_related_services() completes successfully.

        Validates:
        - Environment overrides reference existing workspace resources/providers/modules
        - Stage provisioner/topology references are valid
        - Resource dependencies (depends_on) reference existing resources
        - Resource firewall references are valid
        - Resource namespace references are valid
        - Resource module references are valid
        - Resource provider references are valid

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        if self._workspace_service is None or self._environment_service is None:
            raise ServiceNotValidatedError(
                "DeploymentService",
                reason="Call load_deploy_services() before validate_related_services()",
            )

        errors = []
        workspace = self.get_workspace_service()
        environment = self.get_environment_service()

        # Can't validate without workspace
        if not workspace:
            errors.append("Workspace service not loaded, cannot validate cross-references")
            return False, errors

        # Get workspace infrastructure for validation
        firewalls = self.get_firewall_services() or {}
        providers = self.get_provider_services() or {}
        resources = self.get_resource_services() or {}
        # namespaces = self.get_namespace_services() or {}
        # modules = self.get_module_services() or {}

        # Validation 1: Environment overrides reference valid workspace entities
        if environment and environment.has_overrides():
            # Check resource overrides
            for resource_name in environment.get_overridden_resource_names():
                if resource_name not in resources:
                    errors.append(f"Environment overrides non-existent resource '{resource_name}'")

            # Check provider overrides
            for provider_name in environment.get_overridden_provider_names():
                if provider_name not in providers:
                    errors.append(f"Environment overrides non-existent provider '{provider_name}'")

            # Check module overrides (modules are within resources or namespaces)
            for module_key in environment.get_overridden_module_keys():
                # module_key format: (module_name, resource_or_none, namespace_or_none, slot_type_or_none)
                if isinstance(module_key, tuple) and len(module_key) >= 2:
                    override_resource = module_key[1]
                    if override_resource and override_resource not in resources:
                        errors.append(f"Environment overrides module in non-existent resource '{override_resource}'")

        # Validation 2: Stage provisioner/topology references
        if self.model and self.model.spec and self.model.spec.stages:
            provisioner_names: set = set()
            topology_names: set = set()
            if workspace.model and workspace.model.spec:
                provisioner_names = {p.name for p in (workspace.model.spec.provisioners or [])}
                topology_names = {t.name for t in (workspace.model.spec.topology or [])}

            for stage in self.model.spec.stages:
                if stage.provisioner and stage.provisioner not in provisioner_names:
                    errors.append(
                        f"Stage '{stage.name}' references undefined provisioner '{stage.provisioner}'. "
                        f"Available provisioners: {', '.join(sorted(provisioner_names)) or '(none)'}"
                    )

                if stage.topology and stage.topology not in topology_names:
                    errors.append(
                        f"Stage '{stage.name}' references undefined topology '{stage.topology}'. "
                        f"Available topologies: {', '.join(sorted(topology_names)) or '(none)'}"
                    )

        # Validation 3: Resource cross-references (workspace-level dependencies)
        # Check that workspace resource dependencies reference existing resources
        if workspace.model and workspace.model.spec.resources:
            for workspace_resource in workspace.model.spec.resources:
                resource_name = workspace_resource.name

                # Validate depends_on references
                if workspace_resource.depends_on:
                    for dep_resource in workspace_resource.depends_on:
                        if dep_resource not in resources:
                            errors.append(
                                f"Resource '{resource_name}' depends on non-existent resource '{dep_resource}'"
                            )

                # Validate firewall references
                if workspace_resource.firewalls:
                    for firewall_ref in workspace_resource.firewalls:
                        if firewall_ref not in firewalls:
                            errors.append(
                                f"Resource '{resource_name}' references non-existent firewall '{firewall_ref}'"
                            )

        # Validation N: Provider region/type check against configuration.spec.providers
        # ProviderService._validate_dynamic() has this logic but is never called during
        # normal load (load() runs Phase 1 only). Run it here so region constraints from
        # configuration are enforced — including after a file override has been applied.
        config_svc = ConfigurationService.get_instance()
        config_model = config_svc.model if config_svc else None
        if config_model and providers:
            for prov_name, prov_service in providers.items():
                prov_valid, prov_errors = prov_service._validate_dynamic(configuration_model=config_model)
                if not prov_valid:
                    errors.extend([f"Provider '{prov_name}': {e}" for e in prov_errors])

        success = len(errors) == 0

        if success:
            self.logger.info("Related services validation passed", deployment_name=self.get_name())
        else:
            self.logger.warning(
                "Related services validation failed",
                deployment_name=self.get_name(),
                error_count=len(errors),
            )

        return success, errors
