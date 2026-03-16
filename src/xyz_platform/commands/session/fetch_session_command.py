#!/usr/bin/env python3
"""
===============================================================================
Script Name   : fetch_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to fetch all repositories declared in the merged
                platform configuration into the session workspace.
===============================================================================
"""

from pathlib import Path
from typing import Dict, List, Optional

import click

from xyz_platform.commands.session.base_session_command import BaseSessionCommand


class FetchSessionCommand(BaseSessionCommand):
    """
    Fetch all repositories declared in the merged platform configuration.

    Workflow:
    1. Re-merge all session config sources → .xyz-platform/configuration.yaml
    2. Load that config into ConfigurationService
    3. Call RepositoryController.fetch_all_repositories()
    4. Record each fetched repo in session.json

    Idempotent by default — already-present repos are skipped.
    Use --force to re-fetch.
    """

    def __init__(
        self,
        force: bool = False,
        dry_run: bool = False,
        name: Optional[str] = None,
        work_path: Optional[str] = None,
        output: str = None,
        verbose: bool = None,
        quiet: bool = None,
    ):
        """
        Initialize the fetch command.

        Args:
            force: Re-fetch even if repo already exists on disk
            dry_run: List what would be fetched without touching disk
            name: Fetch only the repo with this name (from spec.repositories)
            work_path: Root working directory
            output: Output format
            verbose: Enable verbose output
            quiet: Disable all console output
        """
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
            require_session=True,
        )
        self._force = force
        self._dry_run = dry_run
        self._filter_name = name
        self._fetch_results: List[Dict] = []

    def execute(self) -> bool:
        """Execute the fetch command."""
        try:
            if not self._initialize(operation="session_fetch"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="session_fetch", success=False)
                return False

            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="session_fetch", success=False)
                return False

            # Step 1: Re-merge config sources → configuration.yaml
            merge_success, merge_errors = self._session_controller.merge_config_and_save(
                work_path=self._work_path
            )
            self._errors.extend(self._session_controller.get_errors())
            self._messages.extend(self._session_controller.get_messages())

            if not merge_success:
                if self._is_console_output():
                    click.echo("\n❌  Failed to merge configuration")
                self._finalize(operation="session_fetch", success=False)
                return False

            # Step 2: Load the merged config into ConfigurationService
            merged_config_file = self._work_path / ".xyz-platform" / "configuration.yaml"
            if not merged_config_file.exists():
                if self._is_console_output():
                    click.echo(
                        "\n⚠️   No configuration.yaml found — nothing to fetch.\n"
                        "    Register a config source first:\n"
                        "      xyz session add --config-file <path>"
                    )
                self._finalize(operation="session_fetch", success=True)
                return True

            from xyz_platform.controllers.workspace_controller import WorkspaceController
            from xyz_platform.services.configuration_service import ConfigurationService

            workspace_controller = WorkspaceController()
            load_success, load_errors = workspace_controller.load_configuration(
                work_path=self._work_path,
                file_paths=[str(merged_config_file)],
            )
            if not load_success:
                self._errors.extend(load_errors)
                if self._is_console_output():
                    click.echo("\n❌  Failed to load merged configuration")
                self._finalize(operation="session_fetch", success=False)
                return False

            # Step 3: Fetch repositories via RepositoryController
            config_service = ConfigurationService.get_instance()
            if (
                not config_service.model
                or not config_service.model.spec
                or not config_service.model.spec.repositories
            ):
                if self._is_console_output():
                    click.echo("\n⚠️   No repositories declared in configuration — nothing to fetch.")
                self._finalize(operation="session_fetch", success=True)
                return True

            repositories = config_service.model.spec.repositories

            # Apply single-name filter
            if self._filter_name:
                repositories = [
                    r for r in repositories
                    if (r.name or r.repository) == self._filter_name
                ]
                if not repositories:
                    error_msg = f"No repository named '{self._filter_name}' found in configuration"
                    self.logger.error(error_msg)
                    self._errors.append(error_msg)
                    self._finalize(operation="session_fetch", success=False)
                    return False

            if self._dry_run:
                if self._is_console_output():
                    click.echo(f"\n🔍  Dry run — {len(repositories)} repository(s) would be fetched:")
                    for repo in repositories:
                        name = repo.name or repo.repository
                        click.echo(f"    • {name}  [{repo.type.value}]"
                                   f"  → {self._work_path / repo.deploy_path}"
                                   if repo.deploy_path else f"    • {name}  [{repo.type.value}]")
                    click.echo("")
                self._fetch_results = [
                    {"name": r.name or r.repository, "type": r.type.value, "status": "dry_run"}
                    for r in repositories
                ]
                self._finalize(operation="session_fetch", success=True)
                return True

            # Actual fetch
            from xyz_platform.controllers.repository_controller import RepositoryController

            repo_controller = RepositoryController()
            build_path = workspace_controller.get_workspace_buildpath(self._work_path)

            if self._is_console_output():
                click.echo(f"\n📥  Fetching {len(repositories)} repository(s)...")

            def _progress(repo_name: str, current: int, total: int) -> None:
                if self._is_console_output():
                    click.echo(f"    [{current}/{total}]  {repo_name}")

            fetch_success, fetch_errors = repo_controller.fetch_all_repositories(
                work_path=str(self._work_path),
                build_path=str(build_path),
                force=self._force,
                progress_callback=_progress,
            )

            self._errors.extend(fetch_errors)
            self._messages.extend(repo_controller.get_messages())

            # Step 4: Record fetched repos in session state
            for repo in repositories:
                repo_name = repo.name or repo.repository
                deploy_path = repo.deploy_path or repo_name
                status = "ok" if repo_name not in [e.split(":")[0] for e in fetch_errors] else "error"
                self._fetch_results.append(
                    {
                        "name": repo_name,
                        "type": repo.type.value,
                        "path": deploy_path,
                        "status": status,
                    }
                )
                # Record in session repositories if not already present
                if status == "ok":
                    self._session_controller._update_session_repositories(
                        work_path=self._work_path,
                        repo_metadata={
                            "name": repo_name,
                            "url": repo.repository,
                            "path": deploy_path,
                            "type": repo.type.value,
                            "branch": repo.reference if repo.type.value == "gitops" else None,
                        },
                    )
                    # Clear duplicate-exists errors — idempotent fetch is expected
                    self._session_controller.clear_errors()

            if not self._after_execute():
                self.logger.error(f"Post-execution hook failed in {self.__class__.__name__}")
                self._finalize(operation="session_fetch", success=False)
                return False

            if not self._finalize(operation="session_fetch", success=fetch_success):
                return False

            return fetch_success

        except Exception as e:
            error_msg = f"Failed to fetch repositories: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(operation="session_fetch", success=False)
            return False

    def _initialize(self, operation: str = None) -> bool:
        if not super()._initialize(operation=operation):
            return False
        self.logger.debug(
            "Fetch command initialized",
            extra={
                "command_class": self.__class__.__name__,
                "force": self._force,
                "dry_run": self._dry_run,
                "filter_name": self._filter_name,
            },
        )
        return True

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        return True

    def _after_execute(self) -> bool:
        ok_count = sum(1 for r in self._fetch_results if r.get("status") == "ok")
        err_count = sum(1 for r in self._fetch_results if r.get("status") == "error")

        self._output_data = {
            "repositories": self._fetch_results,
            "summary": {
                "total": len(self._fetch_results),
                "ok": ok_count,
                "errors": err_count,
                "force": self._force,
                "dry_run": self._dry_run,
            },
        }

        if self._is_console_output() and self._fetch_results:
            click.echo(f"\n✅  Fetched {ok_count} / {len(self._fetch_results)} repository(s)")
            if err_count:
                click.echo(f"    ❌ {err_count} failed — see errors above")
            click.echo("")

        return super()._after_execute()

    def _finalize(self, operation: str = None, success: bool = None, show_footer: bool = True) -> bool:
        self.logger.debug(
            "Fetch command finalized",
            extra={"command_class": self.__class__.__name__},
        )
        return super()._finalize(operation=operation, success=success)
