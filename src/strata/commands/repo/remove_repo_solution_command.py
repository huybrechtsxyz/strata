"""Command to remove a repository from an Strata solution."""

import shutil
from pathlib import Path
from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand


class RemoveRepoSolutionCommand(BaseCommand):
    """Remove a repository entry from the current solution.

    Removes the repository from ``solution.json``.  If ``--purge`` is given,
    the local clone directory is also deleted from disk.
    """

    OPERATION = "solution_repo_remove"
    INIT_REQUIRED = True

    def __init__(
        self,
        name: str,
        purge: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._repo_name = name
        self._purge = purge
        self._removed_repo: Dict = {}

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        return True

    def _execute(self) -> bool:
        """Capture repo metadata, remove from registry, optionally purge."""
        # Capture metadata before removing so _after_execute can report it
        repos, errors = self._solution_controller.get_repositories(self._repo_name)
        if errors:
            self._errors.extend(errors)
            return False

        repo = repos[0]
        self._removed_repo = {
            "name": str(repo.name),
            "url": repo.url,
            "branch": repo.branch,
            "path": repo.path,
        }

        ok, errors = self._solution_controller.remove_repository(self._repo_name)
        if not ok:
            self._errors.extend(errors)
            return False

        ok, errors = self._solution_controller.save()
        if not ok:
            self._errors.extend(errors)
            return False

        # Regenerate the VS Code .code-workspace file to remove the repo folder
        _ws_ok, ws_errors = self._solution_controller.generate_workspace()
        if not _ws_ok:
            self.logger.warning("Could not update .code-workspace", extra={"errors": ws_errors})

        if self._purge:
            local_path = Path(str(self._work_path)) / repo.path
            if local_path.exists():
                shutil.rmtree(local_path)
                self.logger.info("Purged repository directory", path=str(local_path))
            self._removed_repo["purged"] = True
        else:
            self._removed_repo["purged"] = False

        self._output_data = {"repo": self._removed_repo}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._removed_repo:
            if self._is_console_output():
                click.echo("")
                click.echo(f"  🗑️   Removed repository:  {self._removed_repo['name']}")
                click.echo(f"      URL:    {self._removed_repo['url']}")
                click.echo(f"      Branch: {self._removed_repo['branch']}")
                click.echo(f"      Path:   {self._removed_repo['path']}")
                if self._removed_repo.get("purged"):
                    click.echo("      Folder: deleted from disk")
                click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
