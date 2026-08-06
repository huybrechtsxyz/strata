"""Git integration for repository operations."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IRepositoryTool
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel
from strata.utils.system import CommandResult

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

    def fetch(
        self,
        working_dir: str,
        tags: bool = True,
        timeout: int = 180,
    ) -> CommandResult:
        """Fetch from origin, optionally including tags.

        Args:
            working_dir: Git repository directory.
            tags: If True (default), also fetch tags with ``--tags``.
            timeout: Command timeout in seconds.

        Returns:
            CommandResult from the git fetch invocation.
        """
        args = ["fetch", "origin"]
        if tags:
            args.append("--tags")
        return self._run_integration(args, cwd=working_dir, timeout=timeout)

    def checkout(
        self,
        working_dir: str,
        ref: str,
        detach: bool = True,
        timeout: int = 60,
    ) -> CommandResult:
        """Checkout a ref, entering detached HEAD by default.

        Args:
            working_dir: Git repository directory.
            ref: Branch, tag, or commit SHA to check out.
            detach: If True (default), pass ``--detach`` so the repo ends up
                in detached-HEAD state (correct for version-pinned builds).
            timeout: Command timeout in seconds.

        Returns:
            CommandResult from the git checkout invocation.
        """
        args = ["checkout"]
        if detach:
            args.append("--detach")
        args.append(ref)
        return self._run_integration(args, cwd=working_dir, timeout=timeout)

    def rev_parse(
        self,
        working_dir: str,
        ref: str = "HEAD",
        timeout: int = 30,
    ) -> CommandResult:
        """Resolve a ref to its full commit SHA.

        Args:
            working_dir: Git repository directory.
            ref: Ref to resolve (default ``HEAD``).
            timeout: Command timeout in seconds.

        Returns:
            CommandResult whose ``stdout`` contains the SHA when successful.
        """
        return self._run_integration(["rev-parse", ref], cwd=working_dir, timeout=timeout)

    def archive_subtree(
        self,
        working_dir: str,
        ref: str,
        subtree_path: str,
        dest_dir: str,
        timeout: int = 120,
    ) -> Tuple[bool, str]:
        """Extract a subtree at a specific ref into dest_dir without mutating the working tree.

        Uses ``git archive <ref> -- <path>`` piped through tar extraction.
        Falls back gracefully if the ref or path does not exist.

        Args:
            working_dir: Git repository directory.
            ref: Branch, tag, or commit SHA to extract from.
            subtree_path: Relative path within the repository to extract.
            dest_dir: Destination directory to extract files into.
            timeout: Command timeout in seconds.

        Returns:
            (success, message) tuple.
        """
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        available, error = self.ensure_available()
        if not available:
            return False, f"Git not available: {error}"

        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

        # Use git archive to extract the subtree at the given ref
        # This does not mutate the working tree
        archive_cmd = [
            self.command,
            "-C",
            working_dir,
            "archive",
            ref,
            "--",
            subtree_path,
        ]

        try:
            # Run git archive and pipe to tar extraction
            archive_proc = subprocess.Popen(
                archive_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Extract to a temp dir first, then move contents
            with tempfile.TemporaryDirectory() as tmp_dir:
                tar_cmd = ["tar", "-x", "-C", tmp_dir]
                tar_proc = subprocess.Popen(
                    tar_cmd,
                    stdin=archive_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                archive_proc.stdout.close()  # Allow archive to receive SIGPIPE
                _, tar_stderr = tar_proc.communicate(timeout=timeout)
                _, archive_stderr = archive_proc.communicate(timeout=10)

                if archive_proc.returncode != 0:
                    err_msg = archive_stderr.decode("utf-8", errors="replace").strip()
                    return False, f"git archive failed for ref '{ref}' path '{subtree_path}': {err_msg}"

                if tar_proc.returncode != 0:
                    err_msg = tar_stderr.decode("utf-8", errors="replace").strip()
                    return False, f"tar extraction failed: {err_msg}"

                # git archive preserves the subtree_path prefix in the archive.
                # Copy from tmp_dir/subtree_path/* into dest_dir/
                extracted_root = Path(tmp_dir) / subtree_path
                if not extracted_root.exists():
                    # Some git versions strip trailing slashes differently
                    extracted_root = Path(tmp_dir)

                shutil.copytree(str(extracted_root), str(dest), dirs_exist_ok=True)

            return True, f"Extracted '{subtree_path}' at ref '{ref}' to {dest_dir}"

        except subprocess.TimeoutExpired:
            return False, f"git archive timed out after {timeout}s"
        except OSError as exc:
            return False, f"git archive OS error: {exc}"

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

    # ------------------------------------------------------------------
    # Write operations (used by AuditController for deploy-log push)
    # ------------------------------------------------------------------

    def add(self, working_dir: str, paths: List[str], timeout: int = 30) -> CommandResult:
        """Stage files for commit.

        Args:
            working_dir: Git repository directory.
            paths: List of file paths (relative to working_dir) to stage.
            timeout: Command timeout in seconds.

        Returns:
            Command result.
        """
        available, error = self.ensure_available()
        if not available:
            return CommandResult(returncode=1, stdout="", stderr=error, command="git add", duration_ms=0.0)

        args = ["add", "--"] + paths
        return self._run_integration(args, cwd=working_dir, timeout=timeout)

    def commit(self, working_dir: str, message: str, timeout: int = 30) -> CommandResult:
        """Create a commit with the given message.

        Args:
            working_dir: Git repository directory.
            message: Commit message.
            timeout: Command timeout in seconds.

        Returns:
            Command result.
        """
        available, error = self.ensure_available()
        if not available:
            return CommandResult(returncode=1, stdout="", stderr=error, command="git commit", duration_ms=0.0)

        args = ["commit", "-m", message]
        return self._run_integration(args, cwd=working_dir, timeout=timeout)

    def push(
        self,
        working_dir: str,
        remote: str = "origin",
        branch: Optional[str] = None,
        timeout: int = 60,
    ) -> CommandResult:
        """Push to remote.

        Args:
            working_dir: Git repository directory.
            remote: Remote name.
            branch: Branch to push (None pushes current branch).
            timeout: Command timeout in seconds.

        Returns:
            Command result.
        """
        available, error = self.ensure_available()
        if not available:
            return CommandResult(returncode=1, stdout="", stderr=error, command="git push", duration_ms=0.0)

        args = ["push", remote]
        if branch:
            args.append(branch)
        return self._run_integration(args, cwd=working_dir, timeout=timeout)

    def pull_rebase(
        self,
        working_dir: str,
        remote: str = "origin",
        timeout: int = 60,
    ) -> CommandResult:
        """Pull with rebase — used for retry on ref conflicts.

        Args:
            working_dir: Git repository directory.
            remote: Remote name.
            timeout: Command timeout in seconds.

        Returns:
            Command result.
        """
        available, error = self.ensure_available()
        if not available:
            return CommandResult(returncode=1, stdout="", stderr=error, command="git pull", duration_ms=0.0)

        args = ["pull", "--rebase", remote]
        return self._run_integration(args, cwd=working_dir, timeout=timeout)

    def log(
        self,
        working_dir: str,
        format: str = "%H",
        count: int = 1,
        timeout: int = 30,
    ) -> CommandResult:
        """Get git log entries.

        Args:
            working_dir: Git repository directory.
            format: Git log format string.
            count: Number of entries to retrieve.
            timeout: Command timeout in seconds.

        Returns:
            Command result.
        """
        available, error = self.ensure_available()
        if not available:
            return CommandResult(returncode=1, stdout="", stderr=error, command="git log", duration_ms=0.0)

        args = ["log", f"--format={format}", f"-{count}"]
        return self._run_integration(args, cwd=working_dir, timeout=timeout)

    def list_tags(
        self,
        working_dir: str,
        pattern: Optional[str] = None,
        sort: str = "-creatordate",
        timeout: int = 30,
    ) -> List["TagInfo"]:
        """List tags in the repository, optionally filtered by pattern.

        Args:
            working_dir: Git repository directory.
            pattern: Optional grep pattern to filter tags (e.g., "^v[0-9]", "^tested").
            sort: Sort order. Options:
                - "-creatordate": newest first (default)
                - "creatordate": oldest first
                - "version:refname": semantic version order
                - "refname": alphabetical
            timeout: Command timeout in seconds.

        Returns:
            List of TagInfo sorted by creation date (newest first).
            Empty list if no tags found or repository not available.

        Raises:
            RuntimeError: If git command fails unexpectedly.
        """
        available, error = self.ensure_available()
        if not available:
            logger.debug(
                "Cannot list tags, git not available",
                error=error,
                name=self.integration_name,
            )
            return []

        try:
            # Build git tag command
            # Format: name|shortsha|creatordate|tagger|contents
            args = [
                "tag",
                "--list",
                "--sort",
                sort,
                "--format=%(refname:short)|%(objectname:short)|%(creatordate:iso)|%(taggername)|%(contents)",
            ]

            # Add pattern if specified
            if pattern:
                args.append(pattern)
            else:
                args.append("*")  # All tags

            result = self._run_integration(args, cwd=working_dir, timeout=timeout)

            if result.returncode != 0:
                logger.debug(
                    "Git tag list failed",
                    working_dir=working_dir,
                    returncode=result.returncode,
                    stderr=result.stderr,
                    name=self.integration_name,
                )
                return []

            # Parse output into TagInfo list
            tags = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue

                try:
                    parts = line.split("|")
                    if len(parts) < 3:
                        continue

                    name = parts[0].strip()
                    short_commit = parts[1].strip()
                    created_str = parts[2].strip()
                    tagger = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
                    message = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None

                    # Parse ISO format datetime
                    try:
                        created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    except ValueError:
                        # Fallback if parsing fails
                        created = None

                    # Get full commit SHA
                    sha_result = self._run_integration(
                        ["rev-list", "-n", "1", name],
                        cwd=working_dir,
                        timeout=5,
                    )
                    commit = sha_result.stdout.strip() if sha_result.returncode == 0 else short_commit

                    tag_info = TagInfo(
                        name=name,
                        commit=commit,
                        short_commit=short_commit,
                        tagger=tagger,
                        created=created,
                        message=message,
                        is_annotated=bool(tagger or message),
                    )
                    tags.append(tag_info)

                except Exception as e:
                    logger.debug(
                        "Failed to parse tag",
                        line=line,
                        error=str(e),
                        name=self.integration_name,
                    )
                    continue

            logger.debug(
                "Git tag list completed",
                working_dir=working_dir,
                tag_count=len(tags),
                name=self.integration_name,
            )
            return tags

        except Exception as e:
            logger.error(
                "Git tag list failed with exception",
                working_dir=working_dir,
                error_type=type(e).__name__,
                name=self.integration_name,
                exc_info=True,
            )
            return []


# ---------------------------------------------------------------------------
# Supporting data types
# ---------------------------------------------------------------------------


@dataclass
class TagInfo:
    """Git tag metadata."""

    name: str  # e.g., "v1.2.0", "tested"
    commit: str  # full SHA
    short_commit: str  # short SHA (7 chars)
    tagger: Optional[str] = None  # creator name (annotated tags only)
    created: Optional[datetime] = None  # when tag was created
    message: Optional[str] = None  # tag message (annotated tags only)
    is_annotated: bool = False  # vs lightweight

    @property
    def age_days(self) -> Optional[int]:
        """Age of tag in days since creation."""
        if self.created is None:
            return None
        # Handle timezone-aware datetime
        now = datetime.now(self.created.tzinfo) if self.created.tzinfo else datetime.now()
        delta = now - self.created
        return delta.days

    @property
    def age_str(self) -> str:
        """Human-readable age string."""
        if self.age_days is None:
            return "unknown"
        if self.age_days == 0:
            return "today"
        if self.age_days == 1:
            return "1 day ago"
        return f"{self.age_days} days ago"


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
