#!/usr/bin/env python3
"""Service for loading and validating tenant configurations."""

from typing import List, Optional, Tuple

from strata.models.configuration_model import ConfigurationModel
from strata.models.tenant_model import TenantModel
from strata.services.base_service import BaseService


class TenantService(BaseService["TenantModel"]):
    """Service for handling tenant configuration files (kind: tenant)."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the TenantService."""
        super().__init__(path=path, data=data)
        self.model = None

    def _get_model_class(self):
        """Return the TenantModel class for validation."""
        return TenantModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """Phase 2: Dynamic validation against configuration and filesystem.

        Validates:
        - spec.code matches meta.name (single source of truth)
        - spec.zones entries exist in configuration.spec.zones
        - spec.environments file paths resolve on disk

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation.
            work_path: Optional working directory for resolving environment file paths.

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        errors: List[str] = []

        if self.model is None:
            return False, ["tenant model is not initialized"]

        # Validate spec.code matches meta.name
        if self.model.spec.code != self.model.meta.name:
            errors.append(
                f"tenant spec.code '{self.model.spec.code}' must match meta.name '{self.model.meta.name}'. "
                "Use the same value for both fields."
            )

        # Validate spec.zones against configuration zones
        if configuration_model and configuration_model.spec.zones:
            config_zone_names = {z.name for z in configuration_model.spec.zones}
            for zone in self.model.spec.zones:
                if zone not in config_zone_names:
                    errors.append(
                        f"Tenant '{self.model.meta.name}' references zone '{zone}' "
                        f"which is not defined in configuration.spec.zones. "
                        f"Available zones: {sorted(config_zone_names)}"
                    )
        elif self.model.spec.zones and configuration_model and not configuration_model.spec.zones:
            errors.append(
                f"Tenant '{self.model.meta.name}' specifies zones {self.model.spec.zones} "
                "but configuration.spec.zones is not defined."
            )

        # Validate environment file paths exist on disk
        if work_path and self.model.spec.environments:
            config_repo_map = configuration_model.get_remote_map() if configuration_model else {}
            repo_map = {**config_repo_map, **(self._repo_map or {})}
            file_refs = [(f"Environment[{i}]", env_path) for i, env_path in enumerate(self.model.spec.environments)]
            errors.extend(self._validate_file_refs(work_path, repo_map, file_refs))

        return len(errors) == 0, errors

    # --- Accessors ---

    def get_code(self) -> str:
        """Return the tenant's short code identifier."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.code

    def get_display_name(self) -> str:
        """Return the tenant's human-readable display name."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.name

    def get_zones(self) -> List[str]:
        """Return the list of zone names this tenant is allowed to deploy into."""
        self._ensure_validated()
        assert self.model is not None
        return list(self.model.spec.zones)

    def get_environments(self) -> List[str]:
        """Return the ordered list of environment file paths for this tenant."""
        self._ensure_validated()
        assert self.model is not None
        return list(self.model.spec.environments or [])

    def get_configuration(self) -> dict:
        """Return the custom key/value configuration block (empty dict if unset)."""
        self._ensure_validated()
        assert self.model is not None
        return dict(self.model.spec.configuration or {})
