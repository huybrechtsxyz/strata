"""Command to create a new platform configuration file from a template."""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import click
import yaml

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger
from strata.utils.system import get_pkg_templates_path
from strata.utils.templater import TemplateProcessor


def _collect_available_templates(work_path: Optional[Path]) -> list[str]:
    """Collect template stems from workspace and package directories.

    Workspace templates (`.strata/templates/`) take precedence but both
    sources contribute to the *available* list shown to the user.  A template
    may be either a single YAML file (``namespace.yaml``) or a bundle
    directory (``tenant/``).

    Args:
        work_path: Root of the current workspace, or None.

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

    return sorted(stems)


def _collect_templates_with_descriptions(work_path: Optional[Path]) -> List[Dict[str, str]]:
    """Collect all templates with descriptions from all sources.

    Scans:
    1. Package single-file templates (``.strata/templates/*.yaml``)
    2. Package bundle templates (``templates/examples/``)
    3. Workspace single-file templates
    4. Workspace bundle templates

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

    # Workspace-local templates
    if work_path is not None:
        ws_dir = work_path / ".strata" / "templates"
        if ws_dir.is_dir():
            for f in ws_dir.iterdir():
                if f.is_file() and f.suffix == ".yaml":
                    templates[f.stem] = {
                        "name": f.stem,
                        "description": f"Workspace {f.stem} template",
                        "type": "file (workspace)",
                    }
                elif f.is_dir():
                    desc = _read_bundle_description(f)
                    tpl_type = "scaffold (workspace)" if (f / "scaffold").is_dir() else "bundle (workspace)"
                    templates[f.name] = {
                        "name": f.name,
                        "description": desc or f"Workspace template: {f.name}",
                        "type": tpl_type,
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

    ``INIT_REQUIRED = False`` — the command works without a workspace; solution
    context loading is attempted but failures are silently ignored.
    """

    OPERATION = "new"
    INIT_REQUIRED = False

    def __init__(
        self,
        template: Optional[str],
        name: Optional[str],
        list_templates: bool = False,
        path: Optional[str] = None,
        overwrite: bool = False,
        set_values: Tuple[str, ...] = (),
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

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def execute(self) -> bool:
        try:
            if not self._initialize():
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                self.logger.error(f"Pre-execution validation failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            if not self._run_execution():
                self.logger.error(f"Execution failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Execution failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                self.logger.error(f"Post-execution processing failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Post-execution processing failed")
                self._finalize(success=False)
                return False

            self._finalize(success=True)
            return True

        except Exception as e:
            error_msg = f"Failed to create configuration file: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def _initialize(self, show_header: bool = True) -> bool:
        if not super()._initialize(show_header=show_header):
            return False
        self.logger.debug(
            "NewCommand initializing",
            template=self._template,
            name=self._name,
            work_path=str(self._work_path),
        )
        return True

    def _before_execute(self) -> bool:
        return super()._before_execute()

    def _run_execution(self) -> bool:
        # --list: show available templates and exit cleanly
        if self._list_templates:
            templates = _collect_templates_with_descriptions(self._work_path)
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

        # 1. Resolve template (file or bundle directory)
        assert self._template is not None and self._name is not None  # guarded in cli_new.py
        template_path = _resolve_template_path(self._template, self._work_path)
        if template_path is None:
            available = _collect_available_templates(self._work_path)
            self._errors.append(
                f"Template '{self._template}' not found. Available: {', '.join(available) if available else '(none)'}"
            )
            return False

        # 2. Build substitution context (shared by both modes)
        context: Dict[str, str] = {"name": self._name}

        # Best-effort: load team context from solution.json if workspace is available
        try:
            ok, _errors = self._solution_controller.load()
            if ok and self._solution_controller._solution is not None:
                team_context = self._solution_controller._solution.spec.context or {}
                context.update(team_context)
        except Exception:
            pass  # No workspace — skip silently

        # Apply --set overrides (CLI wins)
        for kv in self._set_values:
            if "=" in kv:
                k, v = kv.split("=", 1)
                context[k.strip()] = v.strip()
            else:
                self.logger.warning("Ignoring malformed --set value (expected KEY=VALUE)", value=kv)

        # 3. Dispatch
        if template_path.is_dir():
            return self._run_bundle_execution(template_path, context)
        return self._run_file_execution(template_path, context)

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
          (same ``${var}`` substitution as file content).
        - The file content is rendered the same way.
        - The rendered relative path is joined onto the output root (``--path``
          or CWD) to produce the final destination.
        """
        output_root = Path(self._path) if self._path else Path(os.getcwd())

        bundle_files = sorted(f for f in bundle_dir.rglob("*") if f.is_file())
        if not bundle_files:
            self._errors.append(f"Bundle '{self._template}' contains no files.")
            return False

        created: list[str] = []

        for src_file in bundle_files:
            # Render ${var} in every path segment
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
