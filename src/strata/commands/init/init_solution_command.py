"""Command to initialize a new Strata solution workspace."""

from pathlib import Path
from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.exceptions import PlatformError
from strata.logger import get_logger
from strata.models.scaffold_template_model import ScaffoldTemplateModel
from strata.services.template_resolver import resolve_template


class InitSolutionCommand(BaseCommand):
    """
    Initialize a new Strata solution workspace.

    Creates the ``.strata/`` state directory, ``solution.json``, and a
    ``<name>.code-workspace`` file in the work path.

    When *template* is given, the matching scaffold folder is copied into
    the workspace root with ``${solution_name}`` (and any other declared
    variables) substituted throughout file contents and filenames.
    """

    OPERATION = "solution_init"
    INIT_REQUIRED = False  # Allow running even if no existing solution is detected

    def __init__(
        self,
        name: str,
        template: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self.logger = get_logger(self.__class__.__module__)
        self._solution_name = name
        self._template_arg: Optional[str] = template
        self._scaffold_dir: Optional[Path] = None
        self._template_manifest: Optional[ScaffoldTemplateModel] = None

    # ------------------------------------------------------------------
    # BaseCommand interface
    # ------------------------------------------------------------------

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
            error_msg = f"Failed to initialize solution workspace: {e}"
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
            "InitSolutionCommand initializing",
            extra={
                "solution_name": self._solution_name,
                "work_path": str(self._work_path),
            },
        )
        return True

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        if self._template_arg is not None:
            try:
                self._scaffold_dir, self._template_manifest = resolve_template(self._template_arg)
            except PlatformError as exc:
                self._errors.append(exc.message)
                return False
        return True

    def _run_execution(self) -> bool:
        """Run the main execution logic for initializing the solution workspace."""
        ok, errors = self._solution_controller.init(self._solution_name)
        self._messages.extend(self._solution_controller.get_messages())
        self._errors.extend(errors)

        if not ok:
            self._finalize(success=False)
            return False

        if self._scaffold_dir is not None:
            if not self._copy_scaffold(self._scaffold_dir):
                self._finalize(success=False)
                return False

        self._output_data = {
            "solution_name": self._solution_name,
            "solution_id": self._solution_controller.get_solution_id(),
            "work_path": str(self._work_path),
            "template": self._template_arg,
        }

        return True

    # ------------------------------------------------------------------
    # Scaffold copying
    # ------------------------------------------------------------------

    def _copy_scaffold(self, scaffold_dir: Path) -> bool:
        """Copy all files from *scaffold_dir* into the workspace root.

        - Substitutes ``${variable_name}`` in file contents and relative paths.
        - Skips files that already exist (idempotent).
        - ``${solution_name}`` is always available from ``--name``.
        """
        variables = self._build_variables()

        for src_file in sorted(scaffold_dir.rglob("*")):
            if not src_file.is_file():
                continue
            if "__pycache__" in src_file.parts or src_file.suffix in (".pyc", ".pyo"):
                continue

            rel_str = str(src_file.relative_to(scaffold_dir))
            rel_str = _substitute(rel_str, variables)
            dest = self._work_path / rel_str

            if dest.exists():
                self.logger.debug("Scaffold file already exists — skipping", path=str(dest))
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)

            try:
                content = src_file.read_text(encoding="utf-8")
            except OSError as exc:
                self._errors.append(f"Failed to read scaffold file '{src_file}': {exc}")
                return False

            content = _substitute(content, variables)

            try:
                dest.write_text(content, encoding="utf-8")
            except OSError as exc:
                self._errors.append(f"Failed to write scaffold file '{dest}': {exc}")
                return False

            self.logger.info("Scaffold file written", path=str(dest))
            self._messages.append(f"Created: {dest.relative_to(self._work_path)}")

        return True

    def _build_variables(self) -> dict[str, str]:
        """Build the substitution variable map for scaffold copying."""
        variables: dict[str, str] = {"solution_name": self._solution_name}
        if self._template_manifest is not None:
            for var in self._template_manifest.variables:
                if var.name not in variables:
                    variables[var.name] = var.default
        return variables

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _after_execute(self) -> bool:
        if self._is_console_output():
            click.echo(f"\n✅  Solution '{self._solution_name}' initialised")
            click.echo(f"    • Work path    : {self._work_path}")
            click.echo(f"    • Solution ID  : {self._output_data.get('solution_id', '')}")
            if self._template_arg:
                manifest_name = self._template_manifest.name if self._template_manifest else self._template_arg
                scaffold_files = [m for m in self._messages if m.startswith("Created:")]
                click.echo(f"    • Template     : {manifest_name}")
                click.echo(f"    • Files created: {len(scaffold_files)}")
            click.echo("")
            click.echo("Next steps:")
            if self._scaffold_dir is not None:
                click.echo(f"    1. Register your repo:   strata repo add {self._solution_name} <git-url> --clone")
                click.echo("    2. Add a profile:        strata profile add prd --activate")
                click.echo("    3. Validate:             strata validate --file deploy/deploy-prd.yaml")
                click.echo("    4. Deploy:               strata deploy run --file deploy/deploy-prd.yaml")
            else:
                click.echo("    1. Scaffold config files:  strata init --name <name> --template aks")
                click.echo(f"    2. Register your repo:     strata repo add {self._solution_name} <git-url> --clone")
                click.echo("    3. Add a profile:          strata profile add prd --activate")
            click.echo("")
        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _substitute(text: str, variables: dict[str, str]) -> str:
    """Replace ``${key}`` placeholders in *text* with values from *variables*."""
    for key, value in variables.items():
        text = text.replace(f"${{{key}}}", value)
    return text
