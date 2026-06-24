#!/usr/bin/env python3
"""Resolves a --template argument to a scaffold folder and optional manifest."""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from pydantic import ValidationError

from strata.exceptions import ModelValidationError, PlatformError
from strata.logger import get_logger
from strata.models.scaffold_template_model import ScaffoldTemplateModel
from strata.utils.system import get_pkg_templates_path

logger = get_logger(__name__)

_EXAMPLES_DIR = "examples"
_MANIFEST_FILE = "template.yaml"
_SCAFFOLD_DIR = "scaffold"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_builtin_templates() -> List[str]:
    """Return the names of all built-in templates shipped with the package."""
    examples_dir = get_pkg_templates_path() / _EXAMPLES_DIR
    if not examples_dir.is_dir():
        return []
    return sorted(p.name for p in examples_dir.iterdir() if p.is_dir())


def list_scaffold_templates(work_path: Optional[Path] = None) -> List[Dict[str, str]]:
    """Return scaffold templates with metadata from built-in and workspace sources.

    Discovers template bundles (directories with an optional ``template.yaml``
    manifest) from:

    1. Built-in: ``strata/templates/examples/``
    2. Workspace: ``<work_path>/.strata/templates/`` (directories only)

    Workspace templates take precedence over built-in ones with the same name.

    Returns:
        List of dicts with keys: ``name``, ``description``, ``source``
        (``"builtin"`` or ``"workspace"``).  Sorted by name.
    """
    templates: Dict[str, Dict[str, str]] = {}

    # Built-in templates
    examples_dir = get_pkg_templates_path() / _EXAMPLES_DIR
    if examples_dir.is_dir():
        for p in examples_dir.iterdir():
            if p.is_dir():
                manifest = _load_manifest_safe(p)
                templates[p.name] = {
                    "name": p.name,
                    "description": manifest.description if manifest else "",
                    "source": "builtin",
                }

    # Workspace-local templates (override built-in)
    if work_path is not None:
        ws_dir = work_path / ".strata" / "templates"
        if ws_dir.is_dir():
            for p in ws_dir.iterdir():
                if p.is_dir() and (p / _SCAFFOLD_DIR).is_dir():
                    manifest = _load_manifest_safe(p)
                    templates[p.name] = {
                        "name": p.name,
                        "description": manifest.description if manifest else "(workspace template)",
                        "source": "workspace",
                    }

    return sorted(templates.values(), key=lambda t: t["name"])


def resolve_template(template_arg: str) -> Tuple[Path, Optional[ScaffoldTemplateModel]]:
    """Resolve a ``--template`` argument to *(scaffold_folder, manifest)*.

    Resolution order:

    1. ``git+...`` prefix → raises :class:`PlatformError` (reserved, not yet supported).
    2. Argument is an existing directory on disk → local template folder.
    3. Argument is a short name (no path separators) → look up in the
       built-in ``strata/templates/examples/{name}/`` folder.
    4. Otherwise → :class:`PlatformError` listing available built-ins.

    Args:
        template_arg: Value passed to ``--template``.

    Returns:
        A tuple of *(scaffold_dir, manifest_or_None)*.
        *scaffold_dir* is the ``scaffold/`` sub-folder inside the template folder.
        *manifest_or_None* is the parsed ``template.yaml`` manifest, or ``None``
        if the template folder has no manifest.

    Raises:
        PlatformError: If the template cannot be resolved or the manifest is invalid.
    """
    if template_arg.startswith("git+"):
        raise PlatformError(
            message=(
                "Git-hosted templates are not supported yet. "
                "Use a built-in name (e.g. 'aks') or a path to a local template folder."
            ),
            error_code="TEMPLATE_GIT_NOT_SUPPORTED",
        )

    template_folder = _resolve_folder(template_arg)
    scaffold_dir = template_folder / _SCAFFOLD_DIR
    if not scaffold_dir.is_dir():
        raise PlatformError(
            message=(
                f"Template folder '{template_folder}' has no 'scaffold/' subdirectory. "
                "A template folder must contain a 'scaffold/' directory with the files to copy."
            ),
            error_code="TEMPLATE_NO_SCAFFOLD",
            details={"template_folder": str(template_folder)},
        )

    manifest = _load_manifest(template_folder)
    logger.debug(
        "Template resolved",
        template_arg=template_arg,
        template_folder=str(template_folder),
        manifest=manifest.name if manifest else None,
    )
    return scaffold_dir, manifest


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_folder(template_arg: str) -> Path:
    """Map the raw argument to a template folder path."""
    candidate = Path(template_arg)

    # Absolute or relative path pointing at an existing directory
    if candidate.is_dir():
        return candidate.resolve()

    # Short name — no path separators and does not exist as a path
    is_short_name = "/" not in template_arg and "\\" not in template_arg and not candidate.exists()
    if is_short_name:
        builtin = get_pkg_templates_path() / _EXAMPLES_DIR / template_arg
        if builtin.is_dir():
            return builtin
        available = list_builtin_templates()
        available_str = ", ".join(available) if available else "(none installed)"
        raise PlatformError(
            message=(
                f"Unknown template '{template_arg}'. "
                f"Available built-in templates: {available_str}. "
                "You can also pass a path to a local template folder."
            ),
            error_code="TEMPLATE_NOT_FOUND",
            details={"template": template_arg, "available": available},
        )

    raise PlatformError(
        message=(
            f"Template '{template_arg}' is not a valid template name or an existing directory. "
            "Pass a built-in name (e.g. 'aks') or a path to a local template folder."
        ),
        error_code="TEMPLATE_NOT_FOUND",
        details={"template": template_arg},
    )


def _load_manifest_safe(template_folder: Path) -> Optional[ScaffoldTemplateModel]:
    """Load manifest without raising — returns ``None`` on any failure."""
    try:
        return _load_manifest(template_folder)
    except (PlatformError, ModelValidationError):
        return None


def _load_manifest(template_folder: Path) -> Optional[ScaffoldTemplateModel]:
    """Load and validate ``template.yaml`` from *template_folder*, or return ``None``."""
    manifest_path = template_folder / _MANIFEST_FILE
    if not manifest_path.exists():
        return None

    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlatformError(
            message=f"Could not read template manifest '{manifest_path}': {exc}",
            error_code="TEMPLATE_MANIFEST_READ_ERROR",
        ) from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise PlatformError(
            message=f"Template manifest '{manifest_path}' is not valid YAML: {exc}",
            error_code="TEMPLATE_MANIFEST_YAML_ERROR",
        ) from exc

    if not isinstance(data, dict):
        raise PlatformError(
            message=f"Template manifest '{manifest_path}' must be a YAML mapping.",
            error_code="TEMPLATE_MANIFEST_INVALID",
        )

    try:
        return ScaffoldTemplateModel.model_validate(data)
    except ValidationError as exc:
        errors = [{"loc": str(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ModelValidationError(
            model_name="ScaffoldTemplateModel",
            validation_errors=errors,
            message=f"Template manifest '{manifest_path}' validation failed ({len(errors)} error(s))",
        ) from exc
