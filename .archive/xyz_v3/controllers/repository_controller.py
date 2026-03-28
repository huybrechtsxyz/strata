#!/usr/bin/env python3
"""
===============================================================================
Script Name   : repository_controller.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Controller for managing configuration repositories.
                Orchestrates fetching and status checking of repositories
                defined in configuration.spec.repositories.
===============================================================================
"""

import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

from xyz_platform.integrations.factory import IntegrationFactory
from xyz_platform.integrations.git import GitIntegration
from xyz_platform.logger.logger import get_logger
from xyz_platform.models.integration_model import IntegrationModel
from xyz_platform.models.repository_model import RepositoryModel, RepositoryType
from xyz_platform.services.configuration_service import ConfigurationService


class RepositoryController:
    """
    Controls repository operations for configuration management.

    Orchestrates fetching repositories from various sources (gitops, bundled,
    container) into the workspace directory based on configuration.spec.repositories.

    Source routing:
    - GITOPS  : clone via GitIntegration (or pull if already on disk)
    - BUNDLED : copy from work_path with shutil
    - CONTAINER: logged as unsupported (pull/run not in scope here)
    """

    def __init__(self):
        """Initialize the repository controller."""
        self.logger = get_logger(self.__class__.__module__)
        self._config_service = ConfigurationService.get_instance()
        self._errors: List[str] = []
        self._messages: List[str] = []

    # ------------------------------------------------------------------
    # Error / message helpers
    # ------------------------------------------------------------------

    def has_errors(self) -> bool:
        """Check if any errors were accumulated."""
        return len(self._errors) > 0

    def get_errors(self) -> List[str]:
        """Get accumulated errors."""
        return self._errors.copy()

    def clear_errors(self) -> None:
        """Clear accumulated errors."""
        self._errors.clear()

    def get_messages(self) -> List[str]:
        """Get accumulated messages."""
        return self._messages.copy()

    def clear_messages(self) -> None:
        """Clear accumulated messages."""
        self._messages.clear()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_all_repositories(
        self,
        work_path: str,
        force: bool = False,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Fetch all repositories defined in configuration.

        Args:
            work_path: Working directory for resolving bundled paths
            force: If True, re-fetch even if repository already exists on disk
            progress_callback: Optional callback(repo_name, current, total)
                called before each repository fetch

        Returns:
            Tuple of (success, list of error messages)
        """
        self._errors.clear()
        self._messages.clear()

        if not self._config_service.model:
            error_msg = "No configuration loaded"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False, self._errors.copy()

        if not self._config_service.model.spec:
            error_msg = "Configuration spec not found"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False, self._errors.copy()

        repositories = self._config_service.model.spec.repositories
        if not repositories:
            self.logger.info("No repositories configured")
            return True, []

        total = len(repositories)

        self.logger.debug(
            "Fetching repositories",
            extra={
                "count": total,
                "work_path": work_path,
                "target_base_path": work_path,
                "force": force,
            },
        )

        success_count = 0

        for idx, repo in enumerate(repositories, 1):
            repo_name = repo.name or repo.repository

            if progress_callback:
                progress_callback(repo_name, idx, total)

            try:
                repo_success = self._fetch_single_repository(
                    work_path=work_path,
                    source=repo,
                    force=force,
                )

                if repo_success:
                    success_count += 1
                else:
                    error_msg = f"Failed to fetch repository: {repo_name}"
                    self._errors.append(error_msg)

            except Exception as e:
                error_msg = f"Error fetching repository {repo_name}: {str(e)}"
                self._errors.append(error_msg)
                self.logger.error(
                    "Repository fetch exception",
                    extra={
                        "repository": repo_name,
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )

        self.logger.info(
            "Repository fetch completed",
            extra={
                "success_count": success_count,
                "error_count": len(self._errors),
                "total": total,
            },
        )

        return len(self._errors) == 0, self._errors.copy()

    def get_repository_status(self, work_path: str) -> Dict[str, Dict[str, Any]]:
        """
        Get status of all configured repositories.

        Args:
            work_path: Workspace root directory where repositories are materialized

        Returns:
            Dict of repository name to status info
        """
        if not self._config_service.model:
            self.logger.warning("No configuration loaded")
            return {}

        if not self._config_service.model.spec:
            self.logger.warning("Configuration spec not found")
            return {}

        repositories = self._config_service.model.spec.repositories
        if not repositories:
            return {}

        status: Dict[str, Dict[str, Any]] = {}

        for repo in repositories:
            target_path = self._resolve_target_path(work_path=work_path, source=repo)
            repo_name = repo.name or repo.repository

            status[repo_name] = {
                "name": repo.name or "unnamed",
                "type": repo.type.value,
                "repository": repo.repository,
                "reference": repo.reference,
                "source_path": repo.source_path,
                "deploy_path": repo.deploy_path,
                "target_path": str(target_path),
                "exists": target_path.exists(),
                "is_git": (
                    (target_path / ".git").exists() if target_path.exists() else False
                ),
            }

        self.logger.debug(
            "Repository status retrieved",
            extra={"repository_count": len(status)},
        )

        return status

    def count_repositories(self) -> int:
        """
        Count total repositories in configuration.

        Returns:
            Number of repositories configured
        """
        if not self._config_service.model:
            return 0

        if not self._config_service.model.spec:
            return 0

        repositories = self._config_service.model.spec.repositories
        return len(repositories) if repositories else 0

    def validate_repositories(self, work_path: str) -> Tuple[bool, List[str]]:
        """
        Validate that all repositories exist on disk.

        Args:
            work_path: Workspace root directory where repositories are materialized

        Returns:
            Tuple of (all_exist, list of missing repository names)
        """
        status = self.get_repository_status(work_path)
        missing = [
            repo_name
            for repo_name, repo_status in status.items()
            if not repo_status["exists"]
        ]

        if missing:
            self.logger.warning(
                "Missing repositories found",
                extra={"missing_count": len(missing), "missing": missing},
            )

        return len(missing) == 0, missing

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_single_repository(
        self,
        work_path: str,
        source: RepositoryModel,
        force: bool,
    ) -> bool:
        """
        Fetch a single repository based on its type.

        Routing:
        - GITOPS  : clone (new) or pull (existing) via GitIntegration
        - BUNDLED : copy local path with shutil
        - CONTAINER: not supported at this stage — logged, skipped

        Args:
            work_path: Working directory for resolving bundled source paths
            source: RepositoryModel with repository details
            force: If True, remove existing target before re-fetching

        Returns:
            True if successful, False otherwise
        """
        target_path = self._resolve_target_path(work_path=work_path, source=source)
        repo_name = source.name or source.repository

        self.logger.debug(
            "Fetching repository",
            extra={
                "repository": repo_name,
                "type": source.type.value,
                "target_path": str(target_path),
                "force": force,
            },
        )

        if source.type == RepositoryType.GITOPS:
            return self._fetch_gitops(
                source=source,
                target_path=target_path,
                force=force,
            )

        if source.type == RepositoryType.BUNDLED:
            return self._fetch_bundled(
                work_path=work_path,
                source=source,
                target_path=target_path,
                force=force,
            )

        if source.type == RepositoryType.CONTAINER:
            self.logger.warning(
                "Container repository type is not supported by RepositoryController",
                extra={"repository": repo_name},
            )
            return True  # Non-fatal — caller decides whether to error

        self.logger.error(
            "Unknown repository type",
            extra={"repository": repo_name, "type": source.type},
        )
        return False

    def _resolve_target_path(self, work_path: str, source: RepositoryModel) -> Path:
        """
        Resolve repository target directory under workspace root.

        Resolution order:
        1. source.deploy_path
        2. source.name
        3. source.repository
        """
        target_name = source.deploy_path or source.name or source.repository
        return Path(work_path) / target_name

    def _fetch_gitops(
        self,
        source: RepositoryModel,
        target_path: Path,
        force: bool,
    ) -> bool:
        """
        Clone or pull a GitOps repository via GitIntegration.

        Args:
            source: RepositoryModel for the repository
            target_path: Resolved target directory
            force: Re-clone if target already exists

        Returns:
            True if successful, False otherwise
        """
        repo_name = source.name or source.repository

        try:
            config = IntegrationModel(
                name="git",
                type="git",
                description="Git integration for repository operations",
                validation=None,
                authentication=None,
                endpoints=None,
                lifecycle=None,
            )
            git_classes = IntegrationFactory.get_registered_types()
            git_class = git_classes.get("git")
            if not git_class:
                error_msg = "Git integration is not registered"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            git = cast(GitIntegration, git_class(config=config))

            available, error = git.ensure_available()
            if not available:
                error_msg = f"Git is not available: {error}"
                self.logger.error(
                    error_msg,
                    extra={"repository": repo_name},
                )
                self._errors.append(error_msg)
                return False

            git_dir = target_path / ".git"

            # Force: remove existing directory before re-cloning
            if force and target_path.exists():
                self.logger.debug(
                    "Removing existing repository for force re-fetch",
                    extra={"repository": repo_name, "target_path": str(target_path)},
                )
                shutil.rmtree(target_path, ignore_errors=True)

            if target_path.exists() and git_dir.exists():
                # Repository already checked out — pull updates
                self.logger.debug(
                    "Repository exists, pulling updates",
                    extra={"repository": repo_name, "target_path": str(target_path)},
                )
                result = git.pull(
                    working_dir=str(target_path),
                    branch=source.reference,
                )
                if result.returncode != 0:
                    error_msg = (
                        f"Git pull failed for '{repo_name}': {result.stderr.strip()}"
                    )
                    self.logger.error(error_msg)
                    self._errors.append(error_msg)
                    return False

            else:
                # Clone fresh
                target_path.parent.mkdir(parents=True, exist_ok=True)
                self.logger.debug(
                    "Cloning repository",
                    extra={
                        "repository": repo_name,
                        "url": source.repository,
                        "reference": source.reference,
                        "target_path": str(target_path),
                    },
                )
                result = git.clone(
                    repo_url=source.repository,
                    target_dir=str(target_path),
                    branch=source.reference,
                )
                if result.returncode != 0:
                    error_msg = (
                        f"Git clone failed for '{repo_name}': {result.stderr.strip()}"
                    )
                    self.logger.error(error_msg)
                    self._errors.append(error_msg)
                    return False

            self.logger.debug(
                "GitOps repository fetched successfully",
                extra={"repository": repo_name, "path": str(target_path)},
            )
            return True

        except Exception as e:
            error_msg = f"GitOps fetch failed for '{repo_name}': {str(e)}"
            self.logger.error(
                error_msg,
                extra={"repository": repo_name, "error_type": type(e).__name__},
                exc_info=True,
            )
            self._errors.append(error_msg)
            return False

    def _fetch_bundled(
        self,
        work_path: str,
        source: RepositoryModel,
        target_path: Path,
        force: bool,
    ) -> bool:
        """
        Copy a bundled (local) repository to the workspace directory.

        Args:
            work_path: Working directory used to resolve the relative source path
            source: RepositoryModel for the repository
            target_path: Resolved target directory
            force: Overwrite target if it already exists

        Returns:
            True if successful, False otherwise
        """
        repo_name = source.name or source.repository

        try:
            # Resolve bundled source adaptively.
            # Primary: relative to work_path (typically .app)
            # Fallback: relative to parent of work_path (monorepo/project root)
            workspace_root = Path(work_path)
            repo_ref = source.repository or "."

            base_candidates = []
            if Path(repo_ref).is_absolute():
                base_candidates.append(Path(repo_ref))
            elif repo_ref in (".", "/"):
                base_candidates.extend([workspace_root, workspace_root.parent])
            else:
                base_candidates.extend(
                    [workspace_root / repo_ref, workspace_root.parent / repo_ref]
                )

            source_candidates = []
            for base in base_candidates:
                candidate = base / source.source_path if source.source_path else base
                source_candidates.append(candidate)

            source_dir = next((p for p in source_candidates if p.exists()), None)

            if source_dir is None:
                error_msg = (
                    f"Bundled source path does not exist for '{repo_name}': "
                    f"{source_candidates[0]}"
                )
                if len(source_candidates) > 1:
                    tried = ", ".join(str(p) for p in source_candidates)
                    error_msg = f"{error_msg}. Tried: {tried}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            if target_path.exists():
                if not force:
                    self.logger.debug(
                        "Bundled repository already present, skipping",
                        extra={
                            "repository": repo_name,
                            "target_path": str(target_path),
                        },
                    )
                    return True
                shutil.rmtree(target_path, ignore_errors=True)

            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(source_dir), str(target_path))

            self.logger.debug(
                "Bundled repository copied successfully",
                extra={
                    "repository": repo_name,
                    "source": str(source_dir),
                    "target": str(target_path),
                },
            )
            return True

        except Exception as e:
            error_msg = f"Bundled copy failed for '{repo_name}': {str(e)}"
            self.logger.error(
                error_msg,
                extra={"repository": repo_name, "error_type": type(e).__name__},
                exc_info=True,
            )
            self._errors.append(error_msg)
            return False
