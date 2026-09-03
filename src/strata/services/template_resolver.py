#!/usr/bin/env python3
"""Resolves a --template argument to a scaffold folder and optional manifest."""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from pydantic import ValidationError

from strata.exceptions import ModelValidationError, PlatformError
from strata.logger import get_logger
from strata.models.scaffold_template_model import ScaffoldTemplateModel
from strata.models.solution_model import SolutionSpecModel, SolutionTemplateModel
from strata.utils.config import get_templates_dir
from strata.utils.graph import GraphResult
from strata.utils.system import get_pkg_templates_path, resolve_path, split_repo_ref

logger = get_logger(__name__)

_EXAMPLES_DIR = "examples"
_MANIFEST_FILE = "template.yaml"
_SCAFFOLD_DIR = "scaffold"
_JINJA_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


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
        ws_dir = get_templates_dir(work_path)
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


# ---------------------------------------------------------------------------
# `strata new` template discovery/resolution (multi-source: workspace, package,
# and solution.json — distinct from the `--template`/`sln init` scaffold-folder
# resolution above, which only looks at the package `examples/` directory).
# ---------------------------------------------------------------------------


def extract_jinja_vars(paths: List[str]) -> set:
    """Return all undeclared variable names found in the given Jinja2 path strings."""
    found: set = set()
    for path in paths:
        found.update(_JINJA_VAR_RE.findall(path))
    return found


def resolve_solution_template(name: str, solution_spec: Optional[SolutionSpecModel]) -> Optional[SolutionTemplateModel]:
    """Return the first template entry in *solution_spec* whose name matches *name*."""
    if solution_spec is None or not solution_spec.templates:
        return None
    for tpl in solution_spec.templates:
        if tpl.name == name:
            return tpl
    return None


def collect_available_templates(
    work_path: Optional[Path],
    solution_templates: Optional[List[SolutionTemplateModel]] = None,
) -> list[str]:
    """Collect template stems from workspace, package, and solution.json sources.

    Workspace templates (`.strata/templates/`) take precedence but both
    sources contribute to the *available* list shown to the user.  A template
    may be either a single YAML file (``namespace.yaml``) or a bundle
    directory (``tenant/``).

    Args:
        work_path: Root of the current workspace, or None.
        solution_templates: Solution-level templates declared in
            ``solution.json``'s ``spec.templates[]``, or None.

    Returns:
        Sorted, deduplicated list of template stems (e.g. ``["namespace", "provider"]``).
    """
    stems: set[str] = set()

    # Package-bundled templates
    pkg_dir = get_pkg_templates_path() / "solution" / "dot.strata" / "templates"
    if pkg_dir.exists() and pkg_dir.is_dir():
        for f in pkg_dir.iterdir():
            if f.is_file() and f.suffix == ".yaml":
                stems.add(f.stem)
            elif f.is_dir():
                stems.add(f.name)

    # Workspace-local templates (may override package ones)
    if work_path is not None:
        ws_dir = work_path / ".strata" / "templates"
        if ws_dir.exists() and ws_dir.is_dir():
            for f in ws_dir.iterdir():
                if f.is_file() and f.suffix == ".yaml":
                    stems.add(f.stem)
                elif f.is_dir():
                    stems.add(f.name)

    # Solution-level templates (solution.json spec.templates[])
    if solution_templates:
        for tpl in solution_templates:
            stems.add(tpl.name)

    return sorted(stems)


def collect_templates_with_descriptions(
    work_path: Optional[Path],
    solution_templates: Optional[List[SolutionTemplateModel]] = None,
) -> List[Dict[str, str]]:
    """Collect all templates with descriptions from all sources.

    Scans:
    1. Package single-file templates (``.strata/templates/*.yaml``)
    2. Package bundle templates (``templates/examples/``)
    3. Workspace single-file templates
    4. Workspace bundle templates
    5. Solution-level templates (``solution.json`` ``spec.templates[]``)

    Args:
        work_path: Root of the current workspace, or None.
        solution_templates: Solution-level templates declared in
            ``solution.json``'s ``spec.templates[]``, or None.

    Returns a sorted list of dicts with keys: ``name``, ``description``, ``type``.
    """
    templates: Dict[str, Dict[str, str]] = {}

    # Package single-file templates
    pkg_dir = get_pkg_templates_path() / "solution" / "dot.strata" / "templates"
    if pkg_dir.exists() and pkg_dir.is_dir():
        for f in pkg_dir.iterdir():
            if f.is_file() and f.suffix == ".yaml":
                templates[f.stem] = {
                    "name": f.stem,
                    "description": f"Single-file {f.stem} template",
                    "type": "file",
                }
            elif f.is_dir():
                desc = _read_bundle_description(f)
                templates[f.name] = {
                    "name": f.name,
                    "description": desc or f"Bundle template: {f.name}",
                    "type": "bundle",
                }

    # Package scaffold templates (examples/)
    examples_dir = get_pkg_templates_path() / _EXAMPLES_DIR
    if examples_dir.is_dir():
        for p in examples_dir.iterdir():
            if p.is_dir():
                desc = _read_bundle_description(p)
                templates[p.name] = {
                    "name": p.name,
                    "description": desc or f"Scaffold template: {p.name}",
                    "type": "scaffold",
                }

    # Workspace-local templates — directories take priority over same-named YAML files
    if work_path is not None:
        ws_dir = work_path / ".strata" / "templates"
        if ws_dir.is_dir():
            # Pass 1: single-file templates
            for f in ws_dir.iterdir():
                if f.is_file() and f.suffix == ".yaml":
                    templates[f.stem] = {
                        "name": f.stem,
                        "description": f"Workspace {f.stem} template",
                        "type": "file (workspace)",
                    }
            # Pass 2: bundle directories (overrides same-named file entry)
            for f in ws_dir.iterdir():
                if f.is_dir():
                    desc = _read_bundle_description(f)
                    tpl_type = "scaffold (workspace)" if (f / "scaffold").is_dir() else "bundle (workspace)"
                    templates[f.name] = {
                        "name": f.name,
                        "description": desc or f"Workspace template: {f.name}",
                        "type": tpl_type,
                    }

    # Solution-level templates (solution.json spec.templates[]) — a third source,
    # distinct from workspace/package filesystem templates. On name collision with
    # a filesystem template, last-write-wins (mirrors existing workspace-vs-package
    # collision behavior above) since both accumulate into the same `templates` dict.
    if solution_templates:
        for tpl in solution_templates:
            count = len(tpl.bundle)
            templates[tpl.name] = {
                "name": tpl.name,
                "description": f"Solution template: {tpl.name} ({count} file{'s' if count != 1 else ''})",
                "type": "bundle (solution)",
            }

    return sorted(templates.values(), key=lambda t: t["name"])


