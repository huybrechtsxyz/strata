#!/usr/bin/env python3
"""
===============================================================================
Script Name   : run_validate_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Validate command implementation for the XYZ Platform CLI.
===============================================================================
"""

from pathlib import Path
from typing import Optional

import click

from xyz_platform.commands.validate.base_validate_command import BaseValidateCommand
from xyz_platform.controllers.workspace_controller import WorkspaceController
from xyz_platform.services.base_service import BaseService
from xyz_platform.services.configuration_service import ConfigurationService


class RunValidateCommand(BaseValidateCommand):
    """
    Command to validate any platform artifact file.

    Detects the file kind automatically (workspace, namespace, deployment, …)
    and validates it against the appropriate Pydantic model schema plus any
    dynamic cross-reference rules.

    Lifecycle
    ---------
    1. _initialize()         — resolve paths, set up state
    2. _before_execute()     — resolve platform_file, before-validate hook
    3. _perform_validation() — load & validate via WorkspaceController
    4. _after_execute()      — after-validate hook
    5. _finalize()           — log summary, emit structured output if requested

    Exit codes (via handle_command_exit):
        0 — validation passed
        1 — execution failure (initialization, hooks, IO)
        3 — file processed but validation errors found
    """

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        no_hooks: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file_path=file,
            work_path=work_path,
            no_hooks=no_hooks,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        # Resolved platform file path (set in _before_execute)
        self._platform_file: Optional[Path] = None
        # Detected platform kind string (set in _perform_validation)
        self._platform_kind: Optional[str] = None
        # Typed service returned by WorkspaceController (set in _perform_validation)
        self.service: Optional[BaseService] = None

    # ── Main entry point ─────────────────────────────────────────────────────

    def execute(self) -> bool:
        """
        Execute the full validation lifecycle.

        Returns:
            True if all lifecycle phases complete without execution errors
            (validation errors are reported separately via _validation_errors).
        """
        try:
            if not self._initialize(operation="validate"):
                self.logger.error("Initialization failed")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="validate", success=False)
                return False

            if not self._before_execute():
                self.logger.error("Pre-execution checks failed")
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution checks failed")
                self._finalize(operation="validate", success=False)
                return False

            if not self._perform_validation():
                self.logger.error("Validation step failed")
                if self._is_console_output():
                    click.echo("\n❌  Validation step failed")
                self._finalize(operation="validate", success=False)
                return False

            if not self._after_execute():
                self.logger.error("Post-execution processing failed")
                if self._is_console_output():
                    click.echo("\n❌  Post-execution processing failed")
                self._finalize(operation="validate", success=False)
                return False

            self._finalize(operation="validate", success=True)
            return True

        except Exception as exc:
            error_msg = f"Validate command raised an unexpected error: {exc}"
            self.logger.exception(
                error_msg,
                extra={
                    "file": str(self._file_path),
                    "error_type": type(exc).__name__,
                },
            )
            self._errors.append(error_msg)
            if self._is_console_output():
                click.echo(f"\n❌  {error_msg}")
            self._finalize(operation="validate", success=False)
            return False

    # ── Lifecycle phases ─────────────────────────────────────────────────────

    def _initialize(self, operation: str = None) -> bool:
        """
        Initialize validate-specific state.

        Calls parent initialization (resolves work_path, etc.) then sets up
        command-specific result holders.

        Returns:
            bool: True if initialization succeeds, False otherwise
        """
        if not super()._initialize(operation=operation):
            return False

        self.logger.debug(
            "RunValidateCommand initialized",
            extra={
                "command_class": self.__class__.__name__,
                "file": str(self._file_path) if self._file_path else None,
            },
        )
        return True

    def _before_execute(self) -> bool:
        """
        Pre-execution checks and platform_file resolution.

        Steps:
        1. Delegate to base (file-arg check, integration validation)
        2. Resolve self._platform_file relative to work_path
        3. Execute before-validate lifecycle hook (unless --no-hooks)

        Returns:
            bool: True if all pre-conditions are met, False otherwise
        """
        if not super()._before_execute():
            return False

        # Set _platform_file — plain paths are joined with work_path, @repo_name/...
        # references are passed as-is (as strings) and resolved inside
        # WorkspaceController.load_platform_file() which builds the repo_map from config.
        file_path_str = str(self._file_path)
        if file_path_str.startswith("@"):
            self._platform_file = (
                file_path_str  # keep raw string; controller resolves it
            )
        else:
            self._platform_file = self._work_path / self._file_path

        self.logger.debug(
            "Platform file resolved",
            extra={"platform_file": str(self._platform_file)},
        )

        # Before-validate lifecycle hook
        if not self._validate_before():
            return False

        return True

    def _after_execute(self) -> bool:
        """
        Post-validation processing and after-validate lifecycle hook.

        Returns:
            bool: True if post-execution processing succeeds, False otherwise
        """
        # After-validate lifecycle hook
        if not self._validate_after():
            return False

        self.logger.debug(
            "RunValidateCommand post-execution",
            extra={
                "validation_errors_count": len(self._validation_errors),
                "kind": self._platform_kind or "unknown",
            },
        )
        return super()._after_execute()

    def _finalize(
        self, operation: str = None, success: bool = None, show_footer: bool = True
    ) -> bool:
        """
        Finalize the validate command.

        Populates structured output data, logs summary, and delegates to
        BaseValidateCommand._finalize().

        Returns:
            bool: True if finalization succeeds, False otherwise
        """
        # Populate structured output envelope data
        self._output_data.update(
            {
                "file": (
                    str(self._platform_file)
                    if self._platform_file
                    else str(self._file_path)
                ),
                "kind": self._platform_kind or "unknown",
                "validation_passed": not self.has_validation_errors(),
                "validation_errors": self._validation_errors,
                "validation_error_count": len(self._validation_errors),
            }
        )
        return super()._finalize(
            operation=operation or "validate",
            success=success,
            show_footer=show_footer,
        )

    # ── Validation core ───────────────────────────────────────────────────────

    def _perform_validation(self) -> bool:
        """
        Main validation logic.

        1. Load the session's merged configuration.yaml (if present) to enable
           deep (dynamic) cross-reference validation.
        2. Delegate to WorkspaceController.load_and_validate_file() which:
           - Detects the file kind (workspace, namespace, deployment, …)
           - Validates schema (Pydantic) + dynamic rules (file refs, repo refs, …)
        3. Store kind + validation errors; display results.

        Returns:
            bool: True if the validation step executed without execution errors.
            Validation findings are stored in self._validation_errors, not here.
        """
        self.logger.info(
            "Performing validation",
            extra={"file": str(self._file_path)},
        )

        # ── Step 1: load merged configuration for deep validation ────────────
        merged_config_file = self._work_path / ".xyz-platform" / "configuration.yaml"
        config_service: ConfigurationService = None
        workspace_controller = WorkspaceController()

        if merged_config_file.exists():
            load_ok, load_errors = workspace_controller.load_configuration(
                work_path=self._work_path,
                file_paths=[str(merged_config_file)],
            )
            if load_ok:
                config_service = ConfigurationService.get_instance()
                self.logger.debug(
                    "Configuration loaded for deep validation",
                    extra={"config": str(merged_config_file)},
                )
            else:
                self.logger.warning(
                    "Configuration load failed — falling back to shallow validation",
                    extra={"errors": load_errors},
                )
                if self._is_console_output():
                    click.echo(
                        "⚠️   Configuration load failed — running shallow validation only"
                    )
        else:
            self.logger.debug(
                "No merged configuration found — running shallow validation only",
                extra={"expected": str(merged_config_file)},
            )
            if self._is_console_output():
                click.echo(
                    "ℹ️   No session configuration found — shallow validation only"
                )

        if self._is_console_output():
            click.echo(f"\n🔍  Validating: {self._platform_file}")

        # ── Step 2: load and validate the platform file ──────────────────────
        try:
            service, errors = workspace_controller.load_and_validate_file(
                platform_file=self._platform_file,
                work_path=str(self._work_path),
                configuration_service=config_service,
            )
        except Exception as exc:
            error_msg = f"Validation failed with an unexpected error: {exc}"
            self.logger.exception(error_msg, extra={"file": str(self._platform_file)})
            self._errors.append(error_msg)
            return False

        self.service = service
        self._validation_errors = list(errors) if errors else []

        # ── Step 3: extract detected kind ────────────────────────────────────
        if service:
            try:
                detected = service.get_kind()
                self._platform_kind = (
                    detected.value if hasattr(detected, "value") else str(detected)
                )
                if self._is_console_output():
                    click.echo(f"ℹ️   Detected kind: {self._platform_kind}")
            except Exception:
                pass  # kind display is best-effort

        # ── Step 4: display results ───────────────────────────────────────────
        if self._validation_errors:
            if self._is_console_output():
                click.echo(f"\n❌  Validation errors ({len(self._validation_errors)}):")
                for err in self._validation_errors:
                    click.echo(f"    • {err}")
        else:
            if self._is_console_output():
                click.echo("\n✅  Validation passed")

        return True

    # ── Lifecycle hooks ───────────────────────────────────────────────────────

    def _validate_before(self) -> bool:
        """
        Before-validate lifecycle hook.

        Called from _before_execute() after platform_file is resolved.
        Executes the 'validate_before' phase of the file's lifecycle model
        (unless --no-hooks is set).

        Returns:
            bool: True if the hook succeeds (or is skipped), False otherwise
        """
        self.logger.debug(
            "Before-validate hook",
            extra={
                "platform_file": str(self._platform_file),
                "no_hooks": self._no_hooks,
            },
        )

        if self._no_hooks:
            return True

        # TODO: extract lifecycle model from self._platform_file, then:
        # lifecycle_controller = LifecycleController()
        # success, errors = lifecycle_controller.execute_phase(
        #     phase_name="validate_before",
        #     lifecycle_model=lifecycle_model,
        #     work_path=self._work_path,
        #     context={"file": str(self._platform_file), "kind": self._platform_kind},
        # )
        return True

    def _validate_after(self) -> bool:
        """
        After-validate lifecycle hook.

        Called from _after_execute() after all validation results are collected.
        Executes the 'validate_after' phase of the file's lifecycle model
        (unless --no-hooks is set).

        Returns:
            bool: True if the hook succeeds (or is skipped), False otherwise
        """
        self.logger.debug(
            "After-validate hook",
            extra={
                "platform_file": str(self._platform_file),
                "no_hooks": self._no_hooks,
                "validation_passed": not self.has_validation_errors(),
            },
        )

        if self._no_hooks:
            return True

        # TODO: extract lifecycle model from self._platform_file, then:
        # lifecycle_controller = LifecycleController()
        # success, errors = lifecycle_controller.execute_phase(
        #     phase_name="validate_after",
        #     lifecycle_model=lifecycle_model,
        #     work_path=self._work_path,
        #     context={
        #         "file": str(self._platform_file),
        #         "kind": self._platform_kind,
        #         "validation_passed": not self.has_validation_errors(),
        #     },
        # )
        return True
