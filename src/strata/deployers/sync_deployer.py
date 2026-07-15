"""GitOps sync deployers (ArgoCD and Flux) — step-based provisioner for GitOps controllers.

These deployers handle the deploy phase of a GitOps stage: they commit and push a
rendered configuration file (produced by SyncBuilder) to a git remote repository,
where an ArgoCD Application or Flux Kustomization watches for changes.

Supported steps (in execution order):
  setup      — validate rendered output file exists in build path
  check      — verify git binary is on PATH and remote is reachable
  plan       — show diff of what would be committed
  apply      — copy rendered file to config repo → git add → commit → push
  destroy    — remove file from config repo → git add → commit → push
  health     — query the GitOps controller API for reconciliation status
  output     — return commit SHA and remote path

Typical caller sequences:
  dry-run  : setup → check → plan
  deploy   : setup → check → plan → apply
  destroy  : setup → destroy  (force=True required)
  health   : health  (standalone; validates environment first)
"""

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

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
    BaseDeployer,
)
from strata.models.deployment_model import DeploymentStageModel
from strata.models.reconciliation_model import ReconciliationResult
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService
from strata.utils.resolved_values import ResolvedValues

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController


class BaseSyncDeployer(BaseDeployer):
    """Shared GitOps deploy logic for ArgoCD and Flux.

    Both controllers use the same git commit+push mechanism — only the deployer name
    differs.  Subclasses override ``get_deployer_name`` only.
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
        solution_controller: Optional["SolutionController"] = None,
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
        # Resolved at validate_workspace time; used by all step methods
        self._rendered_file: Optional[Path] = None
        self._repo_path: Optional[Path] = None
        self._output_file_rel: Optional[str] = None
        self._last_commit_sha: Optional[str] = None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_deployer_name(self) -> str:  # pragma: no cover — overridden in subclasses
        raise NotImplementedError

    def get_supported_steps(self) -> List[str]:
        return [
            STEP_SETUP,
            STEP_CHECK,
            STEP_PLAN,
            STEP_APPLY,
            STEP_DESTROY,
            STEP_PLAN_DESTROY,
            STEP_SHOW_PLAN,
            STEP_HEALTH,
            STEP_OUTPUT,
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_workspace(self) -> Tuple[bool, List[str]]:
        """Verify the rendered output file exists in the build path."""
        messages: List[str] = []

        if self.stage.backend is None:
            messages.append(
                f"Stage '{self.stage.name}': sync stage requires 'backend' with 'integration' and 'remote' fields"
            )
            return False, messages

        integration = self._find_integration(self.stage.backend.integration)
        if integration is None:
            messages.append(
                f"Stage '{self.stage.name}': integration '{self.stage.backend.integration}' not found "
                "in configuration spec integrations"
            )
            return False, messages

        if integration.properties is None or not integration.properties.get("output_file"):
            messages.append(
                f"Stage '{self.stage.name}': integration '{self.stage.backend.integration}' "
                "must declare 'output_file' in its properties"
            )
            return False, messages

        output_file_rel: str = str(integration.properties["output_file"])
        deployment_build_path = self.deployment_service.get_build_path(self.build_path)
        rendered_file = deployment_build_path / self.stage.name / output_file_rel

        if not rendered_file.exists():
            messages.append(
                f"Stage '{self.stage.name}': rendered output file not found at '{rendered_file}'. "
                "Run 'strata build run' to produce it before deploying."
            )
            return False, messages

        self._rendered_file = rendered_file
        self._output_file_rel = output_file_rel
        messages.append(f"Sync workspace validated: rendered file at '{rendered_file}'")
        return True, messages

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Verify git is available on PATH and the config remote repo path exists."""
        messages: List[str] = []

        # git binary check
        git_path = shutil.which("git")
        if git_path is None:
            messages.append("'git' binary not found on PATH — required for sync deployment")
            return False, messages

        if self.stage.backend is None:
            # Already caught in validate_workspace, but guard defensively
            return True, messages

        # Resolve the remote repo path
        repo_path, err = self._resolve_repo_path(self.stage.backend.remote)
        if err or repo_path is None:
            messages.append(err or f"Could not resolve remote '{self.stage.backend.remote}'")
            return False, messages

        if not repo_path.exists():
            messages.append(
                f"Stage '{self.stage.name}': remote repo path does not exist: '{repo_path}'. "
                f"Ensure remote '{self.stage.backend.remote}' is cloned locally."
            )
            return False, messages

        self._repo_path = repo_path
        messages.append(f"Git environment validated: repo at '{repo_path}'")
        return True, messages

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def setup(self) -> Tuple[bool, List[str]]:
        """No-op for sync deployers — validation done in validate_workspace."""
        return True, [f"[{self.get_deployer_name()}] Setup complete (sync stage — no tool initialisation required)"]

    def check(self) -> Tuple[bool, List[str]]:
        """Verify the config remote is reachable via git fetch --dry-run."""
        messages: List[str] = []

        repo_path = self._repo_path
        if repo_path is None:
            messages.append("validate_environment() must be called before check()")
            return False, messages

        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "fetch", "--dry-run"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                messages.append(f"[{self.get_deployer_name()}] Remote reachable: '{repo_path}'")
                return True, messages
            stderr = result.stderr.strip()
            messages.append(
                f"[{self.get_deployer_name()}] Remote check failed for '{repo_path}': {stderr or 'non-zero exit'}"
            )
            return False, messages
        except subprocess.TimeoutExpired:
            messages.append(f"[{self.get_deployer_name()}] Remote check timed out for '{repo_path}'")
            return False, messages
        except OSError as exc:
            messages.append(f"[{self.get_deployer_name()}] Remote check error: {exc}")
            return False, messages

    def plan(self) -> Tuple[bool, List[str]]:
        """Show what would be committed: diff of rendered file vs current state in config repo."""
        messages: List[str] = []

        rendered_file = self._rendered_file
        repo_path = self._repo_path
        output_file_rel = self._output_file_rel

        if rendered_file is None or repo_path is None or output_file_rel is None:
            messages.append("validate_workspace() and validate_environment() must be called before plan()")
            return False, messages

        target_in_repo = repo_path / output_file_rel
        deployer_name = self.get_deployer_name()

        if not target_in_repo.exists():
            messages.append(f"[{deployer_name}] New file: '{output_file_rel}' (would be added to '{repo_path}')")
            return True, messages

        # Show diff against the existing file
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "diff", "--no-index", "--", str(target_in_repo), str(rendered_file)],
                capture_output=True,
                text=True,
            )
            diff_output = result.stdout.strip()
            if diff_output:
                for line in diff_output.splitlines():
                    messages.append(line)
            else:
                messages.append(f"[{deployer_name}] No changes: '{output_file_rel}' is up to date")
        except OSError as exc:
            messages.append(f"[{deployer_name}] Could not compute diff: {exc}")

        return True, messages

    def apply(self) -> Tuple[bool, List[str]]:
        """Copy the rendered file to the config repo and commit+push."""
        messages: List[str] = []

        rendered_file = self._rendered_file
        repo_path = self._repo_path
        output_file_rel = self._output_file_rel

        if rendered_file is None or repo_path is None or output_file_rel is None:
            messages.append("validate_workspace() and validate_environment() must be called before apply()")
            return False, messages

        deployer_name = self.get_deployer_name()
        target_in_repo = repo_path / output_file_rel

        # Ensure parent directories exist
        target_in_repo.parent.mkdir(parents=True, exist_ok=True)

        # Copy rendered file to config repo
        try:
            shutil.copy2(str(rendered_file), str(target_in_repo))
            messages.append(f"[{deployer_name}] Copied '{rendered_file.name}' → '{target_in_repo}'")
        except OSError as exc:
            messages.append(f"[{deployer_name}] Failed to copy rendered file: {exc}")
            return False, messages

        # git add
        ok, git_messages = self._git_run(repo_path, ["add", output_file_rel])
        messages.extend(git_messages)
        if not ok:
            return False, messages

        # Build commit message
        deployment_name = str(self.deployment_service.model.meta.name) if self.deployment_service.model else "unknown"
        revision = self._get_revision()
        commit_msg = f"strata: sync {deployment_name} @ {revision[:7] if revision else 'unknown'}"

        # git commit
        ok, git_messages = self._git_run(repo_path, ["commit", "-m", commit_msg])
        messages.extend(git_messages)
        if not ok:
            # Nothing staged → treat as success (already up to date)
            if any("nothing to commit" in m for m in git_messages):
                messages.append(f"[{deployer_name}] Nothing to commit — already up to date")
                return True, messages
            return False, messages

        # git push
        ok, git_messages = self._git_run(repo_path, ["push"])
        messages.extend(git_messages)
        if not ok:
            return False, messages

        # Capture new commit SHA
        sha_result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        self._last_commit_sha = sha_result.stdout.strip() or None
        messages.append(f"[{deployer_name}] Committed and pushed: {self._last_commit_sha or 'unknown SHA'}")
        return True, messages

    def destroy(self) -> Tuple[bool, List[str]]:
        """Remove the output file from the config repo and commit+push."""
        messages: List[str] = []

        if not self.force:
            messages.append(f"[{self.get_deployer_name()}] Destroy requires --force flag to prevent accidental removal")
            return False, messages

        repo_path = self._repo_path
        output_file_rel = self._output_file_rel

        if repo_path is None or output_file_rel is None:
            messages.append("validate_workspace() and validate_environment() must be called before destroy()")
            return False, messages

        deployer_name = self.get_deployer_name()
        target_in_repo = repo_path / output_file_rel

        if not target_in_repo.exists():
            messages.append(f"[{deployer_name}] File '{output_file_rel}' not found in repo — nothing to remove")
            return True, messages

        # git rm
        ok, git_messages = self._git_run(repo_path, ["rm", "--force", output_file_rel])
        messages.extend(git_messages)
        if not ok:
            return False, messages

        # Build commit message
        deployment_name = str(self.deployment_service.model.meta.name) if self.deployment_service.model else "unknown"
        revision = self._get_revision()
        commit_msg = f"strata: remove {deployment_name} @ {revision[:7] if revision else 'unknown'}"

        ok, git_messages = self._git_run(repo_path, ["commit", "-m", commit_msg])
        messages.extend(git_messages)
        if not ok:
            return False, messages

        ok, git_messages = self._git_run(repo_path, ["push"])
        messages.extend(git_messages)
        if not ok:
            return False, messages

        messages.append(f"[{deployer_name}] Removed '{output_file_rel}' and pushed")
        return True, messages

    def plan_destroy(self) -> Tuple[bool, List[str]]:
        """Show what destroy would remove."""
        messages: List[str] = []
        repo_path = self._repo_path
        output_file_rel = self._output_file_rel
        deployer_name = self.get_deployer_name()

        if repo_path is None or output_file_rel is None:
            messages.append("validate_workspace() and validate_environment() must be called before plan_destroy()")
            return False, messages

        target_in_repo = repo_path / output_file_rel
        if target_in_repo.exists():
            messages.append(f"[{deployer_name}] Would remove: '{target_in_repo}'")
        else:
            messages.append(f"[{deployer_name}] Nothing to destroy: '{output_file_rel}' not found in repo")
        return True, messages

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        return True, {}, []

    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Return the commit SHA and config repo path produced by the last apply."""
        data: Dict[str, Any] = {}
        if self._last_commit_sha:
            data["commit_sha"] = self._last_commit_sha
        if self._repo_path:
            data["remote_path"] = str(self._repo_path)
        if self._output_file_rel:
            data["output_file"] = self._output_file_rel
        return True, data, []

    def health(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Query the GitOps controller for reconciliation status.

        Returns:
            (success, health_data_dict, messages)

            health_data_dict contains a serialised ReconciliationResult plus a
            human-readable ``summary`` string.  On failure, health_data_dict is
            empty and messages describe the error.
        """
        messages: List[str] = []
        deployer_name = self.get_deployer_name()

        if self.stage.backend is None:
            messages.append(f"[{deployer_name}] health requires stage.backend.integration to be set")
            return False, {}, messages

        integration = self._find_integration(self.stage.backend.integration)
        if integration is None:
            messages.append(
                f"[{deployer_name}] integration '{self.stage.backend.integration}' not found in configuration"
            )
            return False, {}, messages

        ok, result, query_messages = self._query_reconciliation(integration)
        messages.extend(query_messages)

        if not ok or result is None:
            return False, {}, messages

        data: Dict[str, Any] = {
            "sync_status": result.sync_status,
            "health_status": result.health_status,
            "revision": result.revision,
            "intended_revision": result.intended_revision,
            "drift": result.drift,
            "message": result.message,
            "last_synced_at": result.last_synced_at.isoformat() if result.last_synced_at else None,
        }

        drift_flag = " ⚠ drift detected" if result.drift else ""
        summary = (
            f"[{deployer_name}] Sync: {result.sync_status}  "
            f"Health: {result.health_status}  "
            f"Revision: {(result.revision or 'unknown')[:7]}{drift_flag}"
        )
        messages.append(summary)
        data["summary"] = summary

        # health is OK when Synced+Healthy; Progressing is still OK
        healthy = result.sync_status == "Synced" and result.health_status in ("Healthy", "Progressing")
        return healthy, data, messages

    def _query_reconciliation(self, integration: Any) -> Tuple[bool, Optional[ReconciliationResult], List[str]]:
        """Subclass-specific health query. Override in ArgocdDeployer and FluxDeployer."""
        return False, None, [f"[{self.get_deployer_name()}] health not implemented — override _query_reconciliation()"]

    def _resolve_auth_token(self, integration: Any) -> Optional[str]:
        """Resolve a bearer token from the integration's authentication config.

        Supports method=api_key: treats api_key.api_key as a key-reference name and
        resolves the value from resolved_values.secrets or environment variables.
        Also checks the ARGOCD_AUTH_TOKEN / FLUX_AUTH_TOKEN env vars as fallbacks.
        """
        auth = getattr(integration, "authentication", None)
        if auth is not None and auth.method == "api_key" and auth.api_key is not None:
            key_name: str = auth.api_key.api_key
            if self.resolved_values and key_name in self.resolved_values.secrets:
                return str(self.resolved_values.secrets[key_name])
            env_val = os.environ.get(key_name) or os.environ.get(key_name.upper())
            if env_val:
                return env_val

        # Well-known env var fallbacks per controller type
        deployer_name = self.get_deployer_name()
        fallback_env = f"{deployer_name.upper()}_AUTH_TOKEN"
        return os.environ.get(fallback_env)

    def describe_plan(self) -> List[str]:
        """Return a human-readable summary of what apply would do."""
        lines: List[str] = []
        deployer_name = self.get_deployer_name()
        if self._rendered_file:
            lines.append(f"[{deployer_name}] Rendered file: '{self._rendered_file}'")
        if self._repo_path and self._output_file_rel:
            lines.append(f"[{deployer_name}] Target in repo: '{self._repo_path / self._output_file_rel}'")
        if self.stage.backend:
            lines.append(f"[{deployer_name}] Remote: '{self.stage.backend.remote}'")
        return lines

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_integration(self, name: str):
        """Look up an integration by name from configuration spec."""
        cfg_model = self.configuration_service.model
        if cfg_model is None or cfg_model.spec is None:
            return None
        integrations = cfg_model.spec.integrations or []
        return next((i for i in integrations if str(i.name) == name), None)

    def _resolve_repo_path(self, remote_name: str) -> Tuple[Optional[Path], Optional[str]]:
        """Resolve the local path for a registered remote by name.

        Returns (path, None) on success, or (None, error_message) on failure.
        """
        if self.solution_controller is None:
            return None, (
                f"Stage '{self.stage.name}': cannot resolve remote '{remote_name}' — no solution controller available"
            )
        repo_map = self.solution_controller.get_repo_map()
        raw_path = repo_map.get(remote_name)
        if raw_path is None:
            available = ", ".join(sorted(repo_map.keys())) or "(none)"
            return None, (
                f"Stage '{self.stage.name}': remote '{remote_name}' not found in solution. "
                f"Available remotes: {available}"
            )
        return Path(raw_path), None

    def _get_revision(self) -> Optional[str]:
        """Return the current git HEAD SHA from the workspace, or None on failure."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(self.work_path),
                timeout=5,
            )
            return result.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def _git_run(self, repo_path: Path, args: List[str]) -> Tuple[bool, List[str]]:
        """Run a git command in repo_path, returning (success, messages)."""
        messages: List[str] = []
        cmd = ["git", "-C", str(repo_path)] + args
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if self.verbose and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    messages.append(line)
            if result.returncode != 0:
                stderr = result.stderr.strip()
                stdout = result.stdout.strip()
                if stdout:
                    messages.append(stdout)
                if stderr:
                    messages.append(stderr)
                return False, messages
            if result.stdout.strip():
                messages.append(result.stdout.strip())
            return True, messages
        except subprocess.TimeoutExpired:
            messages.append(f"git command timed out: {' '.join(args)}")
            return False, messages
        except OSError as exc:
            messages.append(f"git command failed: {exc}")
            return False, messages


class ArgocdDeployer(BaseSyncDeployer):
    """GitOps sync deployer for ArgoCD Applications.

    Health query: ``GET {endpoint}/api/v1/applications/{app_name}``

    Configuration via ``integration.properties``:
      ``app_name``         — ArgoCD Application name (defaults to deployment name)
      ``app_name_suffix``  — appended to the deployment name when deriving app_name
    """

    def get_deployer_name(self) -> str:
        return "argocd"

    def _query_reconciliation(self, integration: Any) -> Tuple[bool, Optional[ReconciliationResult], List[str]]:
        messages: List[str] = []

        # Resolve endpoint
        if integration.endpoints is None:
            messages.append("[argocd] integration must have 'endpoints.address' set for health queries")
            return False, None, messages

        base_url = str(integration.endpoints.address).rstrip("/")

        # Derive application name
        props = integration.properties or {}
        if props.get("app_name"):
            app_name = str(props["app_name"])
        else:
            deployment_name = (
                str(self.deployment_service.model.meta.name) if self.deployment_service.model else "unknown"
            )
            suffix = str(props.get("app_name_suffix", ""))
            app_name = f"{deployment_name}{suffix}" if suffix else deployment_name

        url = f"{base_url}/api/v1/applications/{app_name}"

        # Build request
        req = urllib.request.Request(url)  # noqa: S310
        token = self._resolve_auth_token(integration)
        if token:
            req.add_header("Authorization", f"Bearer {token}")

        # Intended revision = last commit SHA strata pushed (from output, or git HEAD)
        intended_revision = self._last_commit_sha or self._get_revision() or "unknown"

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            messages.append(f"[argocd] HTTP {exc.code} from ArgoCD API at '{url}': {exc.reason}")
            return False, None, messages
        except urllib.error.URLError as exc:
            messages.append(f"[argocd] Could not reach ArgoCD API at '{url}': {exc.reason}")
            return False, None, messages
        except (OSError, ValueError) as exc:
            messages.append(f"[argocd] ArgoCD API error: {exc}")
            return False, None, messages

        status = body.get("status", {})
        sync = status.get("sync", {})
        health = status.get("health", {})
        operation_state = status.get("operationState", {})

        sync_status = sync.get("status", "Unknown")
        health_status = health.get("status", "Unknown")
        revision: Optional[str] = sync.get("revision") or None
        health_message: Optional[str] = health.get("message") or operation_state.get("message") or None

        # last_synced_at from operationState.finishedAt
        last_synced_at: Optional[datetime] = None
        finished_at = operation_state.get("finishedAt")
        if finished_at:
            try:
                last_synced_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        drift = bool(revision and revision != intended_revision)

        result = ReconciliationResult(
            sync_status=sync_status,
            health_status=health_status,
            last_synced_at=last_synced_at,
            revision=revision,
            intended_revision=intended_revision,
            drift=drift,
            message=health_message,
        )
        return True, result, messages


class FluxDeployer(BaseSyncDeployer):
    """GitOps sync deployer for Flux Kustomizations.

    Health query: ``kubectl get kustomization|helmrelease {name} -n {namespace} -o json``

    Configuration via ``integration.properties``:
      ``resource_type``   — ``kustomization`` (default) or ``helmrelease``
      ``resource_name``   — Flux resource name (defaults to deployment name)
      ``namespace``       — Kubernetes namespace (defaults to ``flux-system``)
    """

    def get_deployer_name(self) -> str:
        return "flux"

    def _query_reconciliation(self, integration: Any) -> Tuple[bool, Optional[ReconciliationResult], List[str]]:
        messages: List[str] = []

        props = integration.properties or {}
        resource_type = str(props.get("resource_type", "kustomization")).lower()
        namespace = str(props.get("namespace", "flux-system"))

        # Derive resource name
        if props.get("resource_name"):
            resource_name = str(props["resource_name"])
        else:
            resource_name = str(self.deployment_service.model.meta.name) if self.deployment_service.model else "unknown"

        # kubectl availability check
        if shutil.which("kubectl") is None:
            messages.append("[flux] 'kubectl' not found on PATH — required for Flux health queries")
            return False, None, messages

        # Determine CRD API group
        api_group = "kustomize.toolkit.fluxcd.io" if resource_type == "kustomization" else "helm.toolkit.fluxcd.io"
        plural = "kustomizations" if resource_type == "kustomization" else "helmreleases"

        # Intended revision = last commit SHA strata pushed (or git HEAD)
        intended_revision = self._last_commit_sha or self._get_revision() or "unknown"

        try:
            result = subprocess.run(
                ["kubectl", "get", f"{plural}.{api_group}", resource_name, "-n", namespace, "-o", "json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            messages.append(f"[flux] kubectl timed out querying {resource_type} '{resource_name}'")
            return False, None, messages
        except OSError as exc:
            messages.append(f"[flux] kubectl error: {exc}")
            return False, None, messages

        if result.returncode != 0:
            stderr = result.stderr.strip()
            messages.append(
                f"[flux] kubectl get {resource_type} '{resource_name}' in namespace '{namespace}' failed: "
                f"{stderr or 'non-zero exit'}"
            )
            return False, None, messages

        try:
            body = json.loads(result.stdout)
        except ValueError as exc:
            messages.append(f"[flux] Could not parse kubectl output: {exc}")
            return False, None, messages

        # Parse .status.conditions[] for type=Ready
        status = body.get("status", {})
        conditions: List[Dict[str, Any]] = status.get("conditions", [])
        ready_condition = next((c for c in conditions if c.get("type") == "Ready"), None)

        if ready_condition is None:
            rec_result = ReconciliationResult(
                sync_status="Unknown",
                health_status="Unknown",
                last_synced_at=None,
                revision=None,
                intended_revision=intended_revision,
                drift=False,
                message="No Ready condition found",
            )
            return True, rec_result, messages

        condition_status = ready_condition.get("status", "Unknown")  # "True" / "False" / "Unknown"
        condition_reason = ready_condition.get("reason", "")
        condition_message: Optional[str] = ready_condition.get("message") or None

        # Map Flux condition to strata status vocabulary
        if condition_status == "True":
            sync_status = "Synced"
            health_status = "Healthy"
        elif condition_reason in ("Progressing", "Reconciling"):
            sync_status = "OutOfSync"
            health_status = "Progressing"
        else:
            sync_status = "OutOfSync"
            health_status = "Degraded"

        # last_synced_at from condition.lastTransitionTime
        last_synced_at = None
        last_transition = ready_condition.get("lastTransitionTime")
        if last_transition:
            try:
                last_synced_at = datetime.fromisoformat(last_transition.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Flux stores the last applied revision in .status.lastAppliedRevision
        revision = status.get("lastAppliedRevision") or status.get("lastAttemptedRevision") or None
        drift = bool(revision and revision != intended_revision)

        rec_result = ReconciliationResult(
            sync_status=sync_status,
            health_status=health_status,
            last_synced_at=last_synced_at,
            revision=revision,
            intended_revision=intended_revision,
            drift=drift,
            message=condition_message,
        )
        return True, rec_result, messages
