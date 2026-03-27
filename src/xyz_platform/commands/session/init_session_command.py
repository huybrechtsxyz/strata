#!/usr/bin/env python3
"""
===============================================================================
Script Name   : init_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to initialize a new XYZ Platform session workspace.
===============================================================================
"""

import click

from typing import Optional

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.logger.logger import configure_logging


class InitSessionCommand(BaseCommand):
    """
    Initialize a new XYZ Platform session workspace.

    This command prepares a workspace for XYZ Platform development by:
    - Creating the workspace folder structure
    - Generating a VSCode workspace file (from template)
    - Creating .xyz-platform configuration directory
    - Initializing session.yaml state file (from template)
    """

    OPERATION = "session_init"

    def __init__(
        self,
        name: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
        editor: Optional[str] = None,
    ):
        """
        Initialize the session init command.

        Args:
            name: Name of the workspace
            work_path: Root working directory (defaults to current directory)
            output: Output format (json, yaml, text)
            verbose: Enable verbose output
            quiet: Disable all console output
            editor: Editor integration to activate (e.g. 'vscode'). When set to
                'vscode', creates the .code-workspace file.
        """
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )

        self._workspace_name = name
        self._editor = editor
        self._created_paths = {}

    def get_required_integrations(self):
        """
        Declare required integrations for this command.

        Returns:
            Dict[str, str]: Required integrations with operation descriptions
        """
        return {}

    def execute(self) -> bool:
        """
        Execute the session init command - create workspace structure.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            # Initialize
            if not self._initialize(require_session=False):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            # Before
            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            # Execute via controller
            success, self._created_paths = self._session_controller.initialize_session(
                workspace_name=self._workspace_name,
                work_path=self._work_path,
                data_path=self._data_path,
                editor=self._editor,
            )

            # Copy controller errors/messages to command
            self._messages.extend(self._session_controller.get_messages())
            self._errors.extend(self._session_controller.get_errors())

            if not success:
                self.logger.error(
                    f"Session initialization failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Session initialization failed")
                self._finalize(success=False)
                return False

            # After
            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(success=False)
                return False

            # Finalize
            if not self._finalize(success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to initialize session workspace: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _initialize(
        self, require_session: bool = True, show_header: bool = True
    ) -> bool:
        """
        Initialize session init command - validate parameters.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._initialize(show_header=show_header):
            return False

        self.logger.debug(
            "Session init command initialized",
            extra={
                "command_class": self.__class__.__name__,
                "workspace_name": self._workspace_name,
                "work_path": str(self._work_path),
                "editor": self._editor,
            },
        )

        return True

    def _before_execute(self) -> bool:
        """
        Validate workspace parameters before execution.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._before_execute():
            return False

        self.logger.debug(
            "Session init pre-execution validation passed",
            extra={"command_class": self.__class__.__name__},
        )

        return True

    def _after_execute(self) -> bool:
        """
        Post-execution logic - display created paths.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Session init post-execution",
            extra={"command_class": self.__class__.__name__},
        )

        # Display created paths
        if not self._is_quiet() and self._created_paths:
            # Always populate structured output data
            self._output_data = {
                k: str(v) for k, v in self._created_paths.items() if v is not None
            }
            self._output_data["workspace_name"] = self._workspace_name

            if self._is_console_output():
                click.echo("\n📁  Created session structure:")
                if self._created_paths.get("session_folder"):
                    click.echo(
                        f"    • Session folder: {self._created_paths['session_folder']}"
                    )
                if self._created_paths.get("session_file"):
                    click.echo(
                        f"    • Session file:   {self._created_paths['session_file']}"
                    )
                if self._created_paths.get("workspace_file"):
                    click.echo(
                        f"    • Workspace file: {self._created_paths['workspace_file']}"
                    )
                if self._created_paths.get("logging_config"):
                    click.echo(
                        f"    • Logging config: {self._created_paths['logging_config']}"
                    )

        # Configure logging with the new logging.yaml
        if self._created_paths.get("logging_config"):
            try:
                logging_config_path = str(self._created_paths["logging_config"])
                self.logger.info(f"Configuring logging with: {logging_config_path}")
                configure_logging(config_path=logging_config_path)

                if self._is_console_output():
                    click.echo("\n✅  Logging configured successfully\n")

                self._messages.append(f"Logging configured with: {logging_config_path}")
            except Exception as e:
                error_msg = f"Failed to configure logging: {str(e)}"
                self.logger.warning(error_msg)
                self._messages.append(error_msg)

        # Call parent last
        return super()._after_execute()

    def _finalize(
        self,
        success: bool = False,
        show_footer: bool = True,
    ) -> bool:
        """
        Finalize session init command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Session init command finalized",
            extra={"command_class": self.__class__.__name__},
        )

        # Call parent last
        return super()._finalize(success=success)
