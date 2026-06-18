"""Controller for managing and fetching configuration remotes."""

import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

from strata.controllers.base_controller import BaseController
from strata.integrations.factory import IntegrationFactory
from strata.integrations.git import GitIntegration
from strata.models.integration_model import IntegrationModel
from strata.models.repository_model import RemoteModel, RemoteType
from strata.models.solution_model import SolutionSpecRepositoryModel
from strata.services.configuration_service import ConfigurationService


class RepositoryController(BaseController):
    """
    Controls remote operations for configuration management.

    Orchestrates fetching remotes from various sources (gitops, bundled,
    container) into the workspace directory based on configuration.spec.remotes.

    Source routing:
    - GITOPS  : clone via GitIntegration (or pull if already on disk)
    - BUNDLED : copy from work_path with shutil
    - CONTAINER: logged as unsupported (pull/run not in scope here)
    """

    def __init__(self) -> None:
        """Initialize the repository controller."""
        super().__init__()
        self._config_service_instance: Optional[ConfigurationService] = None

    @property
    def _config_service(self) -> ConfigurationService:
        """Lazy accessor — only instantiated when config-layer methods need it."""
        if self._config_service_instance is None:
            self._config_service_instance = ConfigurationService.get_instance()
        return self._config_service_instance

    # Public API

    def fetch_all_repositories(
        self,
        work_path: str,
        force: bool = False,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Fetch all remotes defined in configuration.

        Args:
            work_path: Working directory for resolving bundled paths
            force: If True, re-fetch even if remote already exists on disk
            progress_callback: Optional callback(remote_name, current, total)
                called before each remote fetch

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

        repositories = self._config_service.model.spec.remotes
        if not repositories:
            self.logger.info("No remotes configured")
            return True, []

        total = len(repositories)

        self.logger.debug(
            "Fetching repositories",
            count=total,
            work_path=work_path,
            force=force,
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
                    repository=repo_name,
                    error_type=type(e).__name__,
                    exc_info=True,
                )

        self.logger.info(
            "Repository fetch completed",
            success_count=success_count,
            error_count=len(self._errors),
            total=total,
        )

        return len(self._errors) == 0, self._errors.copy()

    def get_repository_status(self, work_path: str) -> Dict[str, Dict[str, Any]]:
        """
        Get status of all configured remotes.

        Args:
            work_path: Workspace root directory where remotes are materialized

        Returns:
            Dict of remote name to status info
        """
        if not self._config_service.model:
            self.logger.warning("No configuration loaded")
            return {}

        if not self._config_service.model.spec:
            self.logger.warning("Configuration spec not found")
            return {}

        remotes = self._config_service.model.spec.remotes
        if not remotes:
            return {}

        status: Dict[str, Dict[str, Any]] = {}

        for remote in remotes:
            target_path = self._resolve_target_path(work_path=work_path, source=remote)
            remote_name = remote.name or remote.repository

            status[remote_name] = {
                "name": remote.name or "unnamed",
                "type": remote.type.value,
                "repository": remote.repository,
                "reference": remote.reference,
                "source_path": remote.source_path,
                "deploy_path": remote.deploy_path,
                "target_path": str(target_path),
                "exists": target_path.exists(),
                "is_git": ((target_path / ".git").exists() if target_path.exists() else False),
            }

        self.logger.debug(
            "Remote status retrieved",
            remote_count=len(status),
        )

        return status

    def count_repositories(self) -> int:
        """
        Count total remotes in configuration.

        Returns:
            Number of remotes configured
        """
        if not self._config_service.model:
            return 0

        if not self._config_service.model.spec:
            return 0

        remotes = self._config_service.model.spec.remotes
        return len(remotes) if remotes else 0

    def validate_repositories(self, work_path: str) -> Tuple[bool, List[str]]:
        """
        Validate that all remotes exist on disk.

        Args:
            work_path: Workspace root directory where remotes are materialized

        Returns:
            Tuple of (all_exist, list of missing repository names)
        """
        status = self.get_repository_status(work_path)
        missing = [repo_name for repo_name, repo_status in status.items() if not repo_status["exists"]]

        if missing:
            self.logger.warning(
                "Missing repositories found",
                missing_count=len(missing),
                missing=missing,
            )

        return len(missing) == 0, missing

    def sync_solution_repos(
        self,
        work_path: str,
        repos: List[SolutionSpecRepositoryModel],
        force: bool = False,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """Clone or pull repositories registered in a solution.

        For each repo: clones if the local path has no ``.git/``, otherwise
        pulls the tracked branch.  A dirty working tree without ``force``
        is skipped (not an error).

        Args:
            work_path: Workspace root (repo paths resolved relative to it).
            repos: Solution repository entries to sync.
            force: When True, pull even over dirty trees.

        Returns:
            ``(all_ok, results)`` where *results* is a list of per-repo dicts::

                {name, url, path, branch, action, status, error}
        """
        self._errors.clear()
        self._messages.clear()

        results: List[Dict[str, Any]] = []
        git: Optional[GitIntegration] = None  # initialised lazily — only when a gitops repo is encountered

        for repo in repos:
            if repo.type == "local":
                local_path = Path(repo.url)
                if local_path.exists() and local_path.is_dir():
                    status: str = "ok"
                    error: Optional[str] = None
                else:
                    status = "missing"
                    error = f"Local path not found: {repo.url}"
                    self._errors.append(error)
                results.append(
                    {
                        "name": str(repo.name),
                        "url": repo.url,
                        "path": str(local_path),
                        "branch": repo.branch,
                        "action": "local",
                        "status": status,
                        "error": error,
                    }
                )
                continue

            # gitops repo — initialise git integration on first use
            if git is None:
                git = self._get_git_integration()
                if git is None:
                    return False, results

            abs_path = Path(work_path) / repo.path
            result = self._clone_or_pull(
                git=git,
                name=str(repo.name),
                url=repo.url,
                branch=repo.branch,
                target_path=abs_path,
                force=force,
            )
            results.append(result)
            if result["status"] == "failed":
                self._errors.append(result["error"] or f"Sync failed for '{repo.name}'")

        all_ok = len(self._errors) == 0
        self.logger.info(
            "Solution repository sync completed",
            total=len(repos),
            failed=len(self._errors),
        )
        return all_ok, results

    # Private helpers

    def _fetch_single_repository(
        self,
        work_path: str,
        source: RemoteModel,
        force: bool,
    ) -> bool:
        """Fetch a single remote based on its type."""
        target_path = self._resolve_target_path(work_path=work_path, source=source)
        repo_name = source.name or source.repository

        self.logger.debug(
            "Fetching remote",
            remote=repo_name,
            type=source.type.value,
            target_path=str(target_path),
            force=force,
        )

        if source.type == RemoteType.GITOPS:
            return self._fetch_gitops(source=source, target_path=target_path, force=force)

        if source.type == RemoteType.BUNDLED:
            return self._fetch_bundled(work_path=work_path, source=source, target_path=target_path, force=force)

        if source.type == RemoteType.CONTAINER:
            self.logger.warning("Container remote type not supported", remote=repo_name)
            return True

        self.logger.error("Unknown remote type", remote=repo_name, type=source.type)
        return False

    def _resolve_target_path(self, work_path: str, source: RemoteModel) -> Path:
        """Resolve remote target directory under workspace root."""
        target_name = source.deploy_path or source.name or source.repository
        return Path(work_path) / target_name

    def _get_git_integration(self) -> Optional[GitIntegration]:
        """Build and validate a GitIntegration instance.

        Returns the instance on success, or None (with an error appended) on failure.
        """
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
                self._errors.append("Git integration is not registered")
                self.logger.error("Git integration is not registered")
                return None

            git = cast(GitIntegration, git_class(config=config))
            available, error = git.ensure_available()
            if not available:
                self._errors.append(f"Git is not available: {error}")
                self.logger.error("Git is not available", error=error)
                return None

            return git

        except Exception as e:
            self._errors.append(f"Failed to initialise Git integration: {e}")
            self.logger.error("Failed to initialise Git integration", exc_info=True)
            return None

    def _clone_or_pull(
        self,
        git: GitIntegration,
        name: str,
        url: str,
        branch: Optional[str],
        target_path: Path,
        force: bool,
    ) -> Dict[str, Any]:
        """Clone or pull a single git repository.

        Shared logic used by both configuration-layer fetch and solution-layer sync.

        Returns a per-repo result dict with keys:
        ``name, url, path, branch, action, status, error``.
        """
        git_dir = target_path / ".git"

        entry: Dict[str, Any] = {
            "name": name,
            "url": url,
            "path": str(target_path),
            "branch": branch,
            "action": None,
            "status": "ok",
            "error": None,
        }

        try:
            if target_path.exists() and git_dir.exists():
                # Repo already on disk — check for dirty tree
                status_result = git._run_integration(["status", "--porcelain"], cwd=str(target_path), timeout=30)
                is_dirty = status_result.returncode == 0 and bool(status_result.stdout.strip())

                if is_dirty and not force:
                    self.logger.warning(
                        "Skipping dirty repository (use --force to override)",
                        repo=name,
                        path=str(target_path),
                    )
                    entry["action"] = "skipped"
                    entry["error"] = "Working tree is dirty — use --force to override"
                    return entry

                result = git.pull(working_dir=str(target_path), branch=branch)
                entry["action"] = "pull"
                if result.returncode != 0:
                    entry["status"] = "failed"
                    entry["error"] = result.stderr.strip() or "git pull failed"
                    self.logger.error("Git pull failed", repo=name, stderr=result.stderr.strip())
            else:
                # Clone fresh
                target_path.parent.mkdir(parents=True, exist_ok=True)
                result = git.clone(
                    repo_url=url,
                    target_dir=str(target_path),
                    branch=branch,
                )
                entry["action"] = "clone"
                if result.returncode != 0:
                    entry["status"] = "failed"
                    entry["error"] = result.stderr.strip() or "git clone failed"
                    self.logger.error("Git clone failed", repo=name, url=url, stderr=result.stderr.strip())

        except Exception as e:
            entry["action"] = entry.get("action") or "unknown"
            entry["status"] = "failed"
            entry["error"] = str(e)
            self.logger.error("Sync exception", repo=name, error_type=type(e).__name__, exc_info=True)

        return entry

    def _fetch_gitops(
        self,
        source: RemoteModel,
        target_path: Path,
        force: bool,
    ) -> bool:
        """Clone or pull a GitOps remote via GitIntegration."""
        repo_name = source.name or source.repository

        git = self._get_git_integration()
        if git is None:
            return False

        # Force: remove existing directory before re-cloning
        if force and target_path.exists():
            self.logger.debug(
                "Removing existing repository for force re-fetch",
                repository=repo_name,
                target_path=str(target_path),
            )
            shutil.rmtree(target_path, ignore_errors=True)

        result = self._clone_or_pull(
            git=git,
            name=str(repo_name),
            url=source.repository,
            branch=source.reference,
            target_path=target_path,
            force=force,
        )

        if result["status"] == "failed":
            error_msg = result["error"] or f"GitOps fetch failed for '{repo_name}'"
            self._errors.append(error_msg)
            return False

        self.logger.debug(
            "GitOps repository fetched successfully",
            repository=repo_name,
            path=str(target_path),
        )
        return True

    def _fetch_bundled(
        self,
        work_path: str,
        source: RemoteModel,
        target_path: Path,
        force: bool,
    ) -> bool:
        """
        Copy a bundled (local) remote to the workspace directory.

        Args:
            work_path: Working directory used to resolve the relative source path
            source: RemoteModel for the remote
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
                base_candidates.extend([workspace_root / repo_ref, workspace_root.parent / repo_ref])

            source_candidates = []
            for base in base_candidates:
                candidate = base / source.source_path if source.source_path else base
                source_candidates.append(candidate)

            source_dir = next((p for p in source_candidates if p.exists()), None)

            if source_dir is None:
                error_msg = f"Bundled source path does not exist for '{repo_name}': {source_candidates[0]}"
                if len(source_candidates) > 1:
                    tried = ", ".join(str(p) for p in source_candidates)
                    error_msg = f"{error_msg}. Tried: {tried}"
                self.logger.error(
                    "Bundled source path does not exist",
                    repository=repo_name,
                    path=str(source_candidates[0]),
                    tried=[str(p) for p in source_candidates],
                )
                self._errors.append(error_msg)
                return False

            if target_path.exists():
                if not force:
                    self.logger.debug(
                        "Bundled repository already present, skipping",
                        repository=repo_name,
                        target_path=str(target_path),
                    )
                    return True
                shutil.rmtree(target_path, ignore_errors=True)

            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(source_dir), str(target_path))

            self.logger.debug(
                "Bundled repository copied successfully",
                repository=repo_name,
                source=str(source_dir),
                target=str(target_path),
            )
            return True

        except Exception as e:
            error_msg = f"Bundled copy failed for '{repo_name}': {str(e)}"
            self.logger.error(
                "Bundled copy failed",
                repository=repo_name,
                error_type=type(e).__name__,
                exc_info=True,
            )
            self._errors.append(error_msg)
            return False
