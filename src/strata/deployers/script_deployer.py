"""Script deployer — executes lifecycle scripts defined in the deployment model.

Supported steps (map to lifecycle phase names):
  setup        — deploy_setup
  check        — deploy_check
  plan         — deploy_plan
  apply        — deploy_apply
  destroy      — deploy_destroy
  plan_destroy — deploy_plan_destroy
  output       — deploy_output  (returns empty dict; no structured output)
  show_plan    — not applicable (returns empty dict)

Scripts are looked up in deployment_model.spec.lifecycle.root[<phase_name>].scripts.
Each script entry is either a plain path string or a ScriptPathModel (has .file).

Supported script types: .sh, .bash, .py, .ps1
Environment variables injected into every script subprocess:
  WORK_PATH   — work_path passed to the deployer
  BUILD_PATH  — build_path passed to the deployer
  STAGE_NAME  — current stage name
"""

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_OUTPUT,
    STEP_PLAN,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
    STEP_SHOW_PLAN,
    BaseDeployer,
)
from strata.models.deployment_model import DeploymentStageModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService
from strata.utils.resolved_values import ResolvedValues

# Lifecycle phase names that correspond to each deployer step.
_STEP_TO_PHASE: Dict[str, str] = {
    STEP_SETUP: "deploy_setup",
    STEP_CHECK: "deploy_check",
    STEP_PLAN: "deploy_plan",
    STEP_APPLY: "deploy_apply",
    STEP_DESTROY: "deploy_destroy",
    STEP_PLAN_DESTROY: "deploy_plan_destroy",
    STEP_OUTPUT: "deploy_output",
}

# Default timeout (seconds) for a single script subprocess.
_SCRIPT_TIMEOUT = 300

# Map step names to the timeout field on DeploymentStageTimeoutsModel.
_STEP_TIMEOUT_DEFAULTS: Dict[str, int] = {
    STEP_SETUP: 300,
    STEP_CHECK: 300,
    STEP_PLAN: 300,
    STEP_APPLY: 1800,
    STEP_DESTROY: 1800,
    STEP_PLAN_DESTROY: 300,
    STEP_OUTPUT: 300,
}


