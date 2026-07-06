"""ArgoCD provisioner plugin for strata.

Drop this file into ``.strata/provisioners/argocd_provisioner.py`` in your
workspace.  strata will auto-discover it on startup and make the ``argocd``
provisioner type available for use in workspace YAML files.

Workspace YAML entry::

    spec:
      provisioners:
        - name: apps
          provisioner: argocd       # ← the name returned by get_deployer_name()
          source:
            repository: my-repo
            source_path: k8s/apps

Requirements:
  - ArgoCD CLI installed and on PATH (https://argo-cd.readthedocs.io/en/stable/cli_installation/)
  - ``ARGOCD_SERVER`` environment variable set to your ArgoCD server address
  - ``ARGOCD_AUTH_TOKEN`` environment variable set (preferred), or logged in via
    ``argocd login`` before running strata

App name resolution:
  By default, the ArgoCD application name is derived from the stage name.
  Override by setting ``app_name`` in the stage's extra properties block.

See also: docs/guides/building-a-provisioner-plugin.md
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_HEALTH,
    STEP_OUTPUT,
    STEP_PLAN,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
    STEP_SHOW_PLAN,
    STEP_STATUS,
    BaseDeployer,
)
from strata.models.deployment_model import DeploymentStageModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService
from strata.utils.resolved_values import ResolvedValues

_DEFAULT_TIMEOUT = 120
_SYNC_TIMEOUT = 600
_WAIT_HEALTH_TIMEOUT = 300


class ArgoCDDeployer(BaseDeployer):
    """Deployer for ArgoCD GitOps application syncs.

    Manages ArgoCD applications using the ``argocd`` CLI.  Each stage maps to
    one ArgoCD application.  The app must already be registered in ArgoCD
    (either by a preceding bootstrap step or manually).

    Supported steps:
        setup        — ``argocd app get <app>`` (verify app registration)
        check        — ``argocd app diff <app>`` (show pending changes)
        plan         — ``argocd app diff <app>`` (alias for check)
        apply        — ``argocd app sync <app> --prune --timeout <n>``
        destroy      — ``argocd app delete <app> --cascade``
        plan_destroy — ``argocd app get <app>`` (shows what would be deleted)
        output       — ``argocd app get <app> --output json`` (app status dict)
        status       — ``argocd app get <app> --output json`` (sync/health status)
        health       — ``argocd app wait <app> --health --timeout <n>``
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
        # App name defaults to stage name; override via stage properties if needed
        self._app_name: str = str(stage.name)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_deployer_name(self) -> str:
        return "argocd"

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
            STEP_STATUS,
            STEP_HEALTH,
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_workspace(self) -> Tuple[bool, List[str]]:
        """Verify that ARGOCD_SERVER is set in the environment."""
        messages: List[str] = []
        server = os.environ.get("ARGOCD_SERVER", "").strip()
        if not server:
            messages.append(
                f"Stage '{self.stage.name}': ARGOCD_SERVER environment variable is not set. "
                "Set it to your ArgoCD server address (e.g. 'argocd.example.com')."
            )
            return False, messages
        if self.verbose:
            messages.append(f"ArgoCD server: {server}")
        return True, messages

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Check that the ``argocd`` binary is available on PATH."""
        messages: List[str] = []
        result = subprocess.run(
            ["argocd", "version", "--client"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            messages.append(
                "ArgoCD CLI not found.  Install from "
                "https://argo-cd.readthedocs.io/en/stable/cli_installation/ "
                "and ensure it is on PATH."
            )
            return False, messages
        if self.verbose:
            version_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
            messages.append(f"ArgoCD CLI: {version_line.strip()}")
        return True, messages

    # ------------------------------------------------------------------
    # Step methods
    # ------------------------------------------------------------------

    def setup(self) -> Tuple[bool, List[str]]:
        """Verify the ArgoCD application is registered (``argocd app get``)."""
        ok, msgs = self._run(
            ["argocd", "app", "get", self._app_name],
            timeout=_DEFAULT_TIMEOUT,
        )
        if not ok:
            msgs.append(
                f"Application '{self._app_name}' not found in ArgoCD.  "
                "Register it first (via Helm bootstrap, argocd app create, or an App-of-Apps)."
            )
        return ok, msgs

    def check(self) -> Tuple[bool, List[str]]:
        """Show pending changes with ``argocd app diff``."""
        return self._run(
            ["argocd", "app", "diff", self._app_name],
            timeout=_DEFAULT_TIMEOUT,
            # argocd app diff exits 1 when there are differences — treat as success
            allow_exit_one=True,
        )

    def plan(self) -> Tuple[bool, List[str]]:
        """Alias for check — show pending changes."""
        return self.check()

    def apply(self) -> Tuple[bool, List[str]]:
        """Sync the application with ``argocd app sync --prune``."""
        cmd = [
            "argocd",
            "app",
            "sync",
            self._app_name,
            "--prune",
            "--timeout",
            str(_SYNC_TIMEOUT),
        ]
        if self.force:
            cmd.append("--force")
        ok, msgs = self._run(cmd, timeout=_SYNC_TIMEOUT + 30)
        if ok:
            msgs.append(f"✓ Stage '{self.stage.name}' synced successfully.")
        return ok, msgs

    def destroy(self) -> Tuple[bool, List[str]]:
        """Delete the ArgoCD application with cascade (removes all Kubernetes resources)."""
        if not self.force:
            return False, [
                f"Stage '{self.stage.name}': argocd app delete requires --force flag "
                "to confirm cascade deletion of all Kubernetes resources."
            ]
        return self._run(
            ["argocd", "app", "delete", self._app_name, "--cascade", "--yes"],
            timeout=_DEFAULT_TIMEOUT,
        )

    def plan_destroy(self) -> Tuple[bool, List[str]]:
        """Show the application resources that would be removed on delete."""
        ok, msgs = self._run(
            ["argocd", "app", "get", self._app_name, "--output", "json"],
            timeout=_DEFAULT_TIMEOUT,
        )
        if ok:
            msgs.insert(0, f"Resources managed by '{self._app_name}' (would be deleted on destroy):")
        return ok, msgs

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """ArgoCD does not produce a binary plan file; returns app diff output."""
        ok, msgs = self.check()
        return ok, {}, msgs

    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Return the application status as a dict (``argocd app get --output json``)."""
        messages: List[str] = []
        result = subprocess.run(
            ["argocd", "app", "get", self._app_name, "--output", "json"],
            capture_output=True,
            text=True,
            env=self._build_env(),
            timeout=_DEFAULT_TIMEOUT,
        )
        if result.returncode != 0:
            messages.append(f"argocd app get failed: {result.stderr.strip()}")
            return False, {}, messages

        try:
            data: Dict[str, Any] = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            messages.append(f"Failed to parse ArgoCD output JSON: {exc}")
            return False, {}, messages

        # Extract the most useful fields as outputs
        status = data.get("status", {})
        outputs = {
            "sync_status": status.get("sync", {}).get("status", "Unknown"),
            "health_status": status.get("health", {}).get("status", "Unknown"),
            "revision": status.get("sync", {}).get("revision", ""),
        }
        return True, outputs, messages

    def status(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Return live sync and health status for the ArgoCD application."""
        ok, outputs, msgs = self.output()
        return ok, outputs, msgs

    def health(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Wait for the application to reach a Healthy state."""
        messages: List[str] = []
        result = subprocess.run(
            ["argocd", "app", "wait", self._app_name, "--health", "--timeout", str(_WAIT_HEALTH_TIMEOUT)],
            capture_output=not self.verbose,
            text=True,
            env=self._build_env(),
            timeout=_WAIT_HEALTH_TIMEOUT + 30,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else ""
            messages.append(f"Health check failed for '{self._app_name}': {stderr}")
            return False, {"healthy": False}, messages

        messages.append(f"✓ Application '{self._app_name}' is Healthy.")
        return True, {"healthy": True}, messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(
        self,
        cmd: List[str],
        *,
        timeout: int,
        allow_exit_one: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Run an ArgoCD CLI command and return (success, messages)."""
        messages: List[str] = []

        if self.verbose:
            messages.append(f"Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=not self.verbose,
                text=True,
                env=self._build_env(),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            messages.append(f"Command timed out after {timeout}s: {' '.join(cmd)}")
            return False, messages
        except FileNotFoundError:
            messages.append("ArgoCD CLI not found.  Ensure 'argocd' is on PATH.")
            return False, messages

        failed = result.returncode != 0 and not (allow_exit_one and result.returncode == 1)
        if failed:
            stderr = result.stderr.strip() if result.stderr else ""
            messages.append(f"Command failed (exit {result.returncode}): {' '.join(cmd)}")
            if stderr:
                messages.append(stderr)
            return False, messages

        if self.verbose and result.stdout:
            messages.append(result.stdout.strip())
        return True, messages

    def _build_env(self) -> Dict[str, str]:
        """Build the environment for ArgoCD CLI subprocesses."""
        env = os.environ.copy()

        # Inject strata resolved values (secrets, variables) if available
        if self.resolved_values is not None:
            for key, value in self.resolved_values.env_vars.items():
                env[key] = str(value)

        return env
