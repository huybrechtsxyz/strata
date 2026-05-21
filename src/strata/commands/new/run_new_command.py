"""Command to create a new platform configuration file from a template."""

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

import click

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger
from strata.utils.system import get_pkg_templates_path
from strata.utils.templater import TemplateProcessor


def _collect_available_templates(work_path: Optional[Path]) -> list[str]:
    """Collect template stems from workspace and package directories.

    Workspace templates (`.strata/templates/`) take precedence but both
    sources contribute to the *available* list shown to the user.

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

    # Workspace-local templates (may override package ones)
    if work_path is not None:
        ws_dir = work_path / ".strata" / "templates"
        if ws_dir.exists() and ws_dir.is_dir():
            for f in ws_dir.iterdir():
                if f.is_file() and f.suffix == ".yaml":
                    stems.add(f.stem)

    return sorted(stems)


def _resolve_template_path(template: str, work_path: Optional[Path]) -> Optional[Path]:
    """Resolve the YAML file for *template*, preferring workspace over package.

    Args:
        template: Template stem (e.g. ``"namespace"``).
        work_path: Root of the current workspace, or None.

    Returns:
        Path to the template file, or None when not found.
    """
    # 1. Workspace-local templates
    if work_path is not None:
        ws_path = work_path / ".strata" / "templates" / f"{template}.yaml"
        if ws_path.exists():
            return ws_path

    # 2. Package-bundled templates
    pkg_path = get_pkg_templates_path() / "solution" / "dot.strata" / "templates" / f"{template}.yaml"
    if pkg_path.exists():
        return pkg_path

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
            available = _collect_available_templates(self._work_path)
            self._output_data = {"templates": available}
            if self._is_console_output():
                if available:
                    click.echo("\nAvailable templates:")
                    for t in available:
                        click.echo(f"  {t}")
                    click.echo("")
                else:
                    click.echo("No templates found.")
            return True

        # 1. Resolve template file
        assert self._template is not None and self._name is not None  # guarded in cli_new.py
        template_path = _resolve_template_path(self._template, self._work_path)
        if template_path is None:
            available = _collect_available_templates(self._work_path)
            self._errors.append(
                f"Template '{self._template}' not found. Available: {', '.join(available) if available else '(none)'}"
            )
            return False

        # 2. Read template content
        try:
            content = template_path.read_text(encoding="utf-8")
        except Exception as e:
            self._errors.append(f"Failed to read template '{template_path}': {e}")
            return False

        # 3. Build substitution context
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

        # 4. Render
        rendered = TemplateProcessor.render(content, context)

        # 5. Resolve output path
        output_path: Path
        if self._path:
            p = Path(self._path)
            # Treat as directory when it ends with separator or already is a directory
            if self._path.endswith(("/", "\\")) or (p.exists() and p.is_dir()):
                output_path = p / f"{self._name}-{self._template}.yaml"
            else:
                output_path = p
        else:
            output_path = Path(os.getcwd()) / f"{self._name}-{self._template}.yaml"

        # 6. Overwrite guard
        if output_path.exists() and not self._overwrite:
            self._errors.append(f"File already exists: {output_path}. Use --overwrite to replace it.")
            return False

        # 7. Write file
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        except Exception as e:
            self._errors.append(f"Failed to write output file '{output_path}': {e}")
            return False

        self._messages.append(f"Created: {output_path}")
        self.logger.info("Configuration file created", path=str(output_path), template=self._template)

        # 8. Store structured result
        self._output_data = {
            "template": self._template,
            "name": self._name,
            "path": str(output_path),
        }
        return True

    def _after_execute(self) -> bool:
        if not self._list_templates and self._is_console_output():
            path = self._output_data.get("path", "")
            if path:
                click.echo(f"\n✅  Created: {path}\n")
        return super()._after_execute()
