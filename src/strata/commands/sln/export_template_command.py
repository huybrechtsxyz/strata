"""Command to export the current workspace as a reusable scaffold template."""

import shutil
from pathlib import Path
from typing import Optional

import click

from strata.commands.base_command import BaseCommand
from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)

# Directories and patterns excluded from the scaffold export
_EXCLUDE_DIRS = {
    ".git",
    "repos",
    ".venv",
    "node_modules",
}

_EXCLUDE_STRATA_SUBDIRS = {
    "logs",
}

_EXCLUDE_FILE_SUFFIXES = {
    ".pyc",
    ".log",
}

_EXCLUDE_DIR_NAMES = {
    "__pycache__",
}

_EXCLUDE_DIR_SUFFIXES = {
    ".egg-info",
}


def _is_excluded(rel: Path) -> bool:
    """Return True if *rel* (relative to workspace root) should be skipped."""
    parts = rel.parts

    # Top-level excluded directories
    if parts[0] in _EXCLUDE_DIRS:
        return True

    # .strata/ subdirectories (e.g. .strata/logs/)
    if parts[0] == ".strata" and len(parts) > 1 and parts[1] in _EXCLUDE_STRATA_SUBDIRS:
        return True

    # Any path component that is an excluded directory name or suffix
    for part in parts[:-1]:  # directories only (not the file itself)
        if part in _EXCLUDE_DIR_NAMES:
            return True
        if any(part.endswith(sfx) for sfx in _EXCLUDE_DIR_SUFFIXES):
            return True

    # File-level exclusions
    file_name = parts[-1]
    if any(file_name.endswith(sfx) for sfx in _EXCLUDE_FILE_SUFFIXES):
        return True
    if file_name in _EXCLUDE_DIR_NAMES:
        return True

    return False


class SolutionExportCommand(BaseCommand):
    """Export the current workspace as a reusable scaffold template."""

    OPERATION = "sln_export"
    INIT_REQUIRED = True

    def __init__(
        self,
        name: str,
        force: bool = False,
        dry_run: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._name = name
        self._force = force
        self._dry_run = dry_run

    def get_required_integrations(self):
        return {}

    def execute(self) -> bool:
        try:
            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            success = self._run_export()

            self._finalize(success=success)
            return success

        except Exception as e:
            error_msg = f"Failed to export template: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _run_export(self) -> bool:
        solution = self._solution_controller.solution
        if solution is None:
            self._errors.append("No solution loaded.")
            return False

        solution_name = str(solution.meta.name)
        output_dir = self._work_path / ".strata" / "templates" / self._name
        scaffold_dir = output_dir / "scaffold"
        template_yaml = output_dir / "template.yaml"

        # Collect files from workspace root
        files = self._collect_files(solution_name)

        if not files:
            self._errors.append("No files found to export.")
            return False

        file_count = len(files)
        sub_count = sum(subs for _, _, subs in files)

        if self._dry_run:
            self._print_dry_run(output_dir, files, solution_name)
            return True

        # Handle existing output directory
        if output_dir.exists():
            if not self._force:
                self._errors.append(f"Output directory already exists: {output_dir}\nUse --force to overwrite.")
                return False
            shutil.rmtree(output_dir)
            self.logger.debug("Removed existing output dir", path=str(output_dir))

        # Write scaffold files
        for rel_path, content, _ in files:
            dest = scaffold_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            self.logger.debug("Written scaffold file", path=str(dest))

        # Write template.yaml
        template_yaml_content = (
            f"name: {self._name}\n"
            f"description: Exported from {solution_name} workspace\n"
            "variables:\n"
            "  - name: solution_name\n"
            "    description: Logical name for your solution\n"
            f"    default: {solution_name}\n"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        template_yaml.write_text(template_yaml_content, encoding="utf-8")

        self._output_data = {
            "template_name": self._name,
            "output_dir": str(output_dir),
            "scaffold_dir": str(scaffold_dir),
            "template_yaml": str(template_yaml),
            "file_count": file_count,
            "substitution_count": sub_count,
        }

        if self._is_console_output():
            click.echo("")
            click.echo(f"✅  Template exported: {self._name}")
            click.echo(f"📂  Output           : {output_dir}")
            click.echo(f"📄  Files written    : {file_count}")
            click.echo(f"🔁  Substitutions    : {sub_count}")
            click.echo("")
            click.echo("💡  Next steps:")
            click.echo(f"    xyz init --name <new-ws> --template .strata/templates/{self._name}/")

        return True

    def _collect_files(self, solution_name: str) -> list:
        """Scan workspace root and collect (rel_path, processed_content, sub_count) tuples."""
        results = []
        for abs_path in self._work_path.rglob("*"):
            if not abs_path.is_file():
                continue

            rel = abs_path.relative_to(self._work_path)
            if _is_excluded(rel):
                continue

            # Substitute solution name in the relative path string
            rel_str = str(rel).replace("\\", "/")
            new_rel_str, path_subs = _substitute(rel_str, solution_name)
            new_rel = Path(new_rel_str)

            # Read and substitute in content (skip binary files)
            try:
                raw = abs_path.read_text(encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, OSError):
                # Binary or unreadable — include as-is (no substitution)
                try:
                    raw = abs_path.read_bytes().decode("latin-1")
                    new_content = raw
                    content_subs = 0
                except Exception:
                    self.logger.debug("Skipping unreadable file", path=str(abs_path))
                    continue
            else:
                new_content, content_subs = _substitute(raw, solution_name)

            total_subs = path_subs + content_subs
            results.append((new_rel, new_content, total_subs))

        return results

    def _print_dry_run(self, output_dir: Path, files: list, solution_name: str) -> None:
        click.echo("")
        click.echo("🔍  DRY RUN — no files will be written")
        click.echo(f"📂  Output dir  : {output_dir}")
        click.echo(f"📄  Files found : {len(files)}")
        click.echo("")
        for rel_path, _content, subs in files:
            sub_info = f"  [{subs} sub(s)]" if subs else ""
            click.echo(f"    {rel_path}{sub_info}")
        click.echo("")
        click.echo(
            f"🔁  Total substitutions of '{solution_name}' → '${{solution_name}}': {sum(s for _, _, s in files)}"
        )


def _substitute(text: str, solution_name: str) -> tuple:
    """Replace all occurrences of *solution_name* with ``${solution_name}`` in *text*.

    Returns (new_text, count).
    """
    if not solution_name:
        return text, 0
    count = text.count(solution_name)
    return text.replace(solution_name, "${solution_name}"), count


@click.command(name="export", help="Export the current workspace as a reusable scaffold template.")
@click.option("--name", required=True, type=str, help="Name for the saved template.")
@click.option("--force", is_flag=True, default=False, help="Overwrite if output already exists.")
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Show what would be written without making changes.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def export_command(
    name: str,
    force: bool = False,
    dry_run: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Export the workspace as a scaffold template."""
    command = SolutionExportCommand(
        name=name,
        force=force,
        dry_run=dry_run,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
