#!/usr/bin/env python3
"""
===============================================================================
Script Name   : base_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Base session command class for the XYZ Platform.
                Provides common functionality for all session-related commands.
===============================================================================
"""

from abc import abstractmethod

from xyz_platform.commands.base_command import BaseCommand


class BaseSessionCommand(BaseCommand):
    """
    Base class for session commands.

    This class provides common functionality for all session-related commands
    such as session status, logging, reset, etc.

    Inherits from BaseCommand and follows the lifecycle pattern:

    Execution Order:
    1. BaseCommand._initialize()           [Base sets up paths, timing]
    2. BaseSessionCommand._initialize()    [Session-specific setup]
    3. BaseCommand._before_execute()       [Base pre-execution]
    4. BaseSessionCommand._before_execute()[Session-specific pre-execution]
    5. execute()                           [Actual command logic]
    6. BaseSessionCommand._after_execute() [Session-specific cleanup]
    7. BaseCommand._after_execute()        [Base post-execution]
    8. BaseSessionCommand._finalize()      [Session-specific finalization]
    9. BaseCommand._finalize()             [Base logs duration, flushes]
    """

    def __init__(
        self,
        work_path: str = None,
        output: str = None,
        verbose: bool = None,
        quiet: bool = None,
    ):
        """
        Initialize the session command.

        Args:
            output: Output format (json, yaml, text)
            verbose: If True, enable verbose output
            quiet: If True, suppress all console output
        """
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )

    @abstractmethod
    def execute(self) -> bool:
        """
        Execute the command logic.

        Must be implemented by subclasses.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        raise NotImplementedError("Subclasses must implement the execute() method.")

    def _initialize(self, operation: str = None) -> bool:
        """
        Initialize session-specific logic.
        Called AFTER BaseCommand._initialize().

        Use this to:
        - Validate session-specific parameters
        - Set up session-specific state
        - Initialize session-specific resources

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first (REQUIRED)
        if not super()._initialize(operation=operation):
            return False

        self.logger.debug(
            "Session command initialized",
            extra={"command_class": self.__class__.__name__},
        )

        return True

    def _before_execute(self) -> bool:
        """
        Execute session-specific pre-command logic.
        Called AFTER BaseCommand._before_execute().

        Use this to:
        - Load session configurations
        - Validate session preconditions
        - Set up resources needed for session operations

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first (REQUIRED)
        if not super()._before_execute():
            return False

        self.logger.debug(
            "Session command pre-execution",
            extra={"command_class": self.__class__.__name__},
        )

        return True

    def _after_execute(self) -> bool:
        """
        Execute session-specific post-command logic.
        Called BEFORE BaseCommand._after_execute().

        Use this to:
        - Clean up session resources
        - Save session state or results
        - Send session-related notifications

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Session command post-execution",
            extra={"command_class": self.__class__.__name__},
        )

        # Call parent last (REQUIRED)
        return super()._after_execute()

    def _finalize(self, operation: str = None, success: bool = None) -> bool:
        """
        Finalize session-specific logic.
        Called BEFORE BaseCommand._finalize().

        Use this to:
        - Log session-specific metrics
        - Release session-specific resources
        - Final session cleanup

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Session command finalized",
            extra={"command_class": self.__class__.__name__},
        )

        # Call parent last (REQUIRED)
        return super()._finalize(operation=operation, success=success)
