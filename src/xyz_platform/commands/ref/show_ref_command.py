"""Command to display the content of a registered ref-path file."""

from pathlib import Path
from typing import Dict, List, Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.utils.system import resolve_path


class ShowRefCommand(BaseCommand):
    """Show the raw content of a file registered under a profile ref-path.

    Resolves ``@repo_name/...`` cross-repo references and reads the file
    from disk, printing its content to the console.
    """

    OPERATION = "ref_show"
    INIT_REQUIRED = True

    def __init__(
        self,
        path_type: str,
        name: str,
        profile: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._path_type = path_type
        self._path_name = name
        self._profile_arg = profile
        self._resolved_profile: str = ""
        self._file_content: str = ""

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
            error_msg = f"Failed to show ref file: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False

        # Resolve active profile when --profile is not given
        if self._profile_arg:
            self._resolved_profile = self._profile_arg
        else:
            active, errors = self._solution_controller.get_active_profile()
            if errors:
                self._errors.extend(errors)
                return False
            if active is None:
                self._errors.append("No active profile found. Use --profile to specify one.")
                return False
            self._resolved_profile = str(active.name)

        return True

    def _run_execution(self) -> bool:
        """Locate the named path entry and display its file content."""
        paths_by_type, errors = self._solution_controller.get_profile_paths(self._resolved_profile)
        if errors:
            self._errors.extend(errors)
            return False

        entries: List = paths_by_type.get(self._path_type, [])
        match = next((e for e in entries if str(e.name) == self._path_name), None)
        if match is None:
            self._errors.append(
                f"No {self._path_type} entry named '{self._path_name}' found in profile '{self._resolved_profile}'."
            )
            return False

        # Build repo_map for @repo_name/... resolution
        repos, repo_errors = self._solution_controller.get_repositories()
        if repo_errors:
            self._errors.extend(repo_errors)
            return False
        repo_map = {str(r.name): str(self._work_path / r.path) for r in repos}

        try:
            resolved: Path = resolve_path(
                str(self._work_path),
                match.path,
                repo_map=repo_map,
            )
        except ValueError as exc:
            self._errors.append(str(exc))
            return False

        if not resolved.exists():
            self._errors.append(f"File not found: {resolved}")
            return False

        self._file_content = resolved.read_text(encoding="utf-8")
        self._output_data = {
            "profile": self._resolved_profile,
            "type": self._path_type,
            "name": self._path_name,
            "path": str(resolved),
            "content": self._file_content,
        }
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            click.echo(f"\n  📄  {self._path_type} · {self._path_name} [{self._resolved_profile}]\n")
            click.echo(self._file_content)

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
