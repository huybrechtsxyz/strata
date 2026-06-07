#!/usr/bin/env python3
"""Service for loading and validating module configurations."""

from typing import List, Optional, Tuple

from strata.models.configuration_model import ConfigurationModel
from strata.models.module_model import ModuleModel
from strata.services.base_service import BaseService


class ModuleService(BaseService["ModuleModel"]):
    """Service for handling module configurations and source fetching."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the ModuleService."""
        super().__init__(path=path, data=data)
        self.model = None

    def _get_model_class(self):
        """Return the ModuleModel class for validation."""
        return ModuleModel

    def _validate_self(self) -> Tuple[bool, List[str]]:
        """
        Phase 1.5: Self-consistency checks — no external dependencies required.

        Validates intra-document constraints that Pydantic model validators cannot check:
        - services[].depends_on entries must reference real service names in this module
        - services[].environment var/secret/feature refs must exist in spec.references

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        if not self.model:
            return True, []

        errors: List[str] = []
        spec = self.model.spec
        services = spec.services
        if not services:
            return True, []

        module_name = self.model.meta.name
        service_names = {s.name for s in services}

        # Build reference sets for cross-validation
        declared_variables = set(spec.references.variables or []) if spec.references else set()
        declared_secrets = set(spec.references.secrets or []) if spec.references else set()
        declared_features = set(spec.references.features or []) if spec.references else set()

        for service in services:
            # Validate depends_on: intra-module entries must exist locally;
            # cross-module refs (@module/service) are validated at build time.
            for dep in service.depends_on or []:
                if dep.startswith("@"):
                    # Cross-module reference — validate syntax only
                    ref = dep[1:]  # strip leading @
                    parts = ref.split("/", 1)
                    mod_part = parts[0]
                    svc_part = parts[1] if len(parts) > 1 else None
                    if not mod_part:
                        errors.append(
                            f"Module '{module_name}', service '{service.name}': "
                            f"depends_on '{dep}' has invalid syntax — "
                            f"expected @module or @module/service."
                        )
                    elif svc_part is not None and not svc_part:
                        errors.append(
                            f"Module '{module_name}', service '{service.name}': "
                            f"depends_on '{dep}' has empty service name after '/'."
                        )
                    # Valid syntax — will be resolved and validated by ComposeBuilder
                elif dep not in service_names:
                    errors.append(
                        f"Module '{module_name}', service '{service.name}': "
                        f"depends_on '{dep}' is not a service defined in this module. "
                        f"Available services: {sorted(service_names)}."
                    )

            # Validate environment refs exist in spec.references
            for env in service.environment or []:
                if env.var is not None and env.var not in declared_variables:
                    errors.append(
                        f"Module '{module_name}', service '{service.name}', "
                        f"env '{env.key}': var '{env.var}' is not declared in spec.references.variables."
                    )
                if env.secret is not None and env.secret not in declared_secrets:
                    errors.append(
                        f"Module '{module_name}', service '{service.name}', "
                        f"env '{env.key}': secret '{env.secret}' is not declared in spec.references.secrets."
                    )
                if env.feature is not None and env.feature not in declared_features:
                    errors.append(
                        f"Module '{module_name}', service '{service.name}', "
                        f"env '{env.key}': feature '{env.feature}' is not declared in spec.references.features."
                    )

        return len(errors) == 0, errors

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        Module cross-references (provider, workspace topology, etc.) would go here.
        Currently no module-level cross-service checks are needed.

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        return True, []
