#!/usr/bin/env python3
"""
===============================================================================
Script Name   : git.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Git integration for XYZ Platform.

Git integration for repository operations.

Note: Uses subprocess-based approach rather than GitPython for:
- Audit trail: Captures exact Git commands for compliance logging
- Consistency: Matches BaseIntegration pattern used for other integrations
- Simplicity: Only need basic clone operations, not full Git API
- Minimal dependencies: Reduces external dependencies

===============================================================================
"""

import re
from typing import Dict, List, Optional, Any, Tuple

from xyz_platform.logger import get_logger
from xyz_platform.integrations.capabilities import IRepositoryTool
from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.models.integration_model import IntegrationModel

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
            extra={"integration_name": self.integration_name},
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
                extra={"integration_name": self.integration_name},
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
                extra={
                    "integration_name": self.integration_name,
                    "error": version_error,
                },
            )
            return False, version_error

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "Git is available",
            extra={
                "integration_name": self.integration_name,
                "version": self.get_version(),
            },
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
    ) -> Any:
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
                extra={"error": error, "integration_name": self.integration_name},
            )
            raise RuntimeError(error)

        try:
            logger.debug(
                "Cloning Git repository",
                extra={
                    "repo_url": repo_url,
                    "target_dir": target_dir,
                    "branch": branch,
                    "depth": depth,
                    "integration_name": self.integration_name,
                },
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

            if result["returncode"] == 0:
                logger.info(
                    "Git clone completed",
                    extra={
                        "target_dir": target_dir,
                        "integration_name": self.integration_name,
                    },
                )
            else:
                logger.error(
                    "Git clone failed",
                    extra={
                        "repo_url": repo_url,
                        "target_dir": target_dir,
                        "returncode": result["returncode"],
                        "stderr": result.get("stderr", ""),
                        "integration_name": self.integration_name,
                    },
                )

            return result

        except Exception as e:
            logger.error(
                "Git clone failed with exception",
                extra={
                    "repo_url": repo_url,
                    "target_dir": target_dir,
                    "error_type": type(e).__name__,
                    "integration_name": self.integration_name,
                },
                exc_info=True,
            )
            raise RuntimeError(f"Git clone failed: {e}") from e

    def pull(
        self,
        working_dir: str,
        branch: Optional[str] = None,
        timeout: int = 180,
        **kwargs,
    ) -> Dict[str, Any]:
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
                extra={
                    "working_dir": working_dir,
                    "branch": branch,
                    "integration_name": self.integration_name,
                },
            )

            args = ["pull"]
            if branch:
                args.extend(["origin", branch])

            result = self._run_integration(args, cwd=working_dir, timeout=timeout)

            if result["returncode"] == 0:
                logger.info(
                    "Git pull completed",
                    extra={
                        "working_dir": working_dir,
                        "integration_name": self.integration_name,
                    },
                )
            else:
                logger.error(
                    "Git pull failed",
                    extra={
                        "working_dir": working_dir,
                        "returncode": result["returncode"],
                        "stderr": result.get("stderr", ""),
                        "integration_name": self.integration_name,
                    },
                )

            return result

        except Exception as e:
            logger.error(
                "Git pull failed with exception",
                extra={
                    "working_dir": working_dir,
                    "error_type": type(e).__name__,
                    "integration_name": self.integration_name,
                },
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
            result = self._run_integration(
                ["branch", "--show-current"], cwd=working_dir, timeout=timeout
            )

            if result["returncode"] == 0:
                branch = result["stdout"].strip()
                logger.debug(
                    "Current branch retrieved",
                    extra={
                        "working_dir": working_dir,
                        "branch": branch,
                        "integration_name": self.integration_name,
                    },
                )
                return branch

            return None

        except Exception as e:
            logger.debug(
                "Failed to get current branch",
                extra={
                    "working_dir": working_dir,
                    "error": str(e),
                    "integration_name": self.integration_name,
                },
            )
            return None
