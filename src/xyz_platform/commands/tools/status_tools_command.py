#!/usr/bin/env python3
"""
===============================================================================
Script Name   : status_tools_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to display status of required CLI tools.
===============================================================================
"""

import json
from typing import Optional, List, Dict

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.integrations.factory import IntegrationFactory
from xyz_platform.models.integration_model import IntegrationModel


class StatusToolsCommand(BaseCommand):
    """
    Display status of required CLI tools.

    Derives the tool list dynamically from the IntegrationFactory registry.
    Only integrations that expose a COMMAND class attribute (local CLI
    binaries) are checked. Service-based integrations (Vault, Azure Key
    Vault, etc.) are excluded because they have no local binary to probe.
    """

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
    ):
        """
        Initialize the tools status command.

        Args:
            work_path: Root working directory
            output: Output format (json, text)
            verbose: Enable verbose output
        """
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
        )
        self._tool_results: List[Dict] = []

    # Execute the command

    def execute(self) -> bool:
        """
        Execute the tools status command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            # Initialize
            if not self._initialize():
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

            # Check each registered CLI tool using the integration's own methods
            for integration_type, integration_class in sorted(
                IntegrationFactory.get_registered_types().items()
            ):
                if not hasattr(integration_class, "COMMAND"):
                    continue  # Skip service-only integrations (Vault, KeyVault, etc.)

                config = IntegrationModel(name=integration_type, type=integration_type)
                integration = integration_class(config=config)

                available = integration.is_available()
                version = integration.get_version() if available else None
                label = integration_type.replace("-", " ").title()

                self._tool_results.append(
                    {
                        "name": integration_type,
                        "label": label,
                        "command": integration_class.COMMAND,
                        "available": available,
                        "version": version,
                    }
                )
                self.logger.debug(
                    f"Tool check: {integration_type}",
                    extra={"available": available, "version": version},
                )

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
            error_msg = f"Failed to get tools status: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    # Initialize (runs AFTER base)

    def _initialize(self) -> bool:
        """
        Initialize tools status command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._initialize(operation="tools_status"):
            return False

        self.logger.debug(
            "Tools status command initialized",
            extra={"command_class": self.__class__.__name__},
        )

        return True

    # Before execution (runs AFTER base)

    def _before_execute(self) -> bool:
        """
        Validate pre-conditions for the tools status command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._before_execute():
            return False

        return True

    # After execution (runs BEFORE base)

    def _after_execute(self) -> bool:
        """
        Populate output data and render console feedback.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        available_count = sum(1 for t in self._tool_results if t["available"])
        missing_count = len(self._tool_results) - available_count

        self._output_data = {
            "tools": self._tool_results,
            "summary": {
                "total": len(self._tool_results),
                "available": available_count,
                "missing": missing_count,
            },
        }

        if self._is_console_output():
            click.echo(f"\n🔧  Tools status ({len(self._tool_results)} checked):")
            for tool in self._tool_results:
                icon = "✅" if tool["available"] else "❌"
                version = tool.get("version") or "not found"
                click.echo(f"    {icon}  {tool['label']:<20} {version}")
            click.echo("")
            click.echo(f"    Available: {available_count}  |  Missing: {missing_count}")
            click.echo("")

        self.logger.debug(
            "Tools status command post-execution",
            extra={
                "command_class": self.__class__.__name__,
                "total": len(self._tool_results),
                "available": available_count,
            },
        )

        # Call parent last
        return super()._after_execute()

    # Finalize the command execution process (runs BEFORE base)

    def _finalize(self, success: bool) -> bool:
        """
        Finalize tools status command execution.

        Renders structured output (json/text) when --output flag is set,
        then delegates to base with footer suppressed (display command).

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        if self._is_structured_output():
            if self._output_format == "json":
                envelope = {
                    "success": bool(success),
                    "command": "tools_status",
                    **self._output_data,
                    "errors": self._errors,
                }
                click.echo(json.dumps(envelope, indent=2, default=str))
            else:  # text
                for tool in self._tool_results:
                    status = "ok" if tool["available"] else "missing"
                    version = tool.get("version") or ""
                    click.echo(f"{tool['name']}  {status}  {version}".rstrip())
                if self._errors:
                    for err in self._errors:
                        click.echo(f"error: {err}")

        self.logger.debug(
            "Tools status command finalized",
            extra={"command_class": self.__class__.__name__},
        )

        # Call parent last — suppress footer for display commands
        return super()._finalize(
            operation="tools_status", success=success, show_footer=False
        )
