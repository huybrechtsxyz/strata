"""Factory for creating deployer instances by provisioner type.

Centralises deployer selection, eliminating duplicated if/elif chains across
command files.  Supports built-in deployers via a lazy-import map and
user plugins discovered from ``.strata/provisioners/*.py``.
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Dict, List, Optional, Tuple, Type

from strata.deployers.base_deployer import BaseDeployer
from strata.logger import get_logger
from strata.models.deployment_model import DeploymentStageModel

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController
    from strata.services.configuration_service import ConfigurationService
    from strata.services.deployment_service import DeploymentService
    from strata.utils.resolved_values import ResolvedValues

logger = get_logger(__name__)


class DeployerFactory:
    """Central registry for deployer types — built-in and user plugins.

    Built-in deployers are registered via ``_BUILTIN_MAP`` (lazy import on
    first use).  User plugins are discovered by ``load_plugins()`` from
    ``.strata/provisioners/*.py`` files in the workspace.

    Usage::

        deployer = DeployerFactory.create(
            provisioner_type="terraform",
            stage=stage,
            deployment_service=svc,
            ...
        )
    """

    # Lazy-import map: type string → (module_path, class_name)
    _BUILTIN_MAP: ClassVar[Dict[str, Tuple[str, str]]] = {
        "terraform": ("strata.deployers.terraform_deployer", "TerraformDeployer"),
        "ansible": ("strata.deployers.ansible_deployer", "AnsibleDeployer"),
        "compose": ("strata.deployers.compose_deployer", "ComposeDeployer"),
        "helm": ("strata.deployers.helm_deployer", "HelmDeployer"),
        "script": ("strata.deployers.script_deployer", "ScriptDeployer"),
        "argocd": ("strata.deployers.sync_deployer", "ArgocdDeployer"),
        "flux": ("strata.deployers.sync_deployer", "FluxDeployer"),
    }

    # Runtime registry populated by load_plugins() and register()
    _registry: ClassVar[Dict[str, Type[BaseDeployer]]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    @classmethod
    def register(cls, name: str, deployer_class: Type[BaseDeployer]) -> None:
        """Register a deployer class under the given provisioner name."""
        cls._registry[name] = deployer_class
        logger.debug("Deployer type registered", name=name, cls=deployer_class.__name__)

    @classmethod
    def reset(cls) -> None:
        """Clear runtime registry (test helper)."""
        cls._registry.clear()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @classmethod
    def get_known_types(cls) -> List[str]:
        """Return all registered + built-in type names, sorted."""
        return sorted(set(cls._BUILTIN_MAP.keys()) | set(cls._registry.keys()))

    @classmethod
    def is_known_type(cls, type_str: str) -> bool:
        """Return True if *type_str* is a known built-in or registered plugin."""
        return type_str in cls._BUILTIN_MAP or type_str in cls._registry

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    @classmethod
    def resolve_type(
        cls,
        stage: DeploymentStageModel,
        deployment_service: "DeploymentService",
    ) -> Tuple[Optional[str], List[str]]:
        """Resolve the provisioner type string for a deployment stage.

        Resolution (mutually exclusive — exactly one required at runtime):
        - ``stage.provisioner`` → look up named provisioner entry in workspace
        - ``stage.topology``   → look up topology by name → derive provisioner

        Returns:
            (provisioner_type_string_or_None, error_messages)
        """
        errors: List[str] = []

        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            errors.append(f"Stage '{stage.name}': workspace service not loaded.")
            return None, errors

        spec = workspace_service.model.spec
        _provisioners = spec.provisioners or []
        _available = [str(p.name) for p in _provisioners]

        resolved_type: Optional[str] = None
        _iac = None

        if stage.provisioner:
            _iac = next((p for p in _provisioners if p.name == stage.provisioner), None)
            if _iac is not None:
                resolved_type = str(_iac.provisioner)

        elif stage.topology:
            _topologies = spec.topology or []
            topo = next((t for t in _topologies if str(t.name) == stage.topology), None)
            if topo is None:
                _topo_names = [str(t.name) for t in _topologies]
                errors.append(
                    f"Stage '{stage.name}': topology '{stage.topology}' not found in workspace. "
                    f"Available: {_topo_names if _topo_names else ['(none defined)']}"
                )
                return None, errors
            _iac = next((p for p in _provisioners if p.name == topo.provisioner), None)
            if _iac is None:
                errors.append(
                    f"Stage '{stage.name}': topology '{stage.topology}' references provisioner "
                    f"'{topo.provisioner}' which is not defined in the workspace."
                )
                return None, errors
            resolved_type = str(_iac.provisioner)

        if resolved_type is None:
            if not stage.provisioner and not stage.topology:
                errors.append(
                    f"Stage '{stage.name}': either 'provisioner' or 'topology' is required — "
                    "name a workspace provisioner entry directly, or name a workspace topology "
                    "to derive the provisioner from the topology definition."
                )
            elif stage.provisioner and _iac is None:
                errors.append(
                    f"Stage '{stage.name}': provisioner '{stage.provisioner}' not found in workspace. "
                    f"Available: {_available if _available else ['(none defined)']}"
                )
            return None, errors

        if not cls.is_known_type(resolved_type):
            errors.append(
                f"Stage '{stage.name}': provisioner has unsupported type "
                f"'{resolved_type}'. Supported: {', '.join(cls.get_known_types())}."
            )
            return None, errors

        return resolved_type, errors

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        provisioner_type: str,
        *,
        stage: DeploymentStageModel,
        deployment_service: "DeploymentService",
        configuration_service: "ConfigurationService",
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
        resolved_values: Optional["ResolvedValues"] = None,
        solution_controller: Optional["SolutionController"] = None,
    ) -> BaseDeployer:
        """Create a deployer instance for the given provisioner type.

        Resolution order:
        1. ``_registry`` (runtime-registered, includes user plugins)
        2. ``_BUILTIN_MAP`` (lazy import)

        Raises:
            ValueError: If *provisioner_type* is not known.
        """
        deployer_class = cls._resolve_class(provisioner_type)

        return deployer_class(
            stage=stage,
            deployment_service=deployment_service,
            configuration_service=configuration_service,
            build_path=build_path,
            work_path=work_path,
            verbose=verbose,
            force=force,
            resolved_values=resolved_values,
            solution_controller=solution_controller,
        )

    @classmethod
    def _resolve_class(cls, provisioner_type: str) -> Type[BaseDeployer]:
        """Resolve a deployer class by type string.

        Checks runtime registry first, then lazy-imports from built-in map.

        Raises:
            ValueError: If *provisioner_type* is not known.
        """
        # 1. Runtime registry (plugins + manually registered)
        if provisioner_type in cls._registry:
            return cls._registry[provisioner_type]

        # 2. Built-in map (lazy import)
        if provisioner_type in cls._BUILTIN_MAP:
            module_path, class_name = cls._BUILTIN_MAP[provisioner_type]
            module = importlib.import_module(module_path)
            deployer_class = getattr(module, class_name)
            # Cache in registry for subsequent calls
            cls._registry[provisioner_type] = deployer_class
            return deployer_class

        raise ValueError(
            f"Unknown deployer type: '{provisioner_type}'. Known types: {', '.join(cls.get_known_types())}"
        )

    # ------------------------------------------------------------------
    # Plugin discovery
    # ------------------------------------------------------------------

    @classmethod
    def load_plugins(cls, work_path: Path) -> None:
        """Discover and register user plugins from ``.strata/provisioners/*.py``.

        Each ``.py`` file is imported and scanned for ``BaseDeployer``
        subclasses.  Each discovered subclass is registered under the name
        returned by its ``get_deployer_name()`` class or instance method.

        Files prefixed with ``_`` are skipped.  Import errors are logged
        as warnings but do not prevent other plugins from loading.
        """
        plugins_dir = work_path / ".strata" / "provisioners"
        if not plugins_dir.is_dir():
            return

        for py_file in sorted(plugins_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            module_name = f"strata_provisioner_plugin_{py_file.stem}"

            # Skip if already loaded
            if module_name in sys.modules:
                logger.debug("Plugin module already loaded", module=module_name)
                continue

            try:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    logger.warning("Could not create module spec", file=str(py_file))
                    continue

                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)  # type: ignore[union-attr]

                # Find all BaseDeployer subclasses in the module
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, BaseDeployer) and attr is not BaseDeployer:
                        # Instantiate temporarily to get the canonical name,
                        # or use class-level method if available
                        try:
                            name = attr.get_deployer_name(attr)  # type: ignore[arg-type]
                        except TypeError:
                            logger.warning(
                                "Could not determine deployer name",
                                cls=attr_name,
                                file=str(py_file),
                            )
                            continue

                        cls.register(name, attr)
                        logger.info(
                            "Plugin provisioner loaded",
                            name=name,
                            cls=attr_name,
                            file=str(py_file),
                        )

            except Exception:
                logger.warning(
                    "Failed to load provisioner plugin",
                    file=str(py_file),
                    exc_info=True,
                )
