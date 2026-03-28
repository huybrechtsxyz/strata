#!/usr/bin/env python3
"""
===============================================================================
Script Name   : topic_help_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to display help for a specific topic.
===============================================================================
"""

from pathlib import Path
from typing import Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.utils import system


class HelpCommand(BaseCommand):
    """
    Display detailed help information for a specific topic.

    This command provides comprehensive documentation for various
    platform topics including commands, concepts, and configuration.
    """

    OPERATION = "help_topic"

    def __init__(
        self,
        topic: Optional[str] = None,
        cli_context=None,
    ):
        """
        Initialize the topic help command.

        Args:
            topic: The help topic to display (e.g., 'deployment', 'workspace', 'build')
            cli_context: Click context object (provides access to main CLI group)
        """
        super().__init__()
        self._topic_name = topic
        self._cli_context = cli_context

    # Get help from main CLI group
    def get_main_help(self) -> str:
        """
        Get help text from the main CLI group.

        Returns:
            str: Main CLI help text
        """
        if self._cli_context:
            # Get the root/main command
            root_ctx = self._cli_context.find_root()
            return root_ctx.get_help()
        return ""

    # Declare required integrations for this command
    def get_required_integrations(self):
        """
        Declare required integrations for this command.

        Returns:
            Dict[str, str]: Required integrations with operation descriptions
        """
        return {}

    # Execute the command
    def execute(self) -> bool:
        """
        Execute the help command - display topic documentation.

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

            # Show help documentation
            return_code = self._show_helpdoc()
            if return_code == 1:
                self.logger.error(
                    f"Failed to display help for the requested topic in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Failed to display help for the requested topic")
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
            error_msg = (
                f"Failed to display help for topic '{self._topic_name}': {str(e)}"
            )
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    # Initialize (runs AFTER base)
    def _initialize(
        self, require_session: bool = True, show_header: bool = True
    ) -> bool:
        """
        Initialize help command - set up documentation paths.

        Args:
            operation: Optional operation name passed to parent initializer.
            show_header: Whether to show header when calling parent initializer.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._initialize(
            require_session=require_session, show_header=show_header
        ):
            return False

        self.logger.debug(
            "Topic help command initializing",
            extra={
                "command_class": self.__class__.__name__,
                "topic": self._topic_name,
            },
        )

        return True

    # Before execution (runs AFTER base)
    def _before_execute(self) -> bool:
        """
        Validate that the requested topic exists.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._before_execute():
            return False

        self.logger.debug(
            "Topic help command pre-execution validation",
            extra={
                "command_class": self.__class__.__name__,
                "topic": self._topic_name,
            },
        )

        return True

    # After execution (runs BEFORE base)
    def _after_execute(self) -> bool:
        """
        Post-execution cleanup for help command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Help command-specific post-execution logic
        self.logger.debug(
            "Topic help command post-executing validation",
            extra={
                "command_class": self.__class__.__name__,
                "topic": self._topic_name,
            },
        )

        # Call parent last
        return super()._after_execute()

    # Finalize the command execution process (runs BEFORE base)
    def _finalize(
        self,
        success: bool = False,
        show_footer: bool = True,
    ) -> bool:
        """
        Finalize help command execution.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Log help command metrics
        self.logger.debug(
            "Topic help command finalizing",
            extra={
                "command_class": self.__class__.__name__,
                "topic_requested": self._topic_name,
            },
        )

        # Call parent last — prefer provided operation else use help_topic
        return super()._finalize(success=success, show_footer=show_footer)

    # Show help correct documentation
    def _show_helpdoc(self) -> int:
        """
        Load and display the help documentation for a given topic.
        Returns:
            int: 0 if successful, 1 if error occures, 2 if topic not found
        """
        # If no topics available, show basic help
        topics = self._get_helpdoc_list()
        main = self.get_main_help()

        # If no topic provided or topic not found, show list
        if not self._topic_name:
            # echo_begin(message="XYZ Platform Help - Available Topics")
            if main:
                click.echo("")
                click.echo(main)
            if topics and len(topics) > 0:
                click.echo("\nAvailable help topics:")
                for topic in topics:
                    click.echo(f"  • {topic}")
                click.echo("")
            # echo_end()
            return 0

        # Check if topic exists
        if self._topic_name not in topics:
            # echo_begin(message="XYZ Platform Help - Available Topics")
            if main:
                click.echo("")
                click.echo(main)
            click.echo(f"\n❌ Topic '{self._topic_name}' not found.")
            if topics and len(topics) > 0:
                click.echo("\nAvailable help topics:")
                for topic in topics:
                    click.echo(f"  • {topic}")
                click.echo("\nUsage: xyz-platform help topic <topic_name>")
                click.echo("")
            error_msg = f"Help topic not found: {self._topic_name}"
            self._errors.append(error_msg)
            return 2

        # Load and display topic content
        content = self._load_helpdoc_content(self._topic_name)
        if not content:
            if main:
                click.echo("")
                click.echo(main)
            click.echo("")
            click.echo(
                f"❌ No documentation content found for topic: {self._topic_name}"
            )
            if topics and len(topics) > 0:
                click.echo("\nAvailable help topics:")
                for topic in topics:
                    click.echo(f"  • {topic}")
                click.echo("\nUsage: xyz-platform help topic <topic_name>")

            error_msg = f"No documentation content found for topic: {self._topic_name}"
            self._errors.append(error_msg)
            return 2

        # Show content via pager
        click.echo("")
        click.echo_via_pager(content)
        return 0

    # List all available documentation topics
    def _get_helpdoc_list(self) -> list[str]:
        """List all available documentation topics."""
        try:
            docs_path = self._get_helpdoc_path()
            return sorted([f.stem for f in docs_path.glob("*.txt")])
        except FileNotFoundError:
            return []

    # Get the documentation path
    def _get_helpdoc_path(self) -> Path:
        """Get the path to the documentation directory."""
        docs_path = system.get_data_path()
        if not docs_path.exists():
            raise FileNotFoundError(
                f"Help documentation directory not found: {docs_path}"
            )
        return docs_path

    # Read documentation content
    def _load_helpdoc_content(self, topic: str) -> Optional[str]:
        """Load documentation content for a given topic."""
        try:
            docs_path = self._get_helpdoc_path()
            doc_file = docs_path / f"{topic}.txt"

            if not doc_file.exists():
                return None

            return doc_file.read_text(encoding="utf-8")
        except Exception:
            return None
