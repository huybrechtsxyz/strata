#!/usr/bin/env python3
"""
===============================================================================
Script Name   : schema_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to export JSON schemas for all platform config models.
===============================================================================
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Type

import click
from pydantic import BaseModel

from xyz_platform.commands.session.base_session_command import BaseSessionCommand
from xyz_platform.exceptions import PathValidationError


# ---------------------------------------------------------------------------
# Registry of top-level document models → output schema filename
# ---------------------------------------------------------------------------


def _get_schema_registry() -> Dict[str, Type[BaseModel]]:
    """
    Return an ordered dict of {filename_stem: ModelClass} for every
    top-level config document model.

    Imported lazily so the command module loads fast even when the full
    model graph is not needed at import time.
    """
    from xyz_platform.models.configuration_model import ConfigurationModel
    from xyz_platform.models.deployment_model import DeploymentModel
    from xyz_platform.models.environment_model import EnvironmentModel
    from xyz_platform.models.firewall_model import FirewallModel
    from xyz_platform.models.integration_model import IntegrationModel
    from xyz_platform.models.module_model import ModuleModel
    from xyz_platform.models.namespace_model import NamespaceModel
    from xyz_platform.models.provider_model import ProviderModel
    from xyz_platform.models.repository_model import RepositoryModel
    from xyz_platform.models.resource_model import ResourceModel
    from xyz_platform.models.workspace_model import WorkspaceModel

    return {
        "configuration": ConfigurationModel,
        "deployment": DeploymentModel,
        "environment": EnvironmentModel,
        "firewall": FirewallModel,
        "integration": IntegrationModel,
        "module": ModuleModel,
        "namespace": NamespaceModel,
        "provider": ProviderModel,
        "repository": RepositoryModel,
        "resource": ResourceModel,
        "workspace": WorkspaceModel,
    }


class SchemaSessionCommand(BaseSessionCommand):
    """
    Export JSON schemas for all platform configuration models.

    Writes one <name>.schema.json file per top-level config model into the
    target directory (default: .xyz-platform/schemas/).

    Optionally updates .vscode/settings.json with yaml.schemas entries so
    that the VS Code YAML extension validates config files automatically.
    """

    def __init__(
        self,
        work_path: Optional[str] = None,
        output_dir: Optional[str] = None,
        editor: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        """
        Initialize the schema export command.

        Args:
            work_path: Root working directory
            output_dir: Directory to write schema files into (default: .xyz-platform/schemas)
            editor: Editor integration to activate (e.g. 'vscode'). When set to
                'vscode', updates .vscode/settings.json with yaml.schemas entries.
            output: Output format (json, text)
            verbose: Enable verbose output
            quiet: Suppress all console output
        """
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
            require_session=False,  # Schema export does not require an active session
        )
        self._output_dir: Optional[Path] = Path(output_dir) if output_dir else None
        self._editor = editor
        self._schema_results: List[Dict] = []

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(self) -> bool:
        """
        Execute the schema export command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            # Initialize
            if not self._initialize(operation="session_schemas"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="session_schemas", success=False)
                return False

            # Before
            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="session_schemas", success=False)
                return False

            # Generate and write schemas
            registry = _get_schema_registry()
            for name, model_class in registry.items():
                schema_path = self._output_dir / f"{name}.schema.json"
                try:
                    schema = model_class.model_json_schema()
                    # Ensure $schema draft is set for VS Code compatibility
                    schema.setdefault(
                        "$schema",
                        "https://json-schema.org/draft/2020-12/schema",
                    )
                    schema_path.write_text(
                        json.dumps(schema, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    self._schema_results.append(
                        {
                            "name": name,
                            "model": model_class.__name__,
                            "path": str(schema_path),
                            "success": True,
                            "error": None,
                        }
                    )
                    self.logger.debug(
                        "Schema written",
                        extra={"schema_name": name, "path": str(schema_path)},
                    )
                except Exception as exc:
                    error_msg = f"Failed to generate schema for '{name}': {exc}"
                    self.logger.error(error_msg)
                    self._errors.append(error_msg)
                    self._schema_results.append(
                        {
                            "name": name,
                            "model": model_class.__name__,
                            "path": str(schema_path),
                            "success": False,
                            "error": str(exc),
                        }
                    )

            # Optionally update editor integration settings
            if self._editor and self._editor.lower() == "vscode":
                self._update_vscode_settings()

            # After
            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="session_schemas", success=False)
                return False

            # Finalize
            overall_success = all(r["success"] for r in self._schema_results)
            if not self._finalize(operation="session_schemas", success=overall_success):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return overall_success

        except Exception as e:
            error_msg = f"Failed to export schemas: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(operation="session_schemas", success=False)
            return False

    # ------------------------------------------------------------------
    # Lifecycle overrides
    # ------------------------------------------------------------------

    def _initialize(self, operation: str = None) -> bool:
        """
        Initialize schema export — resolve and create the output directory.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first (REQUIRED)
        if not super()._initialize(operation=operation):
            return False

        # Resolve output directory (default: <work_path>/.xyz-platform/schemas)
        if self._output_dir is None:
            self._output_dir = self._work_path / ".xyz-platform" / "schemas"
        elif not self._output_dir.is_absolute():
            # User supplied a relative --output-dir: resolve against work_path
            self._output_dir = self._work_path / self._output_dir

        self._output_dir = self._output_dir.resolve()

        # Validate output-dir semantics before attempting mkdir
        try:
            if self._output_dir.exists() and not self._output_dir.is_dir():
                raise PathValidationError(
                    option="--output-dir",
                    provided=str(self._output_dir),
                    expected="directory path",
                    resolved=str(self._output_dir),
                    work_path=str(self._work_path),
                    message=(
                        f"--output-dir must be a directory path, "
                        f"but an existing file was provided: '{self._output_dir}'"
                    ),
                )
        except PathValidationError as exc:
            self.logger.error(str(exc))
            self._errors.append(exc.message)
            return False

        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            path_exc = PathValidationError(
                option="--output-dir",
                provided=str(self._output_dir),
                expected="writable directory path",
                resolved=str(self._output_dir),
                work_path=str(self._work_path),
                message=(
                    f"Cannot create schema output directory "
                    f"'{self._output_dir}': {exc}"
                ),
            )
            error_msg = path_exc.message
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        self.logger.debug(
            "Schema session command initialized",
            extra={
                "command_class": self.__class__.__name__,
                "output_dir": str(self._output_dir),
                "editor": self._editor,
            },
        )

        return True

    def _before_execute(self) -> bool:
        """
        Validate pre-conditions for schema export.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first (REQUIRED)
        if not super()._before_execute():
            return False

        # Ensure the output directory is writable
        if not self._output_dir.exists():
            error_msg = f"Schema output directory does not exist: {self._output_dir}"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        if not self._output_dir.is_dir():
            error_msg = f"Schema output path is not a directory: {self._output_dir}"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        self.logger.debug(
            "Schema session pre-execution validation passed",
            extra={"output_dir": str(self._output_dir)},
        )

        return True

    def _after_execute(self) -> bool:
        """
        Populate output data and render console feedback.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        ok_count = sum(1 for r in self._schema_results if r["success"])
        fail_count = len(self._schema_results) - ok_count

        self._output_data = {
            "output_dir": str(self._output_dir),
            "editor": self._editor,
            "schemas": self._schema_results,
            "summary": {
                "total": len(self._schema_results),
                "written": ok_count,
                "failed": fail_count,
            },
        }

        if self._is_console_output():
            click.echo(
                f"\n📐  Schema export ({len(self._schema_results)} models) → {self._output_dir}"
            )
            for result in self._schema_results:
                icon = "✅" if result["success"] else "❌"
                label = f"{result['name']}.schema.json"
                suffix = f"  ← {result['error']}" if result["error"] else ""
                click.echo(f"    {icon}  {label:<40}{suffix}")
            click.echo("")
            click.echo(f"    Written: {ok_count}  |  Failed: {fail_count}")
            if self._editor and self._editor.lower() == "vscode":
                click.echo(
                    f"    VS Code settings updated: {self._work_path / '.vscode' / 'settings.json'}"
                )
            click.echo("")

        self.logger.debug(
            "Schema session command post-execution",
            extra={
                "command_class": self.__class__.__name__,
                "written": ok_count,
                "failed": fail_count,
            },
        )

        # Call parent last (REQUIRED)
        return super()._after_execute()

    def _finalize(
        self, operation: str = None, success: bool = True, show_footer: bool = True
    ) -> bool:
        """
        Render structured output then delegate to base.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        if self._is_structured_output():
            if self._output_format == "json":
                envelope = {
                    "success": bool(success),
                    "command": operation or "",
                    **self._output_data,
                    "errors": self._errors,
                }
                click.echo(json.dumps(envelope, indent=2, default=str))
            else:  # text
                for result in self._schema_results:
                    status = "ok" if result["success"] else "error"
                    click.echo(f"{result['name']}  {status}  {result['path']}")
                if self._errors:
                    for err in self._errors:
                        click.echo(f"error: {err}")

        self.logger.debug(
            "Schema session command finalized",
            extra={"command_class": self.__class__.__name__},
        )

        # Call parent last — suppress footer (display command)
        return super()._finalize(
            operation=operation, success=success, show_footer=False
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _update_vscode_settings(self) -> None:
        """
        Merge yaml.schemas entries into .vscode/settings.json.

        Creates the file/folder if they do not exist. Existing entries for
        unrelated settings are preserved.
        """
        vscode_dir = self._work_path / ".vscode"
        settings_path = vscode_dir / "settings.json"

        try:
            vscode_dir.mkdir(parents=True, exist_ok=True)

            # Load existing settings or start fresh
            if settings_path.exists():
                try:
                    settings: Dict = json.loads(
                        settings_path.read_text(encoding="utf-8")
                    )
                except json.JSONDecodeError:
                    self.logger.warning(
                        "Could not parse existing .vscode/settings.json — creating fresh",
                        extra={"path": str(settings_path)},
                    )
                    settings = {}
            else:
                settings = {}

            # Build yaml.schemas mapping: glob pattern → schema path (relative)
            yaml_schemas: Dict[str, object] = settings.get("yaml.schemas", {})
            for result in self._schema_results:
                if not result["success"]:
                    continue
                # Schema path relative to work_path (VS Code expects relative or absolute)
                rel_schema = (
                    Path(result["path"]).relative_to(self._work_path).as_posix()
                )
                name = result["name"]
                # Default glob patterns per config document type
                glob = f"config/{name}s/*.yaml"
                yaml_schemas[rel_schema] = glob

            settings["yaml.schemas"] = yaml_schemas
            settings_path.write_text(
                json.dumps(settings, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            self.logger.debug(
                "VS Code settings updated",
                extra={"path": str(settings_path), "schema_count": len(yaml_schemas)},
            )

        except Exception as exc:
            # Non-fatal — log and continue
            self.logger.warning(
                f"Failed to update .vscode/settings.json: {exc}",
                extra={"path": str(settings_path)},
            )
            self._messages.append(f"Warning: could not update VS Code settings: {exc}")
