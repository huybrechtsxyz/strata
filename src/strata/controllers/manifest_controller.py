"""ManifestController — orchestrates deployment manifest Layer 4 operations.

Responsibilities:
- Push generated deployment manifest files to a git remote.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List

from strata.controllers.base_controller import BaseController

if TYPE_CHECKING:
    from strata.integrations.git import GitIntegration


class ManifestController(BaseController):
    """Controller for deployment-manifest operations."""

    def __init__(self, work_path: Path) -> None:
        super().__init__()
        self._work_path = work_path

    def push_to_remote(self, paths: List[Path], remote_name: str = "origin") -> bool:
        """Stage, commit, and push manifest files to a git remote."""
        if not paths:
            return False

        from strata.integrations.factory import IntegrationFactory

        git: GitIntegration = IntegrationFactory.create_by_type("git")  # type: ignore[assignment]
        available, _ = git.ensure_available()
        if not available:
            self.logger.warning("manifest_push_git_unavailable")
            return False

        working_dir = str(self._work_path)
        relative_paths = []
        for path in paths:
            try:
                relative_paths.append(str(path.relative_to(self._work_path)))
            except ValueError:
                relative_paths.append(str(path))

        result = git.add(working_dir, relative_paths)
        if result.returncode != 0:
            self.logger.warning("manifest_push_add_failed", stderr=result.stderr)
            return False

        result = git.commit(working_dir, "chore(manifest): deployment manifest update [skip ci]")
        if result.returncode != 0:
            if "nothing to commit" in (result.stdout or "") + (result.stderr or ""):
                self.logger.debug("manifest_push_nothing_to_commit")
                return True
            self.logger.warning("manifest_push_commit_failed", stderr=result.stderr)
            return False

        result = git.push(working_dir, remote=remote_name)
        if result.returncode != 0:
            self.logger.warning("manifest_push_push_failed", stderr=result.stderr)
            return False

        return True