class ScriptDeployer(BaseDeployer):
    """Deployer that executes lifecycle scripts defined in the deployment model.

    Scripts are looked up by phase name in
    ``deployment_model.spec.lifecycle.root``.  Each step runs all scripts
    listed in the corresponding phase in order; any non-zero exit code aborts
    the step.

    This deployer requires no external IaC binary — ``validate_environment``
    always succeeds.  ``validate_workspace`` verifies only that the deployment
    model has a lifecycle section; individual phase presence is checked lazily
    at step execution time (missing phase → skip, not error).
    """

    def __init__(
        self,
        stage: DeploymentStageModel,
        deployment_service: DeploymentService,
        configuration_service: ConfigurationService,
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
        resolved_values: Optional[ResolvedValues] = None,
    ) -> None:
        super().__init__(
            stage=stage,
            deployment_service=deployment_service,
            configuration_service=configuration_service,
            build_path=build_path,
            work_path=work_path,
            verbose=verbose,
            force=force,
        )
        self.resolved_values = resolved_values

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_deployer_name(self) -> str:
        return "script"

    def get_supported_steps(self) -> List[str]:
        return [
            STEP_SETUP,
            STEP_CHECK,
            STEP_PLAN,
            STEP_APPLY,
            STEP_DESTROY,
            STEP_PLAN_DESTROY,
            STEP_SHOW_PLAN,
            STEP_OUTPUT,
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_workspace(self) -> Tuple[bool, List[str]]:
        """Verify the deployment model has a lifecycle section."""
        messages: List[str] = []
        model = self.deployment_service.model
        if model is None:
            messages.append("Deployment service model is not loaded")
            return False, messages
        if not model.spec.lifecycle:
            messages.append(
                f"Stage '{self.stage.name}': deployment has no lifecycle section. "
                "Define lifecycle phases (deploy_setup, deploy_apply, …) in the deployment YAML."
            )
            return False, messages
        if self.verbose:
            phases = list(model.spec.lifecycle.root.keys())
            messages.append(f"Lifecycle phases available: {', '.join(phases) if phases else '(none)'}")
        return True, messages

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """No external binary required — always succeeds."""
        return True, []

    # ------------------------------------------------------------------
    # Step methods
    # ------------------------------------------------------------------

    def setup(self) -> Tuple[bool, List[str]]:
        return self._run_phase(STEP_SETUP, self._get_timeout(STEP_SETUP, _STEP_TIMEOUT_DEFAULTS[STEP_SETUP]))

    def check(self) -> Tuple[bool, List[str]]:
        return self._run_phase(STEP_CHECK, self._get_timeout(STEP_CHECK, _STEP_TIMEOUT_DEFAULTS[STEP_CHECK]))

    def plan(self) -> Tuple[bool, List[str]]:
        return self._run_phase(STEP_PLAN, self._get_timeout(STEP_PLAN, _STEP_TIMEOUT_DEFAULTS[STEP_PLAN]))

    def apply(self) -> Tuple[bool, List[str]]:
        success, messages = self._run_phase(
            STEP_APPLY, self._get_timeout(STEP_APPLY, _STEP_TIMEOUT_DEFAULTS[STEP_APPLY])
        )
        if success:
            messages.append(f"✓ Stage '{self.stage.name}' applied successfully.")
        return success, messages

    def destroy(self) -> Tuple[bool, List[str]]:
        success, messages = self._run_phase(
            STEP_DESTROY, self._get_timeout(STEP_DESTROY, _STEP_TIMEOUT_DEFAULTS[STEP_DESTROY])
        )
        if success:
            messages.append(f"✓ Stage '{self.stage.name}' destroyed successfully.")
        return success, messages

    def plan_destroy(self) -> Tuple[bool, List[str]]:
        return self._run_phase(
            STEP_PLAN_DESTROY, self._get_timeout(STEP_PLAN, _STEP_TIMEOUT_DEFAULTS[STEP_PLAN_DESTROY])
        )

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Not applicable for script deployer — returns empty plan data."""
        return True, {}, ["show_plan is not supported by the script deployer"]

    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Execute deploy_output scripts; structured output is not supported."""
        success, messages = self._run_phase(STEP_OUTPUT)
        return success, {}, messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_phase(self, step: str, timeout: int = _SCRIPT_TIMEOUT) -> Tuple[bool, List[str]]:
        """Map a step name to a lifecycle phase and execute its scripts."""
        phase_name = _STEP_TO_PHASE.get(step)
        if phase_name is None:
            return True, [f"No lifecycle phase mapping for step '{step}', skipping"]

        model = self.deployment_service.model
        lifecycle = model.spec.lifecycle if model else None

        if not lifecycle:
            return True, [f"No lifecycle defined, skipping '{phase_name}'"]

        lifecycle_phases = lifecycle.root
        return self._execute_lifecycle_phase(lifecycle_phases, phase_name, timeout)

    def _execute_lifecycle_phase(
        self,
        lifecycle_phases: dict,
        phase_name: str,
        timeout: int = _SCRIPT_TIMEOUT,
    ) -> Tuple[bool, List[str]]:
        """Execute all scripts for one lifecycle phase."""
        messages: List[str] = []

        if phase_name not in lifecycle_phases:
            if self.verbose:
                messages.append(f"Lifecycle phase '{phase_name}' not defined, skipping")
            return True, messages

        phase = lifecycle_phases[phase_name]

        if not phase.scripts:
            if self.verbose:
                messages.append(f"No scripts defined for phase '{phase_name}', skipping")
            return True, messages

        self.logger.debug("Executing lifecycle phase", phase=phase_name)
        messages.append(f"Executing lifecycle phase: {phase_name}")

        for idx, script_entry in enumerate(phase.scripts, start=1):
            # ScriptPathModel has .file; plain strings are kept as-is.
            script_file = Path(script_entry if isinstance(script_entry, str) else script_entry.file)

            if self.verbose:
                messages.append(f"  [{idx}/{len(phase.scripts)}] Running: {script_file.name}")

            success, script_messages = self._execute_script(script_file, phase_name, timeout)
            messages.extend(script_messages)

            if not success:
                messages.append(f"Script failed: {script_file}")
                return False, messages

        messages.append(f"Phase '{phase_name}' completed successfully")
        return True, messages

    def _execute_script(self, script_path: Path, phase_name: str, timeout: int = _SCRIPT_TIMEOUT) -> Tuple[bool, List[str]]:
        """Execute a single script file."""
        messages: List[str] = []

        suffix = script_path.suffix.lower()
        if suffix in (".sh", ".bash"):
            cmd = ["bash", str(script_path)]
        elif suffix == ".py":
            cmd = ["python", str(script_path)]
        elif suffix == ".ps1":
            cmd = ["pwsh", "-File", str(script_path)]
        else:
            messages.append(f"Unsupported script type: {suffix}")
            return False, messages

        env = os.environ.copy()
        if self.resolved_values is not None:
            env.update(self.resolved_values.as_compose_env())
        # Standard STRATA_* vars always win — set after resolved values
        env["STRATA_PHASE"] = phase_name
        env["STRATA_WORKSPACE_PATH"] = str(self.work_path)
        env["STRATA_BUILD_PATH"] = str(self.build_path)
        env["STRATA_CONFIG_PATH"] = str(self.work_path / ".strata")
        env["STRATA_OBJECT_PATH"] = str(self.build_path / "objects")
        env["STRATA_STAGE_NAME"] = self.stage.name

        try:
            result = subprocess.run(
                cmd,
                cwd=self.work_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            messages.append(f"Script timed out after {timeout}s: {script_path}")
            return False, messages
        except FileNotFoundError:
            messages.append(f"Interpreter not found for script: {script_path}")
            return False, messages

        if result.returncode != 0:
            messages.append(f"Script exited with code {result.returncode}: {script_path.name}")
            if result.stderr:
                messages.append(result.stderr.strip())
            return False, messages

        if self.verbose and result.stdout:
            messages.append(result.stdout.strip())

        return True, messages
