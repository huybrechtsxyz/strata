"""Git integration for repository operations."""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.integrations.capabilities import IRepositoryTool
from xyz_platform.logger import get_logger
from xyz_platform.models.integration_model import IntegrationModel
from xyz_platform.utils.system import CommandResult

logger = get_logger(__name__)


class GitIntegration(BaseIntegration):
    """
    Git integration for repository operations.

    Implements singleton pattern per config - multiple instances possible
    for different Git configurations (e.g., different credentials, proxies).
    """

    # Command executable name
    COMMAND = "git"

    # Declare supported capabilities
    CAPABILITIES = [IRepositoryTool]

    # Singleton instance keying based on config name
    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """
        Get instance key based on integration name.

        Creates separate singleton instances per configuration.

        Args:
            class_ref: The class being instantiated
            *args: Constructor positional arguments
            **kwargs: Constructor keyword arguments

        Returns:
            Integration name or "default"
        """
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"

        return config.name or "default"

    # Initializer

    def __init__(self, config: IntegrationModel):
        """
        Initialize Git integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        logger.debug(
            "Git integration initialized",
            name=self.integration_name,
        )

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve git version."""
        return [self.command, "--version"]

    def parse_version(self, version_output: str) -> str:
        """
        Parse version string from git output.

        Args:
            version_output: Raw output (e.g., "git version 2.40.0")

        Returns:
            Version string (e.g., "2.40.0")
        """
        # Extract version number from "git version X.Y.Z"
        match = re.search(r"(\d+\.\d+\.\d+)", version_output)
        if match:
            return match.group(1)
        return version_output.strip()

    def get_setup_info(self) -> dict:
        """Return setup metadata for git."""
        return {
            "name": "git",
            "command": "git",
            "install_url": "https://git-scm.com/downloads",
            "env_vars": [],
            "auth_methods": [
                {
                    "method": "SSH keys",
                    "description": "Add SSH key to remote (e.g. GitHub/GitLab). No env vars needed.",
                },
                {
                    "method": "HTTPS credentials",
                    "description": "Git credential helper or personal access token in remote URL.",
                },
            ],
            "yaml_example": None,
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure integration is available.

        Returns:
            Tuple of (success, error_message)
        """
        # Check integration availability
        if not self.is_available():
            self._info = f"{self.integration_name} CLI is not installed or not in PATH."
            logger.warning(
                "Git CLI not found",
                name=self.integration_name,
            )
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. "
                f"Install Git from: https://git-scm.com/downloads",
            )

        # Validate version requirements
        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "Git version validation failed",
                name=self.integration_name,
                error=version_error,
            )
            return False, version_error

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "Git is available",
            name=self.integration_name,
            version=self.get_version(),
        )
        return True, ""

    # Git-specific methods (IRepositoryTool implementation)

    def clone(
        self,
        repo_url: str,
        target_dir: str,
        branch: Optional[str] = None,
        depth: int = 1,
        timeout: int = 300,
        **kwargs,
    ) -> CommandResult:
        """
        Clone a git repository.

        Implements IRepositoryTool.clone interface.

        Args:
            repo_url: Repository URL
            target_dir: Target directory for clone
            branch: Branch/tag/reference to checkout
            depth: Clone depth (1 for shallow clone, 0 for full clone)
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If git clone fails
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot clone repository",
                error=error,
                name=self.integration_name,
            )
            raise RuntimeError(error)

        try:
            logger.debug(
                "Cloning Git repository",
                repo_url=repo_url,
                target_dir=target_dir,
                branch=branch,
                depth=depth,
                name=self.integration_name,
            )

            args = ["clone"]

            # Add depth argument if specified
            if depth and depth > 0:
                args.extend(["--depth", str(depth)])

            # Add branch argument if specified
            if branch:
                args.extend(["--branch", branch])

            # Add repository and target directory
            args.extend([repo_url, target_dir])

            # Execute git command
            result = self._run_integration(args, timeout=timeout)

            if result.returncode == 0:
                logger.info(
                    "Git clone completed",
                    target_dir=target_dir,
                    name=self.integration_name,
                )
            else:
                logger.error(
                    "Git clone failed",
                    repo_url=repo_url,
                    target_dir=target_dir,
                    returncode=result.returncode,
                    stderr=result.stderr,
                    name=self.integration_name,
                )

            return result

        except Exception as e:
            logger.error(
                "Git clone failed with exception",
                repo_url=repo_url,
                target_dir=target_dir,
                error_type=type(e).__name__,
                name=self.integration_name,
                exc_info=True,
            )
            raise RuntimeError(f"Git clone failed: {e}") from e

    def pull(
        self,
        working_dir: str,
        branch: Optional[str] = None,
        timeout: int = 180,
        **kwargs,
    ) -> CommandResult:
        """
        Pull latest changes from remote repository.

        Args:
            working_dir: Git repository directory
            branch: Branch to pull (None for current branch)
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            Command result dict

        Raises:
            RuntimeError: If git pull fails
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(error)

        try:
            logger.debug(
                "Pulling Git repository",
                working_dir=working_dir,
                branch=branch,
                name=self.integration_name,
            )

            args = ["pull"]
            if branch:
                args.extend(["origin", branch])

            result = self._run_integration(args, cwd=working_dir, timeout=timeout)

            if result.returncode == 0:
                logger.info(
                    "Git pull completed",
                    working_dir=working_dir,
                    name=self.integration_name,
                )
            else:
                logger.error(
                    "Git pull failed",
                    working_dir=working_dir,
                    returncode=result.returncode,
                    stderr=result.stderr,
                    name=self.integration_name,
                )

            return result

        except Exception as e:
            logger.error(
                "Git pull failed with exception",
                working_dir=working_dir,
                error_type=type(e).__name__,
                name=self.integration_name,
                exc_info=True,
            )
            raise RuntimeError(f"Git pull failed: {e}") from e

    def get_current_branch(self, working_dir: str, timeout: int = 30) -> Optional[str]:
        """
        Get current branch name.

        Args:
            working_dir: Git repository directory
            timeout: Command timeout in seconds

        Returns:
            Branch name or None if not a git repository
        """
        available, error = self.ensure_available()
        if not available:
            return None

        try:
            result = self._run_integration(["branch", "--show-current"], cwd=working_dir, timeout=timeout)

            if result.returncode == 0:
                branch = result.stdout.strip()
                logger.debug(
                    "Current branch retrieved",
                    working_dir=working_dir,
                    branch=branch,
                    name=self.integration_name,
                )
                return branch

            return None

        except Exception as e:
            logger.debug(
                "Failed to get current branch",
                working_dir=working_dir,
                error=str(e),
                name=self.integration_name,
            )
            return None

    def status(self, working_dir: str, timeout: int = 30) -> Tuple[bool, "GitStatusResult"]:
        """Return the working-tree status of a repository.

        Args:
            working_dir: Git repository directory.
            timeout: Command timeout in seconds.

        Returns:
            ``(is_git_repo, GitStatusResult)``
        """
        available, _ = self.ensure_available()
        if not available:
            return False, GitStatusResult()

        try:
            # Porcelain v1 — machine-readable, one line per changed file
            result = self._run_integration(
                ["status", "--porcelain", "-b"],
                cwd=working_dir,
                timeout=timeout,
            )
            if result.returncode != 0:
                return False, GitStatusResult()
            return True, _parse_porcelain_status(result.stdout)
        except Exception as e:
            logger.debug(
                "Failed to get git status",
                working_dir=working_dir,
                error=str(e),
                name=self.integration_name,
            )
            return False, GitStatusResult()

    def get_remote_url(self, working_dir: str, remote: str = "origin", timeout: int = 15) -> Optional[str]:
        """Return the fetch URL for a remote.

        Args:
            working_dir: Git repository directory.
            remote: Remote name (default ``origin``).
            timeout: Command timeout in seconds.

        Returns:
            Remote URL string, or ``None`` if not found.
        """
        available, _ = self.ensure_available()
        if not available:
            return None

        try:
            result = self._run_integration(
                ["remote", "get-url", remote],
                cwd=working_dir,
                timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout.strip() or None
            return None
        except Exception as e:
            logger.debug(
                "Failed to get remote URL",
                working_dir=working_dir,
                remote=remote,
                error=str(e),
                name=self.integration_name,
            )
            return None


# ---------------------------------------------------------------------------
# Supporting data types
# ---------------------------------------------------------------------------


@dataclass
class GitStatusResult:
    """Parsed result of ``git status --porcelain -b``."""

    branch: Optional[str] = None
    tracking: Optional[str] = None
    ahead: int = 0
    behind: int = 0
    staged: List[str] = field(default_factory=list)
    unstaged: List[str] = field(default_factory=list)
    untracked: List[str] = field(default_factory=list)
    conflicted: List[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not (self.staged or self.unstaged or self.untracked or self.conflicted)

    @property
    def is_dirty(self) -> bool:
        return not self.is_clean


def _parse_porcelain_status(output: str) -> "GitStatusResult":
    """Parse ``git status --porcelain -b`` output into a :class:`GitStatusResult`."""
    result = GitStatusResult()
    lines = output.splitlines()
    if not lines:
        return result

    # First line: ## branch...tracking [ahead N] [behind N]
    header = lines[0]
    if header.startswith("## "):
        branch_info = header[3:]
        # Split branch and tracking
        if "..." in branch_info:
            branch_part, tracking_part = branch_info.split("...", 1)
            result.branch = branch_part.strip()
            # tracking_part may contain "[ahead N, behind M]" etc.
            tracking_name = re.split(r"\s+\[", tracking_part)[0].strip()
            result.tracking = tracking_name or None
            # Ahead / behind counts
            m_ahead = re.search(r"\bahead\s+(\d+)", tracking_part)
            m_behind = re.search(r"\bbehind\s+(\d+)", tracking_part)
            if m_ahead:
                result.ahead = int(m_ahead.group(1))
            if m_behind:
                result.behind = int(m_behind.group(1))
        else:
            result.branch = branch_info.strip()

    conflict_codes = {"AA", "UU", "DD", "AU", "UA", "DU", "UD"}

    for line in lines[1:]:
        if len(line) < 2:
            continue
        xy = line[:2]
        path = line[3:]
        if xy == "??":
            result.untracked.append(path)
        elif xy in conflict_codes:
            result.conflicted.append(path)
        else:
            x, y = xy[0], xy[1]
            if x != " " and x != "?":
                result.staged.append(path)
            if y != " " and y != "?":
                result.unstaged.append(path)

    return result
