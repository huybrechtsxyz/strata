"""
Loads workspace-local policy drop-ins from .strata/policies/*.py.

Each file must define a top-level ``register()`` function that calls
``PolicyEngine.register_type(type_str, cls)`` to register custom
policy types with the platform.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from strata.logger import get_logger
from strata.utils.config import get_policies_dir

logger = get_logger(__name__)


def load_workspace_policies(work_path: Path) -> int:
    """
    Scan ``.strata/policies/*.py`` and call ``register()`` in each.

    Files whose names start with ``_`` are skipped (e.g. ``__init__.py``).
    Errors in individual files are logged as warnings and never propagate —
    a broken drop-in must not crash the CLI.

    Args:
        work_path: Root of the workspace (the directory that contains ``.strata/``).

    Returns:
        Number of files successfully loaded (i.e. ``register()`` was called
        without raising).
    """
    policies_dir = get_policies_dir(work_path)
    if not policies_dir.is_dir():
        return 0

    loaded = 0
    for py_file in sorted(policies_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module_name = f"_strata_workspace_policy_{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                logger.warning("Could not create module spec for policy drop-in", file=str(py_file))
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            if callable(getattr(module, "register", None)):
                module.register()
                logger.debug("Loaded workspace policy drop-in", file=py_file.name)
                loaded += 1
            else:
                logger.warning(
                    "Policy drop-in has no register() function — skipped",
                    file=py_file.name,
                )
        except Exception as exc:
            logger.warning(
                "Failed to load workspace policy drop-in",
                file=py_file.name,
                error=str(exc),
            )
            # Remove from sys.modules if partially registered
            sys.modules.pop(module_name, None)

    return loaded