def _read_bundle_description(bundle_dir: Path) -> str:
    """Read description from template.yaml manifest if present."""
    manifest_path = bundle_dir / _MANIFEST_FILE
    if not manifest_path.exists():
        return ""
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("description", "")
    except Exception:
        pass
    return ""


def resolve_at_repo_path(identifier: str, repo_map: Dict[str, str]) -> Optional[Path]:
    """Resolve a ``@repo_name/relative/path`` reference to an absolute ``Path``.

    Returns ``None`` if the repo is not registered, the identifier is malformed
    (e.g. missing the trailing ``/relative/path`` segment), or *identifier* is
    not an ``@`` reference at all.

    Thin wrapper around :func:`strata.utils.system.resolve_path` (ADR-0073) —
    kept as a distinct, non-raising function since callers here treat
    "unresolvable" as "skip", not as an error.
    """
    split = split_repo_ref(identifier)
    if split is None or not split["rest"]:
        return None
    try:
        return resolve_path("", identifier, repo_map=repo_map)
    except ValueError:
        return None


def collect_dep_candidates(
    graph_result: GraphResult,
    work_path: Path,
    repo_map: Dict[str, str],
) -> List[Tuple[str, str, Path]]:
    """Return ``(kind, name, resolved_path)`` tuples for unresolved dependencies.

    Covers two node statuses from the graph:

    - ``"missing"``: local relative path that does not exist on disk.
    - ``"external"``: ``@repo/...`` reference; resolved via *repo_map* and
      checked for existence — only included when the resolved file is absent.

    Nodes that are already present on disk are silently skipped.
    """
    seen: set[str] = set()
    candidates: List[Tuple[str, str, Path]] = []

    for node in graph_result.nodes:
        if node.status not in ("missing", "external"):
            continue
        if node.identifier in seen:
            continue
        seen.add(node.identifier)

        kind = node.kind if node.kind not in ("unknown", "") else None
        if not kind:
            continue

        if node.identifier.startswith("@"):
            resolved = resolve_at_repo_path(node.identifier, repo_map)
            if resolved is None:
                continue  # Repo not registered — cannot scaffold
            if resolved.exists():
                continue
        else:
            resolved = work_path / node.identifier
            if resolved.exists():
                continue

        candidates.append((kind, resolved.stem, resolved))

    return candidates


def resolve_new_template_path(template: str, work_path: Optional[Path]) -> Optional[Path]:
    """Resolve the template for ``strata new``, preferring workspace over package.

    Resolution order (first match wins):

    1. Workspace bundle directory  (``.strata/templates/<name>/``)
    2. Workspace single YAML file  (``.strata/templates/<name>.yaml``)
    3. Package bundle directory
    4. Package single YAML file

    Args:
        template: Template stem (e.g. ``"namespace"`` or ``"Tenant"``).
        work_path: Root of the current workspace, or None.

    Returns:
        Path to the template file or bundle directory, or None when not found.
    """
    pkg_base = get_pkg_templates_path() / "solution" / "dot.strata" / "templates"

    # 1 & 2 — workspace
    if work_path is not None:
        ws_bundle = work_path / ".strata" / "templates" / template
        if ws_bundle.exists() and ws_bundle.is_dir():
            return ws_bundle
        ws_file = work_path / ".strata" / "templates" / f"{template}.yaml"
        if ws_file.exists():
            return ws_file

    # 3 & 4 — package
    pkg_bundle = pkg_base / template
    if pkg_bundle.exists() and pkg_bundle.is_dir():
        return pkg_bundle
    pkg_file = pkg_base / f"{template}.yaml"
    if pkg_file.exists():
        return pkg_file

    return None
