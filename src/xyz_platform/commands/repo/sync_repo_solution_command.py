"""Command to sync (clone or pull) repositories in an XYZ Platform solution."""

from typing import Dict, List, Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.controllers.repository_controller import RepositoryController


class SyncRepoSolutionCommand(BaseCommand):
    """Clone or pull all (or one) repositories registered in the solution.

    For each repository:
    - If the local path does not exist or has no ``.git/``: clones from the URL.
    - If it already exists: pulls the tracked branch.
    - If the working tree is dirty and ``--force`` is not set: skips with a warning.

    Partial failures are accumulated — the command always attempts every
    repository before reporting results.
    """

    OPERATION = "solution_repo_sync"
    INIT_REQUIRED = True

    def __init__(
        self,
        name: Optional[str] = None,
        force: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._filter_name = name
        self._force = force
        self._sync_results: List[Dict] = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {"git": "repository sync"}

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
            error_msg = f"Failed to sync repositories: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        return True

    def _run_execution(self) -> bool:
        """Resolve repos to sync, delegate to RepositoryController."""
        # Step 1: SolutionController provides repos from the already-loaded solution
        repos, errors = self._solution_controller.get_repositories(self._filter_name)
        if errors:
            self._errors.extend(errors)
            return False
        if not repos:
            self._errors.append("No repositories registered in solution.")
            return False

        # Step 2: RepositoryController performs the git operations
        repo_controller = RepositoryController()
        all_ok, results = repo_controller.sync_solution_repos(
            work_path=str(self._work_path),
            repos=repos,
            force=self._force,
        )

        self._sync_results = results
        self._errors.extend(repo_controller.get_errors())

        self._output_data = {"repos": results}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._sync_results:
            if self._is_console_output():
                click.echo("")
                for r in self._sync_results:
                    if r["status"] == "ok":
                        if r["action"] == "clone":
                            icon = "✅"
                        elif r["action"] == "local":
                            icon = "📁"
                        else:
                            icon = "🔄"
                        click.echo(f"  {icon}  {r['name']}  ({r['action']})  →  {r['path']}")
                    elif r["action"] == "skipped":
                        click.echo(f"  ⚠️   {r['name']}  (skipped — dirty tree)")
                    elif r["status"] == "missing":
                        click.echo(f"  ❌  {r['name']}  (missing)  {r['error']}")
                    else:
                        click.echo(f"  ❌  {r['name']}  (failed)  {r['error']}")

                ok_count = sum(1 for r in self._sync_results if r["status"] == "ok")
                skip_count = sum(1 for r in self._sync_results if r["action"] == "skipped")
                fail_count = sum(1 for r in self._sync_results if r["status"] in ("failed", "missing"))
                total = len(self._sync_results)

                click.echo("")
                parts = [f"{ok_count}/{total} synced"]
                if skip_count:
                    parts.append(f"{skip_count} skipped")
                if fail_count:
                    parts.append(f"{fail_count} failed")
                click.echo("  " + ", ".join(parts))
                click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
