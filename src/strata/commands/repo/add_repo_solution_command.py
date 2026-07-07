"""Command to add a repository to an Strata solution."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.repository_controller import RepositoryController
from strata.models.solution_model import SolutionSpecRepositoryModel

# A URL is remote when it contains a scheme (e.g. https://, ssh://) or uses git@ notation.
_REMOTE_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+\-.]*://|^git@")


def _is_local_path(url: str) -> bool:
    """Return True when *url* looks like a local filesystem path rather than a remote URL.

    Anything without a URL scheme (``<proto>://``) or ``git@`` prefix is treated
    as a local path — this covers Windows absolute paths (``C:\\...``), Unix
    absolute paths (``/...``), and relative paths (``repos/myrepo``).
    """
    return not bool(_REMOTE_URL_RE.match(url))


class AddRepoSolutionCommand(BaseCommand):
    """Register a repository entry in the current solution.

    Adds the repository to ``solution.json`` only — cloning is deferred to
    ``strata sln sync`` (not yet implemented).
    """

    OPERATION = "solution_repo_add"
    INIT_REQUIRED = True

    def __init__(
        self,
        name: str,
        url: str,
        branch: str = "main",
        path: Optional[str] = None,
        clone: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._repo_name = name
        self._repo_url = url
        self._repo_branch = branch
        # Default local path: repos/<name> relative to work_path
        self._repo_path = path if path else f"repos/{name}"
        self._clone = clone
        self._added_repo: Dict = {}
        self._clone_result: Optional[Dict[str, Any]] = None

    def get_required_integrations(self) -> Dict[str, str]:
        return {"git": "repository registration"}

    def _initialize(self, show_header: bool = True) -> bool:
        if not super()._initialize(show_header=show_header):
            return False
        self.logger.debug(
            "AddRepoSolutionCommand initializing",
            extra={
                "repo_name": self._repo_name,
                "repo_url": self._repo_url,
                "work_path": str(self._work_path),
            },
        )
        return True

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        if not self._repo_name:
            self._errors.append("Repository name is required.")
            return False
        if not self._repo_url:
            self._errors.append("Repository URL is required.")
            return False
        self.logger.debug(
            "AddRepoSolutionCommand pre-execution validated",
            extra={"repo_name": self._repo_name},
        )
        return True

    def _run(self) -> bool:
        """Register the repo in the already-loaded solution and persist."""
        is_local = _is_local_path(self._repo_url)

        if is_local:
            local_path = Path(self._repo_url)
            if not local_path.exists():
                self._errors.append(f"Local path does not exist: {self._repo_url}")
                return False
            if not local_path.is_dir():
                self._errors.append(f"Local path is not a directory: {self._repo_url}")
                return False

        repo_type = "local" if is_local else "gitops"
        repo_branch = "" if is_local else self._repo_branch

        repo = SolutionSpecRepositoryModel(
            name=self._repo_name,
            url=self._repo_url,
            path=self._repo_path,
            branch=repo_branch,
            type=repo_type,
            created=datetime.now(timezone.utc).isoformat(),
        )

        ok, errors = self._solution_controller.add_repository(repo)
        self._messages.extend(self._solution_controller.get_messages())
        self._errors.extend(errors)
        if not ok:
            return False

        ok, errors = self._solution_controller.save()
        self._errors.extend(errors)
        if not ok:
            return False

        # Regenerate the VS Code .code-workspace file to include the new repo folder
        _ws_ok, ws_errors = self._solution_controller.generate_workspace()
        if not _ws_ok:
            self.logger.warning("Could not update .code-workspace", extra={"errors": ws_errors})

        self._added_repo = {
            "name": self._repo_name,
            "url": self._repo_url,
            "path": self._repo_path,
            "branch": repo_branch,
            "type": repo_type,
        }
        self._output_data = {k: v for k, v in self._added_repo.items() if v is not None}

        if self._clone and not is_local:
            repo_controller = RepositoryController()
            _all_ok, results = repo_controller.sync_solution_repos(
                work_path=str(self._work_path),
                repos=[repo],
            )
            self._errors.extend(repo_controller.get_errors())
            self._clone_result = results[0] if results else None
            if self._clone_result and self._clone_result["status"] == "failed":
                return False

        return True

    def _after_execute(self) -> bool:
        self.logger.debug(
            "AddRepoSolutionCommand post-executing",
            extra={"repo_name": self._repo_name},
        )

        if not self._is_quiet() and self._added_repo:
            if self._is_console_output():
                click.echo("\n📦  Repository registered:")
                click.echo(f"    • Name:   {self._added_repo['name']}")
                click.echo(f"    • URL:    {self._added_repo['url']}")
                if self._added_repo["type"] != "local":
                    click.echo(f"    • Branch: {self._added_repo['branch']}")
                click.echo(f"    • Path:   {self._added_repo['path']}")
                click.echo(f"    • Type:   {self._added_repo['type']}")
                click.echo("")
                if self._added_repo["type"] == "local":
                    click.echo("💡  Local path — no sync required.")
                elif self._clone and self._clone_result:
                    r = self._clone_result
                    if r["status"] == "ok":
                        click.echo(f"✅  Cloned to {r['path']}")
                    else:
                        click.echo(f"❌  Clone failed: {r.get('error', 'unknown error')}")
                else:
                    click.echo("💡  Run 'strata repo sync' to clone the repository.")
                click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        self.logger.debug(
            "AddRepoSolutionCommand finalizing",
            extra={"repo_name": self._repo_name, "success": success},
        )
        return super()._finalize(success=success, show_footer=show_footer)
