"""Command to show git state for all registered repositories."""

from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.integrations.factory import IntegrationFactory
from xyz_platform.integrations.git import GitIntegration
from xyz_platform.models.integration_model import IntegrationModel


class StatusRepoSolutionCommand(BaseCommand):
    """Show git working-tree state for all (or one) registered repositories.

    For each repo that is present on disk it reports:

    - Current branch and tracking remote
    - Ahead / behind counts vs. the remote
    - Staged, unstaged, untracked, and conflicted file counts
    - A clean / dirty summary flag

    Repos that are not yet cloned show as ``not cloned``.

    Pass ``--name`` to inspect a single repository.
    Pass ``--verbose`` to list individual changed files.
    """

    OPERATION = "solution_repo_status"
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

    def get_required_integrations(self) -> Dict[str, str]:
        return {"git": "repository status"}

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

            if not self._run_execution():
                if self._is_console_output():
                    click.echo("\n❌  Execution failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                self._finalize(success=False)
                return False

            self._finalize(success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Failed to get repo status: {exc}")
            self.logger.exception("repo status failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------

    def _run_execution(self) -> bool:
        repos, errors = self._solution_controller.get_repositories(self._filter_name)
        if errors:
            self._errors.extend(errors)
            return False
        if not repos:
            self._errors.append("No repositories registered in solution.")
            return False

        git = self._get_git_integration()
        if git is None:
            return False

        results: List[Dict[str, Any]] = []
        for repo in repos:
            local_path = Path(repo.path)
            if not local_path.is_absolute():
                local_path = self._work_path / local_path

            entry: Dict[str, Any] = {
                "name": str(repo.name),
                "path": str(local_path),
                "url": repo.url,
                "branch_registered": repo.branch,
            }

            if not local_path.exists() or not (local_path / ".git").exists():
                entry["state"] = "not_cloned"
            else:
                ok, status = git.status(str(local_path))
                if not ok:
                    entry["state"] = "error"
                else:
                    remote_url = git.get_remote_url(str(local_path))
                    entry.update(
                        {
                            "state": "clean" if status.is_clean else "dirty",
                            "branch": status.branch,
                            "tracking": status.tracking,
                            "ahead": status.ahead,
                            "behind": status.behind,
                            "staged": status.staged,
                            "unstaged": status.unstaged,
                            "untracked": status.untracked,
                            "conflicted": status.conflicted,
                            "remote_url": remote_url,
                        }
                    )

            results.append(entry)

        self._output_data = {"repos": results}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            self._print_results(self._output_data.get("repos", []))
        return True

    # -------------------------------------------------------------------------
    # Console output
    # -------------------------------------------------------------------------

    def _print_results(self, results: List[Dict[str, Any]]) -> None:
        click.echo("")
        for r in results:
            state = r.get("state", "?")

            if state == "not_cloned":
                click.echo(f"  ⚪  {r['name']}  (not cloned — run `xyz repo sync`)")
                continue

            if state == "error":
                click.echo(f"  ❓  {r['name']}  (could not read git status)")
                continue

            icon = "✅" if state == "clean" else "⚠️ "
            branch = r.get("branch") or r.get("branch_registered") or "?"
            tracking = r.get("tracking")
            ahead = r.get("ahead", 0)
            behind = r.get("behind", 0)

            # Build the branch/tracking string
            track_str = ""
            if tracking:
                parts = []
                if ahead:
                    parts.append(f"↑{ahead}")
                if behind:
                    parts.append(f"↓{behind}")
                track_str = f"  →  {tracking}"
                if parts:
                    track_str += f"  [{', '.join(parts)}]"

            staged = len(r.get("staged", []))
            unstaged = len(r.get("unstaged", []))
            untracked = len(r.get("untracked", []))
            conflicted = len(r.get("conflicted", []))

            click.echo(f"  {icon}  {r['name']}  [{branch}{track_str}]")

            if state == "dirty":
                parts = []
                if staged:
                    parts.append(f"{staged} staged")
                if unstaged:
                    parts.append(f"{unstaged} unstaged")
                if untracked:
                    parts.append(f"{untracked} untracked")
                if conflicted:
                    parts.append(f"{conflicted} conflicted")
                click.echo(f"       {', '.join(parts)}")

                if self._output_verbose:
                    for f in r.get("staged", []):
                        click.echo(f"         S  {f}")
                    for f in r.get("unstaged", []):
                        click.echo(f"         M  {f}")
                    for f in r.get("untracked", []):
                        click.echo(f"         ?  {f}")
                    for f in r.get("conflicted", []):
                        click.echo(f"         !  {f}")

        click.echo("")

    # -------------------------------------------------------------------------
    # Git integration factory (mirrors RepositoryController pattern)
    # -------------------------------------------------------------------------

    def _get_git_integration(self) -> Optional[GitIntegration]:
        try:
            config = IntegrationModel(
                name="git",
                type="git",
                description="Git integration for repository status",
                validation=None,
                authentication=None,
                endpoints=None,
                lifecycle=None,
            )
            git_classes = IntegrationFactory.get_registered_types()
            git_class = git_classes.get("git")
            if not git_class:
                self._errors.append("Git integration is not registered")
                return None

            git = cast(GitIntegration, git_class(config=config))
            available, error = git.ensure_available()
            if not available:
                self._errors.append(f"Git is not available: {error}")
                return None

            return git

        except Exception as exc:
            self._errors.append(f"Failed to initialise Git integration: {exc}")
            self.logger.error("Failed to initialise Git integration", exc_info=True)
            return None
