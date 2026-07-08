"""Pulumi provisioner plugin for strata.

Drop this file into ``.strata/provisioners/pulumi_provisioner.py`` in your
workspace.  strata will auto-discover it on startup and make the ``pulumi``
provisioner type available for use in workspace YAML files.

Workspace YAML entry::

    spec:
      provisioners:
        - name: infra
          provisioner: pulumi       # ← the name returned by get_deployer_name()
          source:
            repository: my-repo
            source_path: infra/pulumi

Requirements:
  - Pulumi CLI installed and on PATH (https://www.pulumi.com/docs/install/)
  - Pulumi access token set via ``PULUMI_ACCESS_TOKEN`` environment variable,
    or a local backend configured in Pulumi.yaml

See also: docs/guides/building-a-provisioner-plugin.md
"""

import json
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

_DEFAULT_TIMEOUT = 300
_APPLY_TIMEOUT = 1800
_DESTROY_TIMEOUT = 1800


class PulumiDeployer(BaseDeployer):
    """Deployer for Pulumi IaC stacks.

    Executes Pulumi CLI commands in the build directory for the stage.
    Requires ``pulumi`` on PATH and a configured backend (Pulumi Cloud,
    S3, Azure Blob, etc.).

    Supported steps:
        setup        — ``pulumi stack select --create <stack>`` (idempotent)
        check        — ``pulumi preview --expect-no-changes`` (dry validate)
        plan         — ``pulumi preview``
        apply        — ``pulumi up --yes``
        destroy      — ``pulumi destroy --yes``
        plan_destroy — ``pulumi preview --diff --expect-no-changes`` (preview of destroy)
        output       — ``pulumi stack output --json``
        show_plan    — returns last preview output (no binary plan file in Pulumi)
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
        solution_controller=None,
    ) -> None:
        super().__init__(
            stage=stage,
            deployment_service=deployment_service,
            configuration_service=configuration_service,
            build_path=build_path,
            work_path=work_path,
            verbose=verbose,
            force=force,
            solution_controller=solution_controller,
            resolved_values=resolved_values,
        )
        self._stage_build_dir: Optional[Path] = None
        self._stack_name: str = str(stage.name)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_deployer_name(self) -> str:
        return "pulumi"

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
        """Check that a Pulumi.yaml or Pulumi.<stack>.yaml exists in build dir."""
        messages: List[str] = []
        stage_dir = self._get_stage_dir()
        if stage_dir is None or not stage_dir.exists():
            messages.append(
                f"Stage '{self.stage.name}': build directory not found at "
                f"'{stage_dir}'.  Run 'strata build run' first."
            )
            return False, messages

        pulumi_yaml = stage_dir / "Pulumi.yaml"
        if not pulumi_yaml.exists():
            messages.append(
                f"Stage '{self.stage.name}': no Pulumi.yaml found in build "
                f"directory '{stage_dir}'.  Ensure the source repository "
                "contains a Pulumi project at the configured source_path."
            )
            return False, messages

        if self.verbose:
            messages.append(f"Pulumi project found: {pulumi_yaml}")
        return True, messages

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Check that the ``pulumi`` binary is available on PATH."""
        messages: List[str] = []
        result = subprocess.run(
            ["pulumi", "version"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            messages.append(
                "Pulumi CLI not found.  Install from https://www.pulumi.com/docs/install/ "
                "and ensure it is on PATH."
            )
            return False, messages
        if self.verbose:
            messages.append(f"Pulumi: {result.stdout.strip()}")
        return True, messages

    # ------------------------------------------------------------------
    # Step methods
    # ------------------------------------------------------------------

    def setup(self) -> Tuple[bool, List[str]]:
        """Select (or create) the Pulumi stack for this stage."""
        return self._run(
            ["pulumi", "stack", "select", "--create", self._stack_name],
            timeout=_DEFAULT_TIMEOUT,
        )

    def check(self) -> Tuple[bool, List[str]]:
        """Run ``pulumi preview`` expecting no changes (config/syntax validation)."""
        return self._run(
            ["pulumi", "preview", "--expect-no-changes"],
            timeout=_DEFAULT_TIMEOUT,
        )

    def plan(self) -> Tuple[bool, List[str]]:
        """Preview infrastructure changes with ``pulumi preview``."""
        cmd = ["pulumi", "preview"]
        if self.verbose:
            cmd.append("--diff")
        return self._run(cmd, timeout=_DEFAULT_TIMEOUT)

    def apply(self) -> Tuple[bool, List[str]]:
        """Apply infrastructure changes with ``pulumi up --yes``."""
        cmd = ["pulumi", "up", "--yes"]
        if self.verbose:
            cmd.append("--diff")
        ok, msgs = self._run(cmd, timeout=_APPLY_TIMEOUT)
        if ok:
            msgs.append(f"✓ Stage '{self.stage.name}' applied successfully.")
        return ok, msgs

    def destroy(self) -> Tuple[bool, List[str]]:
        """Destroy infrastructure with ``pulumi destroy --yes``."""
        return self._run(
            ["pulumi", "destroy", "--yes"],
            timeout=_DESTROY_TIMEOUT,
        )

    def plan_destroy(self) -> Tuple[bool, List[str]]:
        """Preview what destroy would remove (``pulumi preview --diff``)."""
        return self._run(
            ["pulumi", "preview", "--diff"],
            timeout=_DEFAULT_TIMEOUT,
        )

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Pulumi does not produce a binary plan file; returns empty dict."""
        return True, {}, ["Pulumi does not produce a persistent plan file.  Use 'plan' step to preview changes."]

    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Return stack outputs as a dict (``pulumi stack output --json``)."""
        messages: List[str] = []
        stage_dir = self._get_stage_dir()

        result = subprocess.run(
            ["pulumi", "stack", "output", "--json"],
            cwd=stage_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            messages.append(f"pulumi stack output failed: {result.stderr.strip()}")
            return False, {}, messages

        try:
            outputs: Dict[str, Any] = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            messages.append(f"Failed to parse Pulumi output JSON: {exc}")
            return False, {}, messages

        if self.verbose:
            messages.append(f"Collected {len(outputs)} output(s) from stack '{self._stack_name}'.")
        return True, outputs, messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_stage_dir(self) -> Optional[Path]:
        """Return the build directory for this stage."""
        if self._stage_build_dir is not None:
            return self._stage_build_dir
        if self.stage.provisioner is None:
            return None
        self._stage_build_dir = self.build_path / str(self.stage.provisioner)
        return self._stage_build_dir

    def _run(self, cmd: List[str], *, timeout: int) -> Tuple[bool, List[str]]:
        """Run a Pulumi CLI command in the stage build directory."""
        messages: List[str] = []
        stage_dir = self._get_stage_dir()

        env = self._build_env()

        if self.verbose:
            messages.append(f"Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                cwd=stage_dir,
                env=env,
                capture_output=not self.verbose,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            messages.append(f"Command timed out after {timeout}s: {' '.join(cmd)}")
            return False, messages
        except FileNotFoundError:
            messages.append("Pulumi CLI not found.  Ensure 'pulumi' is on PATH.")
            return False, messages

        if result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else ""
            messages.append(f"Command failed (exit {result.returncode}): {' '.join(cmd)}")
            if stderr:
                messages.append(stderr)
            return False, messages

        if self.verbose and result.stdout:
            messages.append(result.stdout.strip())
        return True, messages

    def _build_env(self) -> Dict[str, str]:
        """Build the environment for Pulumi subprocesses.

        Injects resolved secrets/variables from the stage context so that
        Pulumi config values and cloud credentials are available.
        """
        import os

        env = os.environ.copy()

        # Stack name as an env var (useful for Pulumi programs that read it)
        env["PULUMI_STACK"] = self._stack_name

        # Inject strata resolved values if available
        if self.resolved_values is not None:
            for key, value in self.resolved_values.env_vars.items():
                env[key] = str(value)

        return env
