#!/usr/bin/env python3
"""
===============================================================================
Script Name   : lifecycle_controller.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Controller for managing and executing lifecycle hooks.
===============================================================================
"""

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from xyz_platform.logger.logger import get_logger
from xyz_platform.models.common_models import (
    CommonLifecycleModel,
    CommonLifecyclePhaseModel,
)
from xyz_platform.services.base_service import BaseService
from xyz_platform.services.configuration_service import ConfigurationService


class LifecycleController:
    """
    Controller for managing and executing lifecycle hooks.

    Supports two lifecycle model styles used in this project:
    - CommonLifecycleModel  (RootModel): phase lookup via .root dict
    - Attribute-based models: phase lookup via getattr (legacy / backward-compat)

    Phase models are CommonLifecyclePhaseModel instances that carry a .scripts
    list of ScriptPathModel / FilePath entries.
    """

    def __init__(self, enable_templating: bool = True):
        """
        Initialize the lifecycle controller.

        Args:
            enable_templating: Whether to enable $VAR / ${VAR} substitution in scripts
        """
        self.logger = get_logger(__name__)
        self.enable_templating = enable_templating

        self._errors: List[str] = []
        self._messages: List[str] = []

    # ------------------------------------------------------------------
    # Error / message helpers
    # ------------------------------------------------------------------

    def has_errors(self) -> bool:
        """Check if any errors were accumulated."""
        return len(self._errors) > 0

    def get_errors(self) -> List[str]:
        """Get accumulated errors."""
        return self._errors.copy()

    def clear_errors(self) -> None:
        """Clear accumulated errors."""
        self._errors.clear()

    def get_messages(self) -> List[str]:
        """Get accumulated messages."""
        return self._messages.copy()

    def clear_messages(self) -> None:
        """Clear accumulated messages."""
        self._messages.clear()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_configuration_phase(
        self,
        phase_name: str,
        work_path: Path,
        context: dict = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Execute a lifecycle phase defined in the active ConfigurationService model.

        Retrieves the phase model from ConfigurationService.get_lifecycle_phase()
        and delegates to execute_phase().

        Args:
            phase_name: Phase key (e.g. 'config_clean_before')
            work_path: Working directory for script execution
            context: Optional extra context passed as XYZ_* env vars
            progress_callback: Optional callback(script_name, current, total)

        Returns:
            Tuple of (success, errors)
        """
        config_service = ConfigurationService.get_instance()
        phase_model = config_service.get_lifecycle_phase(phase_name)

        if not phase_model:
            self.logger.debug(
                f"Configuration lifecycle phase '{phase_name}' not defined, skipping",
                extra={"phase": phase_name},
            )
            return True, []

        return self.execute_phase(
            phase_name=phase_name,
            lifecycle_model=None,
            work_path=work_path,
            context=context,
            progress_callback=progress_callback,
            phase_model=phase_model,
        )

    def execute_workspace_phase(
        self,
        base_service: BaseService,
        phase_name: str,
        work_path: Path,
        context: dict = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        add_config_model: bool = False,
    ) -> Tuple[bool, List[str]]:
        """
        Execute a lifecycle phase from a service's model (workspace, namespace, etc.).

        Retrieves the lifecycle model from base_service.get_lifecycle_phase() (no args)
        and delegates to execute_phase() with the given phase_name.

        Args:
            base_service: Service whose model carries the lifecycle (e.g. WorkspaceService)
            phase_name: Phase key to execute
            work_path: Working directory for script execution
            context: Optional extra context passed as XYZ_* env vars
            progress_callback: Optional callback(script_name, current, total)
            add_config_model: If True, merge configuration-level phase scripts in

        Returns:
            Tuple of (success, errors)
        """
        lifecycle_model = base_service.get_lifecycle_phase()
        return self.execute_phase(
            phase_name=phase_name,
            lifecycle_model=lifecycle_model,
            work_path=work_path,
            context=context,
            progress_callback=progress_callback,
            add_config_model=add_config_model,
        )

    def execute_phase(
        self,
        phase_name: str,
        lifecycle_model: Optional[object],
        work_path: Path,
        context: dict = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        add_config_model: bool = False,
        phase_model: Optional[CommonLifecyclePhaseModel] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Execute all scripts in a lifecycle phase.

        Phase resolution priority:
        1. Explicit phase_model argument (used by execute_configuration_phase)
        2. lifecycle_model lookup:
           - CommonLifecycleModel (RootModel): phase_model = lifecycle_model.root.get(phase_name)
           - Attribute-based legacy model: phase_model = getattr(lifecycle_model, phase_name)
        3. Optional merge with ConfigurationService phase when add_config_model=True

        Args:
            phase_name: Phase key (e.g. 'bootstrap', 'config_clean_before')
            lifecycle_model: Lifecycle model containing the phase
            work_path: Working directory for script execution
            context: Optional extra context passed as XYZ_* env vars
            progress_callback: Optional callback(script_name, current, total)
            add_config_model: Merge configuration-level phase scripts if True
            phase_model: Direct phase model (bypasses lifecycle_model lookup)

        Returns:
            Tuple of (success, errors)
        """
        errors: List[str] = []

        # ------------------------------------------------------------------
        # Resolve phase model from lifecycle_model when not supplied directly
        # ------------------------------------------------------------------
        if not phase_model and lifecycle_model:
            if isinstance(lifecycle_model, CommonLifecycleModel):
                # New project model: phases stored in RootModel .root dict
                phase_model = lifecycle_model.root.get(phase_name)
            elif hasattr(lifecycle_model, "root") and isinstance(
                lifecycle_model.root, dict
            ):
                # Generic RootModel – dict-based phase lookup
                phase_model = lifecycle_model.root.get(phase_name)
            elif hasattr(lifecycle_model, phase_name):
                # Legacy / attribute-based lifecycle model
                phase_model = getattr(lifecycle_model, phase_name)
            else:
                self.logger.debug(
                    "Lifecycle model does not contain the specified phase",
                    extra={
                        "phase": phase_name,
                        "lifecycle_model": type(lifecycle_model).__name__,
                    },
                )
                return True, []

        # ------------------------------------------------------------------
        # Optionally merge configuration-level scripts
        # ------------------------------------------------------------------
        if add_config_model:
            config_service = ConfigurationService.get_instance()
            config_phase = config_service.get_lifecycle_phase(phase_name)

            if (
                phase_model
                and getattr(phase_model, "scripts", None)
                and config_phase
                and getattr(config_phase, "scripts", None)
            ):
                # Merge: phase_model scripts first (higher priority), config scripts appended
                merged = list(phase_model.scripts) + list(config_phase.scripts)
                phase_model.scripts = merged
                self.logger.debug(
                    "Merged configuration lifecycle scripts into phase model",
                    extra={
                        "phase": phase_name,
                        "total_scripts": len(merged),
                    },
                )
            elif (
                not getattr(phase_model, "scripts", None)
                and config_phase
                and getattr(config_phase, "scripts", None)
            ):
                # Phase has no scripts — use config phase entirely
                self.logger.debug(
                    "Using configuration phase model (phase_model has no scripts)",
                    extra={"phase": phase_name},
                )
                phase_model = config_phase

        # ------------------------------------------------------------------
        # Guard: skip silently when phase or scripts are absent
        # ------------------------------------------------------------------
        if phase_model is None:
            self.logger.debug(
                "Lifecycle phase not defined, skipping",
                extra={"phase": phase_name},
            )
            return True, []

        scripts = getattr(phase_model, "scripts", None)
        if not scripts:
            self.logger.debug(
                "Lifecycle phase has no scripts, skipping",
                extra={"phase": phase_name},
            )
            return True, []

        # ------------------------------------------------------------------
        # Execute scripts
        # ------------------------------------------------------------------
        self.logger.info(
            "Executing lifecycle phase",
            extra={"phase": phase_name, "script_count": len(scripts)},
        )

        total = len(scripts)
        for idx, script in enumerate(scripts, start=1):
            # Normalise script entry — support ScriptPathModel and bare Path
            if hasattr(script, "file"):
                script_file = Path(script.file)
                script_desc = getattr(script, "description", None)
            else:
                script_file = Path(script)
                script_desc = None

            if progress_callback:
                progress_callback(str(script_file), idx, total)

            success, error = self._execute_script(
                phase_name=phase_name,
                script_file=script_file,
                script_desc=script_desc,
                work_path=work_path,
                context=context,
            )

            if not success:
                errors.append(error)
                self._errors.append(error)

        return len(errors) == 0, errors

    # ------------------------------------------------------------------
    # Phase introspection helpers
    # ------------------------------------------------------------------

    def has_phase(self, lifecycle_model: Optional[object], phase_name: str) -> bool:
        """
        Check if a lifecycle model has a specific phase with at least one script.

        Args:
            lifecycle_model: Lifecycle model (CommonLifecycleModel or attribute-based)
            phase_name: Phase name to check

        Returns:
            True if phase exists and has scripts, False otherwise
        """
        if lifecycle_model is None:
            return False

        if isinstance(lifecycle_model, CommonLifecycleModel):
            phase = lifecycle_model.root.get(phase_name)
        elif hasattr(lifecycle_model, "root") and isinstance(
            lifecycle_model.root, dict
        ):
            phase = lifecycle_model.root.get(phase_name)
        elif hasattr(lifecycle_model, phase_name):
            phase = getattr(lifecycle_model, phase_name)
        else:
            return False

        if phase is None:
            return False

        scripts = getattr(phase, "scripts", None)
        return bool(scripts)

    def get_phase_script_count(
        self, lifecycle_model: Optional[object], phase_name: str
    ) -> int:
        """
        Get the number of scripts in a lifecycle phase.

        Args:
            lifecycle_model: Lifecycle model
            phase_name: Phase name

        Returns:
            Number of scripts, 0 if phase not defined or empty
        """
        if not self.has_phase(lifecycle_model, phase_name):
            return 0

        if isinstance(lifecycle_model, CommonLifecycleModel):
            phase = lifecycle_model.root.get(phase_name)
        elif hasattr(lifecycle_model, "root") and isinstance(
            lifecycle_model.root, dict
        ):
            phase = lifecycle_model.root.get(phase_name)
        else:
            phase = getattr(lifecycle_model, phase_name, None)

        scripts = getattr(phase, "scripts", None)
        return len(scripts) if scripts else 0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute_script(
        self,
        phase_name: str,
        script_file: Path,
        script_desc: Optional[str],
        work_path: Path,
        context: dict = None,
    ) -> Tuple[bool, str]:
        """
        Execute a single lifecycle script.

        Resolves the script relative to work_path, selects the appropriate
        interpreter based on the file extension, applies optional template
        variable substitution, then runs the script via subprocess.

        Args:
            phase_name: Name of the lifecycle phase (for logging)
            script_file: Script path (relative resolved against work_path)
            script_desc: Optional description (for logging)
            work_path: Working directory
            context: Optional extra context added as XYZ_* env vars

        Returns:
            Tuple of (success, error_message)
        """
        script_path = work_path / script_file

        if not script_path.exists():
            error_msg = (
                f"Lifecycle script not found: {script_file} (phase: {phase_name})"
            )
            self.logger.error(
                error_msg,
                extra={
                    "phase": phase_name,
                    "script": str(script_file),
                    "resolved_path": str(script_path),
                },
            )
            return False, error_msg

        if not script_path.is_file():
            error_msg = (
                f"Lifecycle script is not a file: {script_file} (phase: {phase_name})"
            )
            self.logger.error(
                error_msg,
                extra={"phase": phase_name, "script": str(script_file)},
            )
            return False, error_msg

        log_desc = f" — {script_desc}" if script_desc else ""
        self.logger.info(
            f"Executing lifecycle script: {script_file}{log_desc}",
            extra={
                "phase": phase_name,
                "script": str(script_file),
                "description": script_desc,
            },
        )

        env = self._prepare_environment(context)

        # Optional template substitution — writes processed copy to a temp dir
        script_to_execute = script_path
        temp_dir: Optional[Path] = None

        if self.enable_templating:
            script_to_execute, temp_dir = self._process_script_template(
                script_path, env, phase_name
            )
            if script_to_execute is None:
                error_msg = (
                    f"Failed to process template for script: {script_file} "
                    f"(phase: {phase_name})"
                )
                return False, error_msg

        try:
            cmd = self._build_command(script_to_execute)
            result = subprocess.run(
                cmd,
                cwd=work_path,
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
            )

            if result.returncode == 0:
                self.logger.info(
                    "Lifecycle script completed successfully",
                    extra={
                        "phase": phase_name,
                        "script": str(script_file),
                        "return_code": result.returncode,
                    },
                )
                if result.stdout:
                    self.logger.debug(
                        "Script output",
                        extra={
                            "phase": phase_name,
                            "script": str(script_file),
                            "output": result.stdout,
                        },
                    )
                self._messages.append(
                    f"Lifecycle script executed: {script_file} (phase: {phase_name})"
                )
                return True, ""

            error_msg = (
                f"Lifecycle script failed: {script_file} "
                f"(phase: {phase_name}, exit code: {result.returncode})"
            )
            if result.stderr:
                error_msg += f"\nError output: {result.stderr.strip()}"
            self.logger.error(
                "Lifecycle script failed",
                extra={
                    "phase": phase_name,
                    "script": str(script_file),
                    "return_code": result.returncode,
                    "stderr": result.stderr,
                    "stdout": result.stdout,
                },
            )
            return False, error_msg

        except subprocess.TimeoutExpired:
            error_msg = (
                f"Lifecycle script timed out: {script_file} (phase: {phase_name})"
            )
            self.logger.error(
                "Lifecycle script timed out",
                extra={"phase": phase_name, "script": str(script_file), "timeout": 300},
                exc_info=True,
            )
            return False, error_msg

        except Exception as e:
            error_msg = f"Failed to execute lifecycle script: {script_file} — {str(e)}"
            self.logger.error(
                "Failed to execute lifecycle script",
                extra={
                    "phase": phase_name,
                    "script": str(script_file),
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            return False, error_msg

        finally:
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                    self.logger.debug(
                        "Cleaned up temporary template directory",
                        extra={"temp_dir": str(temp_dir)},
                    )
                except Exception as exc:
                    self.logger.warning(
                        "Failed to clean up temporary template directory",
                        extra={"temp_dir": str(temp_dir), "error": str(exc)},
                    )

    def _build_command(self, script_path: Path) -> List[str]:
        """
        Build the subprocess command list for a given script file.

        Routes by file extension:
        - .sh / .bash → bash (on Windows: Git bash)
        - .ps1         → powershell -ExecutionPolicy Bypass -File ...
        - .bat / .cmd  → direct invocation
        - .py          → current Python interpreter
        - other        → direct invocation (assumes shebang / executable bit)

        Args:
            script_path: Resolved path to the script to execute

        Returns:
            List of command tokens suitable for subprocess.run()
        """
        ext = script_path.suffix.lower()

        if ext in (".sh", ".bash"):
            if platform.system() == "Windows":
                return ["bash", str(script_path)]
            return [str(script_path)]

        if ext == ".ps1":
            return [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ]

        if ext in (".bat", ".cmd"):
            return [str(script_path)]

        if ext == ".py":
            return [sys.executable, str(script_path)]

        # Default: execute directly (relies on shebang or OS association)
        return [str(script_path)]

    def _process_script_template(
        self,
        script_path: Path,
        env: dict,
        phase_name: str,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """
        Create a temp copy of script with $VAR / ${VAR} substitutions applied.

        Variables that are not found in env are left as-is (with a warning).
        The caller is responsible for cleaning up the returned temp directory.

        Args:
            script_path: Original script path
            env: Environment dict used for substitution
            phase_name: Phase name (for logging / temp dir naming)

        Returns:
            Tuple of (processed_script_path, temp_dir_path), or (None, None) on error
        """
        temp_dir: Optional[Path] = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix=f"xyz_lifecycle_{phase_name}_"))
            temp_script = temp_dir / script_path.name

            content = script_path.read_text(encoding="utf-8")

            pattern = r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"

            def replace_var(match: re.Match) -> str:
                var_name = match.group(1)
                if var_name in env:
                    return str(env[var_name])
                self.logger.warning(
                    f"Template variable not found in environment: {var_name}",
                    extra={"script": str(script_path), "variable": var_name},
                )
                return match.group(0)

            processed = re.sub(pattern, replace_var, content)
            temp_script.write_text(processed, encoding="utf-8")

            # Preserve original permission bits
            original_mode = script_path.stat().st_mode
            temp_script.chmod(original_mode & 0o777)

            self.logger.debug(
                "Processed script template",
                extra={
                    "original": str(script_path),
                    "processed": str(temp_script),
                    "phase": phase_name,
                },
            )
            return temp_script, temp_dir

        except Exception as exc:
            self.logger.error(
                "Failed to process script template",
                extra={
                    "script": str(script_path),
                    "phase": phase_name,
                    "error": str(exc),
                },
                exc_info=True,
            )
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass
            return None, None

    def _prepare_environment(self, context: dict = None) -> dict:
        """
        Build the environment dict for script execution.

        Starts from the current process environment and adds each context
        entry as XYZ_<KEY_UPPER> (string value).

        Args:
            context: Optional dict of extra key/value pairs

        Returns:
            Environment dict ready for subprocess.run(env=...)
        """
        env = os.environ.copy()

        if context:
            for key, value in context.items():
                env_key = f"XYZ_{key.upper()}"
                env[env_key] = str(value)
                self.logger.debug(
                    f"Added environment variable: {env_key}",
                    extra={"key": env_key, "value": str(value)},
                )

        return env
