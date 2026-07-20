"""Load workspace-local SBOM collector plugins from .strata/collectors.yaml."""

import importlib.util
import sys
from pathlib import Path
from typing import List, Optional

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.exceptions.base_exception import PlatformError
from strata.logger import get_logger
from strata.utils.config import get_collectors_path, get_lockfile_parsers_dir

logger = get_logger(__name__)


_SUPPORTED_TYPES = {"collector", "lockfile_parser"}


class CollectorPluginLoader:
    """Load collector plugins declared in ``.strata/collectors.yaml``.

    Follows the same pattern as ``IntegrationFactory``: importlib-based class
    loading, structured errors, debug logging.

    Supports two plugin types:

    ``type: collector``
        A ``BaseSbomCollector`` subclass.  The class is instantiated and
        appended after the built-in collectors.  ``class`` is required.

    ``type: lockfile_parser``
        A ``LockfileParser`` subclass.  The plugin module is imported for its
        **side effects** — ``LockfileParser.__init_subclass__`` fires on class
        body execution and auto-registers every concrete parser class into
        ``DEFAULT_REGISTRY``.  No explicit ``register()`` call is needed and
        the ``class`` key is optional (importing the module is sufficient).

    Config schema::

        collectors:
          - name: my-collector
            path: .strata/plugins/my_collector.py   # relative to work_path
            class: MyCollector
            type: collector

          - name: cargo-parser
            path: .strata/plugins/cargo_parser.py
            type: lockfile_parser   # class optional — __init_subclass__ handles it

    ``path`` is resolved relative to *work_path*.  ``module`` (dotted import
    path) may be used instead of ``path`` for installed packages.
    """

    @staticmethod
    def load(work_path: Path) -> List[BaseSbomCollector]:
        """Load ``.strata/collectors.yaml`` and return instantiated extra collectors.

        Side effect for ``type: lockfile_parser`` entries: importing the plugin
        module causes ``LockfileParser.__init_subclass__`` to fire, which
        auto-registers every concrete parser class found in that module into
        ``DEFAULT_REGISTRY``.

        Returns an empty list when the config file does not exist or contains no
        ``type: collector`` entries.  Never raises on missing config file.

        Raises:
            PlatformError: If the config file exists but cannot be parsed, a
                declared file path is missing, or a class cannot be loaded.
        """
        config_path = get_collectors_path(work_path)
        if not config_path.exists():
            # No YAML config, but still auto-discover from folder
            CollectorPluginLoader._discover_lockfile_parsers(work_path)
            return []

        try:
            import yaml

            with config_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception as exc:
            raise PlatformError(
                message=f"Failed to parse {config_path}: {exc}",
                error_code="COLLECTOR_PLUGIN_CONFIG_ERROR",
            ) from exc

        if not isinstance(data, dict):
            raise PlatformError(
                message=f"{config_path} must be a YAML mapping",
                error_code="COLLECTOR_PLUGIN_CONFIG_ERROR",
            )

        entries = data.get("collectors") or []
        if not isinstance(entries, list):
            raise PlatformError(
                message=f"{config_path}: 'collectors' must be a list",
                error_code="COLLECTOR_PLUGIN_CONFIG_ERROR",
            )

        extra: List[BaseSbomCollector] = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            name: str = str(entry.get("name", "<unnamed>"))
            plugin_type: str = str(entry.get("type", ""))
            path_str: Optional[str] = entry.get("path")
            module_str: Optional[str] = entry.get("module")
            class_name: Optional[str] = entry.get("class")

            if plugin_type not in _SUPPORTED_TYPES:
                raise PlatformError(
                    message=(
                        f"Collector plugin '{name}' has unknown type '{plugin_type}'. "
                        f"Supported types: {', '.join(sorted(_SUPPORTED_TYPES))}"
                    ),
                    error_code="COLLECTOR_PLUGIN_CONFIG_ERROR",
                )

            if plugin_type == "lockfile_parser":
                # Import the module — __init_subclass__ auto-registers parsers
                # into DEFAULT_REGISTRY as a side effect.  class name is optional.
                CollectorPluginLoader._import_module(
                    path=str(work_path / path_str) if path_str else None,
                    module_path=module_str,
                    plugin_name=name,
                )
                logger.debug("Loaded lockfile_parser plugin module", plugin=name)
                continue

            # type == "collector" — load and instantiate
            if not class_name:
                raise PlatformError(
                    message=f"Collector plugin '{name}': 'class' is required for type=collector",
                    error_code="COLLECTOR_PLUGIN_CONFIG_ERROR",
                )

            cls = CollectorPluginLoader._load_class(
                path=str(work_path / path_str) if path_str else None,
                module_path=module_str,
                class_name=class_name,
                plugin_name=name,
            )

            if not (isinstance(cls, type) and issubclass(cls, BaseSbomCollector)):
                raise PlatformError(
                    message=(
                        f"Collector plugin '{name}': class '{class_name}' must be a subclass of BaseSbomCollector"
                    ),
                    error_code="COLLECTOR_PLUGIN_LOAD_ERROR",
                )

            instance: BaseSbomCollector = cls()
            extra.append(instance)
            logger.debug("Loaded collector plugin", plugin=name, cls=class_name)

        # Auto-discover lockfile parsers from .strata/lockfile_parsers/*.py
        CollectorPluginLoader._discover_lockfile_parsers(work_path)

        return extra

    @staticmethod
    def _discover_lockfile_parsers(work_path: Path) -> None:
        """Auto-import all ``.py`` files from ``.strata/lockfile_parsers/``.

        Each file is imported for its side effects — any ``LockfileParser``
        subclass defined in the module is auto-registered into
        ``DEFAULT_REGISTRY`` via ``__init_subclass__``.

        Files starting with ``_`` are skipped.  Import errors are logged as
        warnings but do not halt processing.
        """
        parsers_dir = get_lockfile_parsers_dir(work_path)
        if not parsers_dir.is_dir():
            return

        for py_file in sorted(parsers_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"strata_lockfile_parser_{py_file.stem}"
            if module_name in sys.modules:
                continue  # already imported (avoid double-registration)
            try:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    logger.warning("Failed to create import spec", file=str(py_file))
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                logger.debug("Auto-discovered lockfile parser", file=py_file.name)
            except Exception as exc:
                logger.warning(
                    "Failed to load lockfile parser plugin",
                    file=str(py_file),
                    error=str(exc),
                )

    @staticmethod
    def _import_module(
        path: Optional[str],
        module_path: Optional[str],
        plugin_name: str,
    ) -> None:
        """Import a module by file path or dotted module path (side-effects only).

        Raises:
            PlatformError: If the file is missing or the module cannot be imported.
        """
        if path is not None:
            abs_path = Path(path)
            if not abs_path.exists():
                raise PlatformError(
                    message=f"Lockfile parser plugin '{plugin_name}': file not found: {path}",
                    error_code="COLLECTOR_PLUGIN_LOAD_ERROR",
                )
            try:
                spec = importlib.util.spec_from_file_location(f"strata_plugin_{plugin_name}", abs_path)
                if spec is None or spec.loader is None:
                    raise ImportError("spec_from_file_location returned None")
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
            except Exception as exc:
                raise PlatformError(
                    message=(f"Lockfile parser plugin '{plugin_name}': failed to import {path}: {exc}"),
                    error_code="COLLECTOR_PLUGIN_LOAD_ERROR",
                ) from exc
        elif module_path is not None:
            try:
                importlib.import_module(module_path)
            except ImportError as exc:
                raise PlatformError(
                    message=(f"Lockfile parser plugin '{plugin_name}': cannot import '{module_path}': {exc}"),
                    error_code="COLLECTOR_PLUGIN_LOAD_ERROR",
                ) from exc
        else:
            raise PlatformError(
                message=(f"Lockfile parser plugin '{plugin_name}': one of 'path' or 'module' is required"),
                error_code="COLLECTOR_PLUGIN_CONFIG_ERROR",
            )

    @staticmethod
    def _load_class(
        path: Optional[str],
        module_path: Optional[str],
        class_name: str,
        plugin_name: str,
    ) -> type:
        """Load a class from a file path or dotted module path.

        Raises:
            PlatformError: If the file is missing, the module cannot be
                imported, or the class is not found.
        """
        mod = None
        if path is not None:
            abs_path = Path(path)
            if not abs_path.exists():
                raise PlatformError(
                    message=f"Collector plugin '{plugin_name}': file not found: {path}",
                    error_code="COLLECTOR_PLUGIN_LOAD_ERROR",
                )
            try:
                spec = importlib.util.spec_from_file_location(f"strata_plugin_{plugin_name}", abs_path)
                if spec is None or spec.loader is None:
                    raise ImportError("spec_from_file_location returned None")
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
            except Exception as exc:
                raise PlatformError(
                    message=(f"Collector plugin '{plugin_name}': failed to import {path}: {exc}"),
                    error_code="COLLECTOR_PLUGIN_LOAD_ERROR",
                ) from exc
        elif module_path is not None:
            try:
                mod = importlib.import_module(module_path)
            except ImportError as exc:
                raise PlatformError(
                    message=(f"Collector plugin '{plugin_name}': cannot import '{module_path}': {exc}"),
                    error_code="COLLECTOR_PLUGIN_LOAD_ERROR",
                ) from exc
        else:
            raise PlatformError(
                message=(f"Collector plugin '{plugin_name}': one of 'path' or 'module' is required"),
                error_code="COLLECTOR_PLUGIN_CONFIG_ERROR",
            )

        try:
            return getattr(mod, class_name)  # type: ignore[return-value]
        except AttributeError as exc:
            raise PlatformError(
                message=(f"Collector plugin '{plugin_name}': class '{class_name}' not found in module"),
                error_code="COLLECTOR_PLUGIN_LOAD_ERROR",
            ) from exc
