"""Command to create a new platform configuration file from a template."""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import click
import yaml

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger
from strata.models.solution_model import SolutionSpecModel, SolutionTemplateModel
from strata.utils.system import get_pkg_templates_path
from strata.utils.templater import TemplateProcessor

_JINJA_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _extract_jinja_vars(paths: List[str]) -> set:
    """Return all undeclared variable names found in the given Jinja2 path strings."""
    found: set = set()
    for path in paths:
        found.update(_JINJA_VAR_RE.findall(path))
    return found


def _prompt_missing_vars(required: set, context: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Prompt for any variables in *required* that are absent from *context*.

    Returns an updated copy of *context* with all variables filled in,
    or ``None`` if the user cancelled (Ctrl+C / Ctrl+D).
    """
    result = dict(context)
    missing = sorted(required - result.keys())
    if not missing:
        return result
    click.echo("")
    try:
        for var in missing:
            result[var] = click.prompt(f"  {var}")
    except (click.Abort, KeyboardInterrupt):
        click.echo("\n⚠️  Cancelled.")
        return None
    return result


def _resolve_solution_template(name: str, solution_spec) -> Optional["SolutionTemplateModel"]:
    """Return the first template entry in *solution_spec* whose name matches *name*."""
    if solution_spec is None or not solution_spec.templates:
        return None
    for tpl in solution_spec.templates:
        if tpl.name == name:
            return tpl
    return None


def _collect_available_templates(
    work_path: Optional[Path],
    solution_templates: Optional[List["SolutionTemplateModel"]] = None,
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


def _collect_templates_with_descriptions(
    work_path: Optional[Path],
    solution_templates: Optional[List["SolutionTemplateModel"]] = None,
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
    examples_dir = get_pkg_templates_path() / "examples"
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
    manifest_path = bundle_dir / "template.yaml"
    if not manifest_path.exists():
        return ""
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("description", "")
    except Exception:
        pass
    return ""


def _resolve_at_repo_path(identifier: str, repo_map: Dict[str, str]) -> Optional[Path]:
    """Resolve a ``@repo_name/relative/path`` reference to an absolute ``Path``.

    Returns ``None`` if the repo is not registered or the identifier is malformed.
    """
    if not identifier.startswith("@"):
        return None
    rest = identifier[1:]
    if "/" not in rest:
        return None
    repo_name, rel_path = rest.split("/", 1)
    if repo_name not in repo_map:
        return None
    return Path(repo_map[repo_name]) / rel_path


def _collect_dep_candidates(
    graph_result,
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
            resolved = _resolve_at_repo_path(node.identifier, repo_map)
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


def _resolve_template_path(template: str, work_path: Optional[Path]) -> Optional[Path]:
    """Resolve the template for *template*, preferring workspace over package.

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


class NewCommand(BaseCommand):
    """Create a new platform configuration file from a template.

    The command works without a workspace; solution
    context loading is attempted but failures are silently ignored.
    """

    OPERATION = "new"

    def __init__(
        self,
        template: Optional[str],
        name: Optional[str],
        list_templates: bool = False,
        path: Optional[str] = None,
        overwrite: bool = False,
        set_values: Tuple[str, ...] = (),
        run_validate: bool = False,
        scaffold_deps: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)
        self._template = template
        self._name = name
        self._list_templates = list_templates
        self._path = path
        self._overwrite = overwrite
        self._set_values = set_values  # tuple of "KEY=VALUE" strings
        self._run_validate = run_validate
        self._scaffold_deps = scaffold_deps

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def _initialize(self, show_header: bool = True) -> bool:
        self._initialize_session(show_header=show_header)
        self.logger.debug(
            "NewCommand initializing",
            template=self._template,
            name=self._name,
            work_path=str(self._work_path),
        )
        return True

    def _before_execute(self) -> bool:
        return super()._before_execute()

    def _load_solution_spec(self) -> Optional[SolutionSpecModel]:
        """Best-effort load of solution.json.

        Returns the solution spec, or ``None`` when no workspace/solution.json
        is present or loading fails for any reason (silent, by design).
        """
        try:
            ok, _errors = self._solution_controller.load()
            if ok and self._solution_controller._solution is not None:
                return self._solution_controller._solution.spec
        except Exception:
            pass  # No workspace — skip silently
        return None

    def _execute(self) -> bool:
        # Best-effort: load solution.json for team context and solution templates.
        # Hoisted above the --list branch so both branches can see solution_spec.
        solution_spec = self._load_solution_spec()

        # --list: show available templates and exit cleanly
        if self._list_templates:
            solution_templates = solution_spec.templates if solution_spec else None
            templates = _collect_templates_with_descriptions(self._work_path, solution_templates=solution_templates)
            available = [t["name"] for t in templates]
            self._output_data = {"templates": templates}
            if self._is_console_output():
                if templates:
                    click.echo("\nAvailable templates:\n")
                    # Group by type
                    file_templates = [t for t in templates if "file" in t["type"]]
                    scaffold_templates = [t for t in templates if "scaffold" in t["type"] or "bundle" in t["type"]]

                    if file_templates:
                        click.echo("  Single-file templates (strata new <name> <NAME>):")
                        for t in file_templates:
                            ws_tag = " *" if "workspace" in t["type"] else ""
                            click.echo(f"    {t['name']}{ws_tag}")
                        click.echo("")

                    if scaffold_templates:
                        click.echo("  Scaffold bundles (strata sln init --template <name>):")
                        for t in scaffold_templates:
                            ws_tag = " *" if "workspace" in t["type"] else ""
                            desc = f" — {t['description']}" if t["description"] else ""
                            click.echo(f"    {t['name']}{desc}{ws_tag}")
                        click.echo("")

                    if any("workspace" in t["type"] for t in templates):
                        click.echo("  * = workspace-local template (.strata/templates/)")
                        click.echo("")
                else:
                    click.echo("No templates found.")
            return True

        assert self._template is not None and self._name is not None  # guarded in cli_new.py

        # 1. Build substitution context (shared by all tiers)
        context: Dict[str, str] = {"name": self._name}
        if solution_spec is not None:
            context.update(solution_spec.context or {})

        # Apply --set overrides (CLI wins over solution context)
        for kv in self._set_values:
            if "=" in kv:
                k, v = kv.split("=", 1)
                context[k.strip()] = v.strip()
            else:
                self.logger.warning("Ignoring malformed --set value (expected KEY=VALUE)", value=kv)

        # 2. Tier-0: solution.json spec.templates[]
        solution_tpl = _resolve_solution_template(self._template, solution_spec)
        if solution_tpl is not None:
            all_paths = [entry.path for entry in solution_tpl.bundle]
            required_vars = _extract_jinja_vars(all_paths)
            prompted = _prompt_missing_vars(required_vars, context)
            if prompted is None:
                return False
            context = prompted
            result = self._run_solution_bundle_execution(solution_tpl, context)
        else:
            # 3. Tiers 1-4: file-system resolution (workspace bundle/file → package bundle/file)
            template_path = _resolve_template_path(self._template, self._work_path)
            if template_path is None:
                available = _collect_available_templates(
                    self._work_path,
                    solution_templates=solution_spec.templates if solution_spec else None,
                )
                self._errors.append(
                    f"Template '{self._template}' not found. Available: {', '.join(available) if available else '(none)'}"
                )
                return False

            if template_path.is_dir():
                result = self._run_bundle_execution(template_path, context)
            else:
                result = self._run_file_execution(template_path, context)

        if result and self._run_validate:
            result = self._validate_generated()

        if result and self._scaffold_deps and not self._list_templates:
            created: list[str] = []
            if self._output_data:
                if "path" in self._output_data:
                    created = [self._output_data["path"]]
                else:
                    created = list(self._output_data.get("files", []))
            if created:
                self._scaffold_missing_deps(created)

        return result

    def _run_file_execution(self, template_path: Path, context: Dict[str, str]) -> bool:
        """Render a single-file template."""
        assert self._template is not None and self._name is not None

        # Read template content
        try:
            content = template_path.read_text(encoding="utf-8")
        except Exception as e:
            self._errors.append(f"Failed to read template '{template_path}': {e}")
            return False

        # Render
        rendered = TemplateProcessor.render(content, context)

        # Resolve output path
        output_path: Path
        if self._path:
            p = Path(self._path)
            if self._path.endswith(("/", "\\")) or (p.exists() and p.is_dir()):
                output_path = p / f"{self._name}-{self._template}.yaml"
            else:
                output_path = p
        else:
            output_path = Path(os.getcwd()) / f"{self._name}-{self._template}.yaml"

        # Overwrite guard
        if output_path.exists() and not self._overwrite:
            self._errors.append(f"File already exists: {output_path}. Use --overwrite to replace it.")
            return False

        # Write
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        except Exception as e:
            self._errors.append(f"Failed to write output file '{output_path}': {e}")
            return False

        self._messages.append(f"Created: {output_path}")
        self.logger.info("Configuration file created", path=str(output_path), template=self._template)
        self._output_data = {
            "template": self._template,
            "name": self._name,
            "path": str(output_path),
        }
        return True

    def _run_bundle_execution(self, bundle_dir: Path, context: Dict[str, str]) -> bool:
        """Render a directory bundle template.

        Walks *bundle_dir* recursively. For every file:

        - Each path segment is rendered through ``TemplateProcessor.render()``
          (same ``{{ var }}`` substitution as file content).
        - The file content is rendered the same way.
        - The rendered relative path is joined onto the output root (``--path``
          or CWD) to produce the final destination.
        """
        output_root = Path(self._path) if self._path else Path(os.getcwd())

        bundle_files = sorted(
            f
            for f in bundle_dir.rglob("*")
            if f.is_file() and not (f.name == "template.yaml" and f.parent == bundle_dir)
        )
        if not bundle_files:
            self._errors.append(f"Bundle '{self._template}' contains no files.")
            return False

        created: list[str] = []

        for src_file in bundle_files:
            # Render {{ var }} in every path segment
            rel = src_file.relative_to(bundle_dir)
            rendered_parts = [TemplateProcessor.render(part, context) for part in rel.parts]
            output_path = output_root.joinpath(*rendered_parts)

            # Overwrite guard
            if output_path.exists() and not self._overwrite:
                self._errors.append(f"File already exists: {output_path}. Use --overwrite to replace it.")
                return False

            # Read and render content
            try:
                content = src_file.read_text(encoding="utf-8")
            except Exception as e:
                self._errors.append(f"Failed to read bundle file '{src_file}': {e}")
                return False

            rendered = TemplateProcessor.render(content, context)

            # Write
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(rendered, encoding="utf-8")
            except Exception as e:
                self._errors.append(f"Failed to write output file '{output_path}': {e}")
                return False

            created.append(str(output_path))
            self.logger.info("Bundle file created", path=str(output_path), template=self._template)

        self._messages.extend(f"Created: {p}" for p in created)
        self._output_data = {
            "template": self._template,
            "name": self._name,
            "files": created,
        }
        return True

    def _run_solution_bundle_execution(self, template: "SolutionTemplateModel", context: Dict[str, str]) -> bool:
        """Execute a solution-level template bundle.

        For each entry in *template.bundle*:

        1. Render the ``path`` field with *context* to get the destination directory.
        2. Resolve the template source (``entry.name``) via the standard file-system
           resolution chain (tiers 1-4).
        3. Copy/render template content into the destination directory.
        """
        all_created: list[str] = []
        output_root = Path(self._path) if self._path else Path(os.getcwd())

        for entry in template.bundle:
            # Render destination path
            dest_dir = output_root / TemplateProcessor.render(entry.path, context)

            # Resolve source template
            source_path = _resolve_template_path(entry.name, self._work_path)
            if source_path is None:
                self._errors.append(
                    f"Bundle entry '{entry.name}' not found. "
                    f"Add a template named '{entry.name}' to .strata/templates/ or the package."
                )
                return False

            if source_path.is_dir():
                # Bundle directory → walk and copy all files into dest_dir
                bundle_files = sorted(
                    f
                    for f in source_path.rglob("*")
                    if f.is_file() and not (f.name == "template.yaml" and f.parent == source_path)
                )
                if not bundle_files:
                    self._errors.append(f"Bundle source '{entry.name}' contains no files.")
                    return False

                for src_file in bundle_files:
                    rel = src_file.relative_to(source_path)
                    rendered_parts = [TemplateProcessor.render(part, context) for part in rel.parts]
                    output_path = dest_dir.joinpath(*rendered_parts)

                    if output_path.exists() and not self._overwrite:
                        self._errors.append(f"File already exists: {output_path}. Use --overwrite to replace it.")
                        return False

                    try:
                        content = src_file.read_text(encoding="utf-8")
                    except Exception as e:
                        self._errors.append(f"Failed to read bundle file '{src_file}': {e}")
                        return False

                    rendered = TemplateProcessor.render(content, context)
                    try:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_text(rendered, encoding="utf-8")
                    except Exception as e:
                        self._errors.append(f"Failed to write '{output_path}': {e}")
                        return False

                    all_created.append(str(output_path))

            else:
                # Single YAML file → write into dest_dir using the file name
                output_path = dest_dir / source_path.name

                if output_path.exists() and not self._overwrite:
                    self._errors.append(f"File already exists: {output_path}. Use --overwrite to replace it.")
                    return False

                try:
                    content = source_path.read_text(encoding="utf-8")
                except Exception as e:
                    self._errors.append(f"Failed to read template '{source_path}': {e}")
                    return False

                rendered = TemplateProcessor.render(content, context)
                try:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(rendered, encoding="utf-8")
                except Exception as e:
                    self._errors.append(f"Failed to write '{output_path}': {e}")
                    return False

                all_created.append(str(output_path))

        self._messages.extend(f"Created: {p}" for p in all_created)
        self._output_data = {
            "template": template.name,
            "name": self._name,
            "files": all_created,
        }
        self.logger.info(
            "Solution bundle executed",
            template=template.name,
            files=all_created,
        )
        return True

    def _scaffold_missing_deps(self, created_paths: List[str]) -> None:
        """Detect and offer to scaffold missing file dependencies.

        Runs the dependency graph on every file produced by the current
        invocation, collects nodes with ``status="missing"`` (local path) or
        ``status="external"`` (``@repo/...`` reference), resolves them to
        absolute paths, and — after a single confirmation prompt — calls
        :meth:`_scaffold_single_dep` for each absent file.

        Scaffolding failures are reported inline but never propagate as errors
        on the parent command: the primary file was already created successfully.
        """
        from strata.controllers.graph_controller import GraphController

        work_path = self._work_path or Path(os.getcwd())

        # Resolve repo map for @repo/ references
        repo_map: Dict[str, str] = {}
        try:
            ok, _ = self._solution_controller.load()
            if ok:
                repo_map = self._solution_controller.get_repo_map()
        except Exception:
            pass

        # Collect candidates across all created files (deduplicated)
        all_candidates: List[Tuple[str, str, Path]] = []
        seen_paths: set[str] = set()

        for path_str in created_paths:
            created_path = Path(path_str)
            if not created_path.exists():
                continue
            try:
                entry_rel = str(created_path.relative_to(work_path))
            except ValueError:
                # Created file is outside the workspace root (e.g. written to CWD).
                # Skip dependency scanning for this file — we cannot resolve
                # relative cross-file references without a workspace root.
                self.logger.debug(
                    "Skipping dep-scan: file outside work_path",
                    path=path_str,
                    work_path=str(work_path),
                )
                continue

            gc = GraphController(work_path, entry=entry_rel, no_validate=True)
            graph_result = gc.build_file_graph()

            for kind, name, resolved in _collect_dep_candidates(graph_result, work_path, repo_map):
                key = str(resolved)
                if key not in seen_paths:
                    seen_paths.add(key)
                    all_candidates.append((kind, name, resolved))

        if not all_candidates:
            return

        count = len(all_candidates)
        label = "dependency" if count == 1 else "dependencies"
        click.echo(f"\n  ⚠  {count} missing {label} referenced by the scaffolded file:")
        for kind, _name, resolved in all_candidates:
            try:
                display = resolved.relative_to(work_path)
            except ValueError:
                display = resolved
            click.echo(f"       {kind:18s}  {display}")

        try:
            if not click.confirm("\n  Scaffold missing files?", default=True):
                return
        except (click.Abort, KeyboardInterrupt):
            click.echo("\n  Skipped.")
            return

        click.echo("")
        scaffolded: list[str] = []
        for kind, name, resolved in all_candidates:
            if resolved.exists():
                continue  # May have been created by a cascade earlier in the loop
            if self._scaffold_single_dep(kind, name, resolved):
                scaffolded.append(str(resolved))

        if scaffolded and self._output_data is not None:
            self._output_data["scaffolded"] = scaffolded

    def _scaffold_single_dep(self, kind: str, name: str, output_path: Path) -> bool:
        """Scaffold one dependency file using its kind template.

        Only single-file templates are supported.  Bundle templates are skipped
        with a warning — they require explicit path decisions that cannot be
        inferred automatically.
        """
        template_path = _resolve_template_path(kind, self._work_path)
        if template_path is None:
            click.echo(f"  ⚠  No template for kind '{kind}' \u2014 skipping")
            return False

        if template_path.is_dir():
            click.echo(f"  ⚠  Bundle template '{kind}' not supported for auto-scaffold \u2014 skipping")
            return False

        if output_path.exists() and not self._overwrite:
            try:
                display: Path | str = output_path.relative_to(self._work_path or Path.cwd())
            except ValueError:
                display = output_path
            click.echo(f"  \u23ed  Already exists: {display}")
            return True

        context: Dict[str, str] = {"name": name}
        for kv in self._set_values:
            if "=" in kv:
                k, v = kv.split("=", 1)
                context[k.strip()] = v.strip()

        try:
            content = template_path.read_text(encoding="utf-8")
        except Exception as e:
            click.echo(f"  \u274c  Failed to read template '{template_path}': {e}")
            return False

        rendered = TemplateProcessor.render(content, context)

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        except Exception as e:
            click.echo(f"  \u274c  Failed to write '{output_path}': {e}")
            return False

        try:
            display = output_path.relative_to(self._work_path or Path.cwd())
        except ValueError:
            display = output_path
        click.echo(f"  \u2705  Created: {display}")
        self.logger.info("Dependency scaffolded", path=str(output_path), kind=kind)
        return True

    def _validate_generated(self) -> bool:
        """Validate every file produced by the most recent generation step.

        Reads generated paths from ``self._output_data`` (key ``path`` for
        single-file execution, ``files`` for bundle execution).  Validation
        errors are appended to ``self._errors`` but the generated files are
        *not* rolled back — the operator may edit and re-validate manually.
        """
        from strata.validators.platform_validator import PlatformValidator

        data = self._output_data or {}
        paths: list[str] = [data["path"]] if "path" in data else list(data.get("files", []))

        if not paths:
            return True

        all_ok = True
        for path_str in paths:
            file_path = Path(path_str)
            try:
                validator = PlatformValidator(
                    file_path=file_path,
                    configuration_service=None,
                    repo_map=None,
                    verify_digests=False,
                )
                for phase_fn in (validator.before_validate, validator.validate, validator.after_validate):
                    if not phase_fn(self._work_path):
                        break
                if validator.has_errors():
                    for err in validator.get_errors():
                        self._errors.append(f"[validate] {file_path.name}: {err}")
                    all_ok = False
                else:
                    self._messages.append(f"Validated: {file_path.name} ✓")
            except Exception as e:
                self._errors.append(f"[validate] {file_path.name}: unexpected error — {e}")
                all_ok = False
        return all_ok

    def _after_execute(self) -> bool:
        if not self._list_templates and self._is_console_output():
            path = self._output_data.get("path", "")
            files: list = self._output_data.get("files", [])
            if path:
                click.echo(f"\n✅  Created: {path}\n")
            elif files:
                click.echo(f"\n✅  Created {len(files)} file(s):")
                for f in files:
                    click.echo(f"     {f}")
                click.echo("")
        return super()._after_execute()
