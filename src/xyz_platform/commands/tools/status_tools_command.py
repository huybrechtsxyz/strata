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
from typing import Optional, List, Dict, Tuple

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.integrations.factory import IntegrationFactory
from xyz_platform.models.integration_model import IntegrationModel
from xyz_platform.services.configuration_service import ConfigurationService


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
        env_path: Optional[str] = None,
        env_file: Optional[str] = None,
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
            env_path=env_path,
            env_file=env_file,
            output=output,
            verbose=verbose or False,
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

            # Build a lookup map: integration type -> IntegrationModel from loaded config
            config_integration_map: Dict[str, IntegrationModel] = {}
            config_service = ConfigurationService.get_instance()
            config_model = (
                config_service.get_configuration()
                if config_service.is_validated()
                else None
            )
            if config_model and config_model.spec and config_model.spec.integrations:
                for int_model in config_model.spec.integrations:
                    config_integration_map[int_model.type] = int_model

            # Check each registered CLI tool using the integration's own methods
            for integration_type, integration_class in sorted(
                IntegrationFactory.get_registered_types().items()
            ):
                if not hasattr(integration_class, "COMMAND"):
                    continue  # Skip service-only integrations (Vault, KeyVault, etc.)

                # Use real config from loaded configuration if available, else stub

                int_config = config_integration_map.get(
                    integration_type,
                    IntegrationModel(
                        name=integration_type,
                        type=integration_type,
                        description="",
                        validation=None,
                        authentication=None,
                        endpoints=None,
                        lifecycle=None,
                    ),
                )
                integration = integration_class(config=int_config)

                available = integration.is_available()
                version = integration.get_version() if available else None
                label = integration_type.replace("-", " ").title()

                # Read min/max version from config.validation (set in platform YAML)
                min_version = (
                    int_config.validation.min_version if int_config.validation else None
                )
                max_version = (
                    int_config.validation.max_version if int_config.validation else None
                )

                # Check whether the detected version satisfies declared constraints
                version_ok, version_detail = self._check_version_constraints(
                    version, min_version, max_version
                )

                integration_command = getattr(integration_class, "COMMAND", None)

                self._tool_results.append(
                    {
                        "name": integration_type,
                        "label": label,
                        "command": integration_command,
                        "available": available,
                        "version": version,
                        "min_version": min_version,
                        "max_version": max_version,
                        "version_ok": version_ok,
                        "version_detail": version_detail,
                    }
                )
                self.logger.debug(
                    f"Tool check: {integration_type}",
                    extra={
                        "available": available,
                        "version": version,
                        "min_version": min_version,
                        "max_version": max_version,
                        "version_ok": version_ok,
                        "version_detail": version_detail,
                    },
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

    def _initialize(
        self, require_session: bool = True, show_header: bool = True
    ) -> bool:
        """
        Initialize tools status command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._initialize(show_header=show_header):
            return False

        self.logger.debug(
            "Tools status command initializing",
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

        self.logger.debug(
            "Tools status command pre-execution validating",
            extra={"command_class": self.__class__.__name__},
        )

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
        # Tools that are available but fail their version constraints
        version_fail_count = sum(
            1
            for t in self._tool_results
            if t["available"]
            and (t.get("min_version") or t.get("max_version"))
            and not t.get("version_ok")
        )

        self._output_data = {
            "tools": self._tool_results,
            "summary": {
                "total": len(self._tool_results),
                "available": available_count,
                "missing": missing_count,
                "version_fail": version_fail_count,
            },
        }

        if self._is_console_output():
            click.echo(f"\n🔧  Tools status ({len(self._tool_results)} checked):")
            for tool in self._tool_results:
                available = tool["available"]
                min_v = tool.get("min_version")
                max_v = tool.get("max_version")
                has_constraints = bool(min_v or max_v)
                version_ok = tool.get("version_ok", True)
                version_str = tool.get("version") or "not found"

                # Availability icon: warn if available but version constraint fails
                if not available:
                    icon = "❌"
                elif has_constraints and not version_ok:
                    icon = "⚠️ "
                else:
                    icon = "✅"

                # Build version + constraint display
                parts = [version_str]
                if has_constraints:
                    constraints = []
                    if min_v:
                        constraints.append(f">={min_v}")
                    if max_v:
                        constraints.append(f"<={max_v}")
                    parts.append(f"[{', '.join(constraints)}]")
                    if available:
                        detail = tool.get("version_detail") or ""
                        parts.append("✅" if version_ok else f"❌ {detail}")

                click.echo(f"    {icon}  {tool['label']:<20} {'  '.join(parts)}")
            click.echo("")
            summary_parts = [
                f"Available: {available_count}",
                f"Missing: {missing_count}",
            ]
            if version_fail_count:
                summary_parts.append(f"Version failures: {version_fail_count}")
            click.echo(f"    {'  |  '.join(summary_parts)}")
            click.echo("")

        self.logger.debug(
            "Tools status command post-executing",
            extra={
                "command_class": self.__class__.__name__,
                "total": len(self._tool_results),
                "available": available_count,
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
                    min_v = tool.get("min_version") or ""
                    max_v = tool.get("max_version") or ""
                    has_constraints = bool(min_v or max_v)
                    if has_constraints and tool["available"]:
                        version_status = (
                            "version_ok"
                            if tool.get("version_ok")
                            else f"version_fail:{tool.get('version_detail', '')}"
                        )
                    else:
                        version_status = ""
                    click.echo(
                        f"{tool['name']}  {status}  {version}  {min_v}  {max_v}  {version_status}".rstrip()
                    )
                if self._errors:
                    for err in self._errors:
                        click.echo(f"error: {err}")

        self.logger.debug(
            "Tools status command finalizing",
            extra={"command_class": self.__class__.__name__},
        )

        # Call parent last — prefer provided operation else use tools_status
        return super()._finalize(success=success, show_footer=show_footer)

    # Version constraint check helper

    def _check_version_constraints(
        self,
        version_str: Optional[str],
        min_version: Optional[str],
        max_version: Optional[str],
    ) -> Tuple[bool, str]:
        """
        Check whether version_str satisfies min/max constraints.

        Returns:
            (ok, detail) where detail is an empty string on success or a
            human-readable reason on failure.
        """
        # No constraints defined — always OK
        if not min_version and not max_version:
            return True, ""

        # Tool not available — constraint not applicable
        if not version_str:
            return False, "version unknown"

        try:
            from packaging import version as pkg_version

            ver = pkg_version.parse(version_str)

            if min_version:
                if ver < pkg_version.parse(min_version):
                    return False, f"below minimum {min_version}"

            if max_version:
                if ver > pkg_version.parse(max_version):
                    return False, f"above maximum {max_version}"

            return True, "ok"

        except Exception as e:
            return False, f"version parse error: {e}"
