#!/usr/bin/env python3
"""
===============================================================================
Script Name   : docker.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Docker integration for XYZ Platform.

Docker integration for container operations.

Note: Uses subprocess-based approach for:
- Audit trail: Captures exact Docker commands for compliance logging
- Consistency: Matches BaseIntegration pattern used for other integrations
- Simplicity: Only need basic Docker operations
- Minimal dependencies: Reduces external dependencies

===============================================================================
"""

import re
from typing import Dict, List, Optional, Any, Tuple

from xyz_platform.logger import get_logger
from xyz_platform.integrations.capabilities import IContainerTool
from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class DockerIntegration(BaseIntegration):
    """
    Docker integration for container operations.

    Implements singleton pattern per config - multiple instances possible
    for different Docker configurations (e.g., different registries, endpoints).
    """

    # Command executable name
    COMMAND = "docker"

    # Declare supported capabilities
    CAPABILITIES = [IContainerTool]

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
        Initialize Docker integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        logger.debug(
            "Docker integration initialized",
            extra={"name": self.integration_name},
        )

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve docker version."""
        return [self.command, "--version"]

    def parse_version(self, version_output: str) -> str:
        """
        Parse version string from docker output.

        Args:
            version_output: Raw output (e.g., "Docker version 24.0.7, build afdd53b")

        Returns:
            Version string (e.g., "24.0.7")
        """
        # Extract version number from "Docker version X.Y.Z, build ..."
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
                "Docker CLI not found", extra={"name": self.integration_name}
            )
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. "
                f"Install Docker from: https://www.docker.com/get-started",
            )

        # Validate version requirements
        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "Docker version validation failed",
                extra={"name": self.integration_name, "error": version_error},
            )
            return False, version_error

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "Docker is available",
            extra={"name": self.integration_name, "version": self.get_version()},
        )
        return True, ""

    # Docker-specific methods (IContainerTool implementation)

    def build(
        self,
        context_dir: str,
        tag: str,
        dockerfile: Optional[str] = None,
        build_args: Optional[Dict[str, str]] = None,
        no_cache: bool = False,
        timeout: int = 600,
        **kwargs,
    ) -> Any:
        """
        Build a Docker image.

        Implements IContainerTool.build interface.

        Args:
            context_dir: Build context directory
            tag: Image tag (e.g., "myapp:latest")
            dockerfile: Path to Dockerfile (relative to context, default: Dockerfile)
            build_args: Build arguments (--build-arg key=value)
            no_cache: Disable build cache
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If docker build fails
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot build image",
                extra={"error": error, "name": self.integration_name},
            )
            raise RuntimeError(error)

        try:
            logger.debug(
                "Building Docker image",
                extra={
                    "context_dir": context_dir,
                    "tag": tag,
                    "dockerfile": dockerfile,
                    "name": self.integration_name,
                },
            )

            args = ["build", "-t", tag]

            # Add dockerfile argument if specified
            if dockerfile:
                args.extend(["-f", dockerfile])

            # Add build arguments if specified
            if build_args:
                for key, value in build_args.items():
                    args.extend(["--build-arg", f"{key}={value}"])

            # Add no-cache flag if specified
            if no_cache:
                args.append("--no-cache")

            # Add context directory
            args.append(context_dir)

            result = self._run_integration(args, timeout=timeout)

            if result["returncode"] != 0:
                error_msg = f"Docker build failed: {result['stderr']}"
                logger.error(
                    "Docker build failed",
                    extra={
                        "tag": tag,
                        "stderr": result["stderr"],
                        "name": self.integration_name,
                    },
                )
                raise RuntimeError(error_msg)

            logger.info(
                "Docker image built successfully",
                extra={"tag": tag, "name": self.integration_name},
            )

            return result

        except Exception as e:
            logger.error(
                "Failed to build Docker image",
                extra={"tag": tag, "error": str(e), "name": self.integration_name},
                exc_info=True,
            )
            raise

    def run(
        self,
        image: str,
        command: Optional[List[str]] = None,
        detach: bool = False,
        rm: bool = True,
        env: Optional[Dict[str, str]] = None,
        ports: Optional[Dict[str, str]] = None,
        volumes: Optional[Dict[str, str]] = None,
        name: Optional[str] = None,
        timeout: int = 300,
        **kwargs,
    ) -> Any:
        """
        Run a Docker container.

        Implements IContainerTool.run interface.

        Args:
            image: Image name and tag (e.g., "myapp:latest")
            command: Command to run in container
            detach: Run container in background
            rm: Automatically remove container when it exits
            env: Environment variables (key: value)
            ports: Port mappings (host_port: container_port)
            volumes: Volume mappings (host_path: container_path)
            name: Container name
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If docker run fails
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot run container",
                extra={"error": error, "name": self.integration_name},
            )
            raise RuntimeError(error)

        try:
            logger.debug(
                "Running Docker container",
                extra={"image": image, "name": self.integration_name},
            )

            args = ["run"]

            # Add detach flag if specified
            if detach:
                args.append("-d")

            # Add rm flag if specified
            if rm:
                args.append("--rm")

            # Add container name if specified
            if name:
                args.extend(["--name", name])

            # Add environment variables if specified
            if env:
                for key, value in env.items():
                    args.extend(["-e", f"{key}={value}"])

            # Add port mappings if specified
            if ports:
                for host_port, container_port in ports.items():
                    args.extend(["-p", f"{host_port}:{container_port}"])

            # Add volume mappings if specified
            if volumes:
                for host_path, container_path in volumes.items():
                    args.extend(["-v", f"{host_path}:{container_path}"])

            # Add image
            args.append(image)

            # Add command if specified
            if command:
                args.extend(command)

            result = self._run_integration(args, timeout=timeout)

            if result["returncode"] != 0:
                error_msg = f"Docker run failed: {result['stderr']}"
                logger.error(
                    "Docker run failed",
                    extra={
                        "image": image,
                        "stderr": result["stderr"],
                        "name": self.integration_name,
                    },
                )
                raise RuntimeError(error_msg)

            logger.info(
                "Docker container started successfully",
                extra={"image": image, "name": self.integration_name},
            )

            return result

        except Exception as e:
            logger.error(
                "Failed to run Docker container",
                extra={"image": image, "error": str(e), "name": self.integration_name},
                exc_info=True,
            )
            raise

    def push(
        self,
        image: str,
        timeout: int = 600,
        **kwargs,
    ) -> Any:
        """
        Push a Docker image to a registry.

        Implements IContainerTool.push interface.

        Args:
            image: Image name and tag (e.g., "myregistry.com/myapp:latest")
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If docker push fails
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot push image",
                extra={"error": error, "name": self.integration_name},
            )
            raise RuntimeError(error)

        try:
            logger.debug(
                "Pushing Docker image",
                extra={"image": image, "name": self.integration_name},
            )

            args = ["push", image]

            result = self._run_integration(args, timeout=timeout)

            if result["returncode"] != 0:
                error_msg = f"Docker push failed: {result['stderr']}"
                logger.error(
                    "Docker push failed",
                    extra={
                        "image": image,
                        "stderr": result["stderr"],
                        "name": self.integration_name,
                    },
                )
                raise RuntimeError(error_msg)

            logger.info(
                "Docker image pushed successfully",
                extra={"image": image, "name": self.integration_name},
            )

            return result

        except Exception as e:
            logger.error(
                "Failed to push Docker image",
                extra={"image": image, "error": str(e), "name": self.integration_name},
                exc_info=True,
            )
            raise

    def pull(
        self,
        image: str,
        timeout: int = 600,
        **kwargs,
    ) -> Any:
        """
        Pull a Docker image from a registry.

        Args:
            image: Image name and tag (e.g., "myregistry.com/myapp:latest")
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If docker pull fails
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot pull image",
                extra={"error": error, "name": self.integration_name},
            )
            raise RuntimeError(error)

        try:
            logger.debug(
                "Pulling Docker image",
                extra={"image": image, "name": self.integration_name},
            )

            args = ["pull", image]

            result = self._run_integration(args, timeout=timeout)

            if result["returncode"] != 0:
                error_msg = f"Docker pull failed: {result['stderr']}"
                logger.error(
                    "Docker pull failed",
                    extra={
                        "image": image,
                        "stderr": result["stderr"],
                        "name": self.integration_name,
                    },
                )
                raise RuntimeError(error_msg)

            logger.info(
                "Docker image pulled successfully",
                extra={"image": image, "name": self.integration_name},
            )

            return result

        except Exception as e:
            logger.error(
                "Failed to pull Docker image",
                extra={"image": image, "error": str(e), "name": self.integration_name},
                exc_info=True,
            )
            raise

    def ps(
        self,
        all_containers: bool = False,
        timeout: int = 30,
        **kwargs,
    ) -> Any:
        """
        List Docker containers.

        Args:
            all_containers: Show all containers (default shows just running)
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If docker ps fails
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot list containers",
                extra={"error": error, "name": self.integration_name},
            )
            raise RuntimeError(error)

        try:
            args = ["ps"]

            if all_containers:
                args.append("-a")

            result = self._run_integration(args, timeout=timeout)

            if result["returncode"] != 0:
                error_msg = f"Docker ps failed: {result['stderr']}"
                logger.error(
                    "Docker ps failed",
                    extra={"stderr": result["stderr"], "name": self.integration_name},
                )
                raise RuntimeError(error_msg)

            return result

        except Exception as e:
            logger.error(
                "Failed to list Docker containers",
                extra={"error": str(e), "name": self.integration_name},
                exc_info=True,
            )
            raise
