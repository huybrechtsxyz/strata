#!/usr/bin/env python3
"""
===============================================================================
Script Name   : base_integration.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Base class for external integrations.

BaseIntegration provides common functionality for all integrations:
- Singleton pattern (one instance per integration class and optional key)
- Integration availability checking
- Version detection
- Command execution
- Error handling
- Config-driven initialization

Key Design:
- Integrations receive IntegrationModel config at initialization
- Authentication and endpoints loaded from config
- Environment variables resolved from config spec
===============================================================================
"""

import os
import threading
from abc import ABC, abstractmethod
from packaging import version
from typing import Any, Dict, List, Optional, Tuple

from xyz_platform.logger import get_logger
from xyz_platform.utils.system import run_command
from xyz_platform.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class BaseIntegration(ABC):
    """
    Abstract base class for external integrations.

    Provides common functionality for:
    - Singleton pattern (one instance per integration class and optional key)
    - Integration availability checking
    - Version detection
    - Command execution
    - Error handling
    - Config-driven initialization

    Command Resolution:
    - Built-in integrations: Define COMMAND class attribute (e.g., COMMAND = "git")
    - Custom integrations: Extract from config.validation.command
    - Fallback: Use config.type as command name

    Singleton Pattern:
    - Each integration subclass automatically gets singleton behavior
    - Thread-safe implementation using locks
    - Multiple instances possible per integration class using custom keys
    - Override _get_instance_key_static() for custom instance keying

    Example:
        # Simple singleton (one instance per integration class)
        class GitIntegration(BaseIntegration):
            def __init__(self, config: IntegrationModel):
                super().__init__(config)

        # Multiple instances per endpoint
        class VaultIntegration(BaseIntegration):
            @classmethod
            def _get_instance_key_static(cls, class_ref, *args, **kwargs):
                config = kwargs.get('config') or args[0]
                endpoint = config.endpoints.address if config.endpoints else "default"
                return endpoint
    """

    _instances: Dict[str, "BaseIntegration"] = {}
    _lock = threading.Lock()

    # Singleton implementation

    def __new__(cls, *args, **kwargs):
        """
        Create or return existing singleton instance per integration class.

        Each subclass gets its own singleton, with optional instance key
        for multiple instances (e.g., different endpoints).

        Thread-safe implementation prevents race conditions.

        Returns:
            Singleton instance for this integration class and instance key
        """
        # Get instance key from subclass or use default
        instance_key = cls._get_instance_key_static(cls, *args, **kwargs)
        full_key = f"{cls.__name__}::{instance_key}"

        # Thread-safe singleton creation
        with cls._lock:
            if full_key not in cls._instances:
                instance = super().__new__(cls)
                cls._instances[full_key] = instance
            return cls._instances[full_key]

    def __init__(self, config: IntegrationModel):
        """
        Initialize the integration.

        Args:
            config: Integration configuration model from platform config
        """
        # Avoid re-initialization of singleton
        if hasattr(self, "_initialized"):
            return

        self.config = config
        self.integration_name = config.name
        self.integration_type = config.type
        self.command = self._get_command_from_config()

        self._is_available = None
        self._version = None
        self._info = None
        self._initialized = True

        logger.debug(
            "Integration initialized",
            extra={
                "integration_name": self.integration_name,
                "type": self.integration_type,
            },
        )

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """
        Get instance key for singleton lookup.

        Override in subclass to provide custom instance keys based on
        constructor arguments (e.g., endpoint, connection string, etc.).

        This allows multiple singleton instances per integration class, each
        with different configurations.

        Args:
            class_ref: The class being instantiated
            *args: Constructor positional arguments
            **kwargs: Constructor keyword arguments

        Returns:
            Instance key string (default: "default")

        Example:
            @classmethod
            def _get_instance_key_static(cls, class_ref, *args, **kwargs):
                # Create different instances per endpoint
                config = kwargs.get('config') or args[0]
                endpoint = config.endpoints.address if config.endpoints else "default"
                return endpoint or "default"
        """
        return "default"

    def _get_command_from_config(self) -> str:
        """
        Extract command from config.

        Priority:
        1. Class-level COMMAND attribute (for built-in integrations)
        2. Config validation.command (for custom integrations)
        3. Integration type as fallback

        Returns:
            Command executable name
        """
        # Check for class-level COMMAND attribute first
        # This allows built-in integrations to define their own command
        if hasattr(self.__class__, "COMMAND"):
            return self.__class__.COMMAND

        # For custom integrations, extract from validation.command
        if self.config.validation and self.config.validation.command:
            # Extract first word from validation command
            # e.g., "customtool --version" -> "customtool"
            return self.config.validation.command.split()[0]

        # Fallback to integration type
        return self.config.type

    def _get_env_var(self, var_name: str) -> Optional[str]:
        """
        Get environment variable value.

        Args:
            var_name: Environment variable name

        Returns:
            Environment variable value or None
        """
        return os.getenv(var_name)

    # Abstract methods to implement in subclasses

    @abstractmethod
    def get_version_command(self) -> List[str]:
        """
        Get the command to retrieve integration version.

        Returns:
            List of command arguments (e.g., ["git", "--version"])
        """
        pass

    @abstractmethod
    def parse_version(self, version_output: str) -> str:
        """
        Parse version string from integration output.

        Args:
            version_output: Raw output from version command

        Returns:
            Parsed version string (e.g., "2.40.0")
        """
        pass

    # Base integration methods

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure integration is available and meets version requirements.

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_available():
            self._info = f"{self.integration_name} is not installed or not in PATH"
            return False, self._info

        # Validate version if requirements are specified
        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            return False, version_error

        self._info = f"{self.integration_name} has {self.get_version()} installed"
        return True, ""

    def get_info(self) -> Dict[str, Any]:
        """
        Get integration information.

        Returns:
            Dict with integration name, command, availability, version
        """
        return {
            "name": self.integration_name,
            "type": self.integration_type,
            "command": self.command,
            "available": self.is_available(),
            "version": self.get_version(),
            "info": self._info,
            "capabilities": self.config.capabilities,
            "required": self.config.required,
            "enabled": self.config.enabled,
        }

    def get_version(self, use_cache: bool = True) -> Optional[str]:
        """
        Get the installed version of the integration.

        Args:
            use_cache: Use cached result if available

        Returns:
            Version string or None if integration not available
        """
        if use_cache and self._version is not None:
            return self._version

        if not self.is_available(use_cache=False):
            return None

        try:
            version_cmd = self.get_version_command()
            result = run_command(version_cmd, timeout=5)

            if result.returncode == 0:
                self._version = self.parse_version(result.stdout)
                logger.debug(
                    "Integration version detected",
                    extra={
                        "integration_name": self.integration_name,
                        "version": self._version,
                    },
                )
                return self._version
            else:
                logger.warning(
                    "Failed to get integration version",
                    extra={
                        "integration_name": self.integration_name,
                        "stderr": result.stderr,
                    },
                )
                return None

        except Exception as e:
            logger.warning(
                "Exception getting integration version",
                extra={"integration_name": self.integration_name, "error": str(e)},
                exc_info=True,
            )
            return None

    def validate_version(self) -> Tuple[bool, str]:
        """
        Validate integration version against min/max version requirements.

        Checks if installed version meets the requirements specified in
        config.validation.min_version and config.validation.max_version.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # If no validation spec, version is valid
        if not self.config.validation:
            return True, ""

        # Get current version
        current_version = self.get_version()
        if not current_version:
            return False, f"Could not detect {self.integration_name} version"

        try:
            current = version.parse(current_version)

            # Check minimum version
            if self.config.validation.min_version:
                min_ver = version.parse(self.config.validation.min_version)
                if current < min_ver:
                    msg = (
                        f"{self.integration_name} version {current_version} is below "
                        f"minimum required version {self.config.validation.min_version}"
                    )
                    logger.warning(
                        "Integration version below minimum",
                        extra={
                            "integration_name": self.integration_name,
                            "current": current_version,
                            "min_required": self.config.validation.min_version,
                        },
                    )
                    return False, msg

            # Check maximum version
            if self.config.validation.max_version:
                max_ver = version.parse(self.config.validation.max_version)
                if current > max_ver:
                    msg = (
                        f"{self.integration_name} version {current_version} is above "
                        f"maximum supported version {self.config.validation.max_version}"
                    )
                    logger.warning(
                        "Integration version above maximum",
                        extra={
                            "integration_name": self.integration_name,
                            "current": current_version,
                            "max_supported": self.config.validation.max_version,
                        },
                    )
                    return False, msg

            # Version is within range
            logger.debug(
                "Integration version validated",
                extra={
                    "integration_name": self.integration_name,
                    "version": current_version,
                    "min_version": self.config.validation.min_version,
                    "max_version": self.config.validation.max_version,
                },
            )
            return True, ""

        except Exception as e:
            msg = f"Failed to validate {self.integration_name} version: {str(e)}"
            logger.error(
                "Version validation failed",
                extra={
                    "integration_name": self.integration_name,
                    "version": current_version,
                    "error": str(e),
                },
                exc_info=True,
            )
            return False, msg

    def is_available(self, use_cache: bool = True) -> bool:
        """
        Check if integration command is available in system PATH.

        Args:
            use_cache: Use cached result if available

        Returns:
            True if command is available, False otherwise
        """
        if use_cache and self._is_available is not None:
            return self._is_available

        try:
            # Try running version command
            result = run_command(self.get_version_command(), timeout=5)
            self._is_available = result.returncode == 0

            logger.debug(
                "Integration availability checked",
                extra={
                    "integration_name": self.integration_name,
                    "available": self._is_available,
                },
            )

            return self._is_available

        except Exception as e:
            logger.debug(
                "Integration not available",
                extra={"integration_name": self.integration_name, "error": str(e)},
            )
            self._is_available = False
            return False

    def _run_integration(
        self, args: List[str], cwd: Optional[str] = None, timeout: int = 300, **kwargs
    ) -> Dict[str, Any]:
        """
        Run integration command with arguments.

        Args:
            args: Command arguments
            cwd: Working directory
            timeout: Command timeout in seconds
            **kwargs: Additional arguments for run_command

        Returns:
            Command result dict with returncode, stdout, stderr
        """
        command = [self.command] + args

        logger.debug(
            "Running integration command",
            extra={
                "integration_name": self.integration_name,
                "command": " ".join(command),
            },
        )

        result = run_command(command, cwd=cwd, timeout=timeout, **kwargs)

        if result.returncode != 0:
            logger.warning(
                "Integration command failed",
                extra={
                    "integration_name": self.integration_name,
                    "command": " ".join(command),
                    "returncode": result.returncode,
                    "stderr": result.stderr,
                },
            )

        return result
