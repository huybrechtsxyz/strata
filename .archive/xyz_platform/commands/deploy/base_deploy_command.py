#!/usr/bin/env python3
"""
===============================================================================
Script Name   : base_deploy_command.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Base class for deploy commands.
===============================================================================
"""

from abc import abstractmethod
from typing import Optional

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.controllers.workspace_controller import WorkspaceController
from xyz_platform.models.common_models import PlatformKind
from xyz_platform.services.configuration_service import ConfigurationService
from xyz_platform.services.deployment_service import DeploymentService


class BaseDeployCommand(BaseCommand):
    """Base class for deploy command implementations."""

    def __init__(
        self,
        file: str = None,
        work_path: str = None,
        no_hooks: bool = False,
        output: str = None,
        verbose: bool = None,
        quiet: bool = None,
    ):
        super().__init__(
            file_path=file,
            work_path=work_path,
            no_hooks=no_hooks,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )

        self._deployment_service: Optional[DeploymentService] = None
        self._configuration_service: Optional[ConfigurationService] = None

    @abstractmethod
    def execute(self) -> bool:
        raise NotImplementedError

    def _initialize(self, operation: str = None) -> bool:
        """Initialize command and workspace paths."""
        if not super()._initialize(operation=operation):
            return False

        workspace_controller = WorkspaceController()
        self._build_path = workspace_controller.get_workspace_buildpath(self._work_path)
        self._object_path = workspace_controller.get_workspace_objectpath(
            self._work_path
        )

        self.logger.debug(
            "Deploy command initialized",
            extra={
                "build_path": str(self._build_path),
                "object_path": str(self._object_path),
                "file_path": str(self._file_path) if self._file_path else None,
            },
        )
        return True

    def _before_execute(self) -> bool:
        """Load and validate deployment + configuration for deploy commands."""
        if not super()._before_execute():
            return False

        if not self._file_path:
            self._errors.append("No deployment file specified. Use --file.")
            return False

        workspace_controller = WorkspaceController()
        self._configuration_service = workspace_controller.get_configuration_service()

        unknown_service, load_errors = workspace_controller.load_platform_file(
            platform_file=self._file_path,
            expected_kind=PlatformKind.DEPLOYMENT,
            work_path=self._work_path,
        )
        if unknown_service is None or load_errors:
            self._errors.extend(load_errors)
            return False

        deploy_service, validation_errors = workspace_controller.load_platform_service(
            unknown_service=unknown_service
        )
        if deploy_service is None or validation_errors:
            self._errors.extend(validation_errors)
            return False

        success, errors = workspace_controller.load_deployment_configuration(
            deployment_data=unknown_service.data,
            work_path=self._work_path,
        )
        if not success:
            self._errors.extend(errors)
            return False

        self._deployment_service, validation_errors = (
            workspace_controller.validate_platform_service(
                known_service=deploy_service,
                configuration_service=self._configuration_service,
                work_path=self._work_path,
            )
        )
        if self._deployment_service is None or validation_errors:
            self._errors.extend(validation_errors)
            return False

        return True

    def _after_execute(self) -> bool:
        """Post-execution hook — override in subclasses as needed."""
        return super()._after_execute()
