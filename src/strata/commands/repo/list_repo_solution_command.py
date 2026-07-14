"""Command to list repositories registered in an Strata solution."""

from typing import Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand


class ListRepoSolutionCommand(BaseCommand):
    """List repositories registered in the current solution.

    Prints all entries from ``solution.json``, showing name, URL, branch,
    and local path.  Pass ``--name`` to filter to a single repository.
    """

    OPERATION = "solution_repo_list"
    INIT_REQUIRED = True

    def __init__(
        self,
        name: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._filter_name = name
        self._repos: List[Dict] = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        return True

    def _execute(self) -> bool:
        """Read repositories from the already-loaded solution."""
        repos, errors = self._solution_controller.get_repositories(self._filter_name)
        if errors:
            self._errors.extend(errors)
            return False

        self._repos = [
            {
                "name": str(r.name),
                "url": r.url,
                "branch": r.branch,
                "path": r.path,
                "type": r.type,
            }
            for r in repos
        ]
        self._output_data = {"repositories": self._repos}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            if not self._repos:
                click.echo("\n  📋  No repositories registered in solution.\n")
            else:
                click.echo(f"\n  📋  Repositories ({len(self._repos)}):\n")
                for r in self._repos:
                    click.echo(f"    • {r['name']}")
                    click.echo(f"      URL:    {r['url']}")
                    click.echo(f"      Branch: {r['branch']}")
                    click.echo(f"      Path:   {r['path']}")
                click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
