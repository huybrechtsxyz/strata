"""Cost estimation controller.

Orchestrates cost estimation for deployment builds using registered
cost estimator integrations (e.g. Infracost).

Follows the same dependency-injection pattern as DriftController:
receives a deployment_service, build_path, and solution_controller
from the CLI layer rather than re-discovering paths internally.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from strata.controllers.base_controller import BaseController
from strata.models.capabilities import ICostEstimator
from strata.models.common_models import ProvisionerType
from strata.utils.config import get_cost_cache_dir

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController
    from strata.models.workspace_model import WorkspaceIacModel
    from strata.services.deployment_service import DeploymentService
    from strata.utils.cost_history import CostHistoryStore

# Cache TTL: 7 days in seconds
_CACHE_TTL_SECONDS = 7 * 24 * 3600

# Shown when no ICostEstimator-capable integration is declared in spec.integrations.
_NO_ESTIMATOR_CONFIGURED_MSG = (
    "No cost estimator configured. Declare one in spec.integrations, e.g.:\n"
    "  integrations:\n"
    "    - name: infracost\n"
    "      type: infracost\n"
    "      capabilities: [cost]\n"
    "See: https://www.infracost.io/docs/install"
)


class CostController(BaseController):
    """Controller for infrastructure cost estimation operations."""

    def __init__(self, work_path: Optional[Path] = None) -> None:
        super().__init__()
        self._work_path = work_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_auto_diff_enabled(self) -> bool:
        """Return True if a cost estimator is declared in ``spec.integrations``.

        Gates the automatic cost diff that ``deploy run --dry-run`` runs after
        each stage's plan — e.g.::

            spec:
              integrations:
                - name: infracost
                  type: infracost
                  capabilities: [cost]
                  enabled: true   # default; set false to disable

        An installed ``infracost`` binary alone is not enough to trigger the
        automatic dry-run diff (or ``strata cost show``/``strata cost diff``)
        — it must be declared, consistent with how every other integration
        (secret stores, provisioners, etc.) is gated by declaration rather
        than by probing for installed binaries. This only checks whether it's
        *declared*; use :meth:`is_available` to also confirm it's installed
        and working.
        """
        return self._get_estimator() is not None

    def show(
        self,
        deployment_service: "DeploymentService",
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
        currency: Optional[str] = None,
        provisioner_filter: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Get cost breakdown for a deployment's terraform provisioners.

        Iterates over terraform provisioners in the workspace and runs
        ``infracost breakdown`` on each. Returns combined results.

        Results are cached in ``.strata/cache/cost/`` keyed by a hash of
        the terraform directory contents. Cache TTL is 7 days.

        Args:
            deployment_service: Loaded deployment service with resolved workspace.
            build_path: Base build directory (e.g. work_path / "build").
            solution_controller: Optional solution context for path resolution.
            currency: Optional ISO 4217 currency code (e.g. "EUR", "GBP").
            provisioner_filter: Optional provisioner name to limit estimation to.
            force_refresh: Skip cache and always run infracost.

        Returns:
            Tuple of (success, result_dict).
            On success: result_dict contains cost data keyed by provisioner name.
            On failure: result_dict contains ``{"error": "<reason>"}``.
        """
        estimator = self._get_estimator()
        if estimator is None:
            return self._fail(_NO_ESTIMATOR_CONFIGURED_MSG)

        available, msg = estimator.ensure_available()
        if not available:
            return self._fail(msg)

        provisioners = self._get_terraform_provisioners(deployment_service, provisioner_filter)
        if not provisioners:
            return self._fail(
                "No terraform provisioners found in workspace."
                + (f" (filter: '{provisioner_filter}')" if provisioner_filter else "")
            )

        results: Dict[str, Any] = {}
        for iac in provisioners:
            terraform_path = self._resolve_provisioner_path(iac, deployment_service, build_path, solution_controller)
            if terraform_path is None:
                self._add_error(f"Build artifacts not found for provisioner '{iac.name}'. Run: strata build run")
                continue

            if not (terraform_path / ".terraform").exists():
                self._add_error(
                    f"Terraform not initialized for provisioner '{iac.name}' at {terraform_path}. Run: terraform init"
                )
                continue

            # Check cache first (unless force_refresh)
            cache_key = self._compute_cache_key(terraform_path, currency)
            if not force_refresh:
                cached = self._read_cache(cache_key)
                if cached is not None:
                    results[str(iac.name)] = cached
                    self._add_message(f"Cost estimate loaded from cache for provisioner '{iac.name}'")
                    continue

            try:
                result = estimator.breakdown(str(terraform_path), currency=currency)
                self._write_cache(cache_key, result)
                results[str(iac.name)] = result
                self._add_message(f"Cost estimate retrieved for provisioner '{iac.name}'")
            except Exception as exc:
                self._add_error(f"Cost estimation failed for provisioner '{iac.name}': {exc}")

        if not results:
            return False, {"error": "No cost estimates could be produced", "details": self.get_errors()}

        combined = {"provisioners": results}

        # Write cost.json alongside platform.json in the build directory
        self._write_cost_json(combined, deployment_service, build_path)

        # Append snapshot to cost history
        self._record_history_snapshot(
            cost_data=combined,
            deployment_service=deployment_service,
            currency=currency or "USD",
        )

        return True, combined

    def _write_cost_json(
        self,
        data: Dict[str, Any],
        deployment_service: "DeploymentService",
        build_path: Path,
    ) -> None:
        """Write cost.json alongside platform.json in the deployment build directory. Non-fatal."""
        try:
            deployment_build_path = deployment_service.get_build_path(build_path)
            deployment_build_path.mkdir(parents=True, exist_ok=True)
            cost_path = deployment_build_path / "cost.json"
            cost_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self._add_message(f"cost.json written: {cost_path}")
        except Exception as exc:
            self.logger.debug("cost_json_write_failed", error=str(exc))
            # Non-fatal — cost.json write failure does not affect the estimate result

    def _record_history_snapshot(
        self,
        cost_data: Dict[str, Any],
        deployment_service: "DeploymentService",
        currency: str = "USD",
    ) -> None:
        """Append a cost snapshot to the deployment's history file. Non-fatal."""
        if self._work_path is None:
            return
        try:
            from strata.utils.cost_history import CostHistoryStore

            deployment_name = deployment_service.get_name() if deployment_service else "unknown"
            version = deployment_service.get_version() if deployment_service else None

            store = CostHistoryStore(self._work_path, str(deployment_name))
            store.load()
            store.record_snapshot(cost_data=cost_data, version=version, currency=currency)
            store.save()
            self.logger.debug("cost_history_recorded", deployment=deployment_name)
            self._push_cost_history(store, deployment_service, self._work_path)
            self._forward_cost_audit_event(store, deployment_service, self._work_path)
        except Exception as exc:
            self.logger.debug("cost_history_record_failed", error=str(exc))
            # Non-fatal

    def _push_cost_history(
        self, store: "CostHistoryStore", deployment_service: "DeploymentService", work_path: Path
    ) -> None:
        """Push the cost-history file to a durable git repo, if configured (ADR-0065 Phase 1).

        Best-effort — never raises, never affects the cost-recording result above.
        """
        try:
            from strata.controllers.audit_controller import AuditController
            from strata.services.configuration_service import ConfigurationService

            config_model = ConfigurationService.get_instance().model
            cost_cfg = getattr(getattr(config_model, "spec", None), "cost", None)
            repo_cfg = getattr(getattr(cost_cfg, "history", None), "repository", None)
            if not repo_cfg or not repo_cfg.push:
                return

            workspace_service = deployment_service.get_workspace_service() if deployment_service else None
            workspace_name = (
                str(workspace_service.model.meta.name) if workspace_service and workspace_service.model else "unknown"
            )

            AuditController(work_path=work_path).push_to_remote(
                [store.history_file],
                local_base=store.history_dir,
                remote_path=repo_cfg.path or "cost",
                repo_name=repo_cfg.name,
                workspace=workspace_name,
                commit_message="chore(cost): history update [skip ci]",
            )
        except Exception as exc:
            self.logger.debug("cost_history_push_failed", error=str(exc))
            # Non-fatal

    def _forward_cost_audit_event(
        self, store: "CostHistoryStore", deployment_service: "DeploymentService", work_path: Path
    ) -> None:
        """Forward a cost.threshold_exceeded event when the latest snapshot crosses a
        configured threshold (ADR-0066 follow-up — drift.detected's cost counterpart).

        Unlike drift, "any cost" is not inherently alert-worthy, so this requires an
        actual threshold to be configured under spec.cost.history.alert — no config,
        no event, ever. Best-effort — never raises, never affects the cost-recording
        result above.
        """
        try:
            from strata.controllers.audit_controller import AuditController
            from strata.services.configuration_service import ConfigurationService

            config_model = ConfigurationService.get_instance().model
            cost_cfg = getattr(getattr(config_model, "spec", None), "cost", None)
            alert_cfg = getattr(getattr(cost_cfg, "history", None), "alert", None)
            if alert_cfg is None or (alert_cfg.max_monthly is None and alert_cfg.delta_percent is None):
                return

            latest = store.latest()
            if latest is None:
                return

            total = latest.get("total_monthly")
            delta = latest.get("delta_from_previous")
            if total is None:
                return

            alert_reason: Optional[str] = None
            if alert_cfg.max_monthly is not None and total > alert_cfg.max_monthly:
                alert_reason = "ceiling"
            elif alert_cfg.delta_percent is not None and delta is not None and delta > 0:
                previous_total = total - delta
                if previous_total > 0 and (delta / previous_total) * 100 >= alert_cfg.delta_percent:
                    alert_reason = "delta"

            if alert_reason is None:
                return

            audit_cfg = getattr(getattr(config_model, "spec", None), "audit", None)
            deployment_name = deployment_service.get_name() if deployment_service else "unknown"

            payload: Dict[str, Any] = {
                "deployment": str(deployment_name),
                "recorded_at": latest.get("recorded_at"),
                "currency": latest.get("currency"),
                "total_monthly": total,
                "delta_from_previous": delta,
                "alert_reason": alert_reason,
                "provisioners": latest.get("provisioners"),
            }
            if latest.get("version"):
                payload["version"] = latest["version"]

            AuditController(work_path=work_path).forward("cost.threshold_exceeded", payload, audit_config=audit_cfg)
        except Exception as exc:
            self.logger.debug("cost_audit_forward_failed", error=str(exc))
            # Non-fatal

    def invalidate_cache(self, work_path: Optional[Path] = None) -> int:
        """Remove all cached cost estimates.

        Args:
            work_path: Workspace root. Falls back to self._work_path if not given.

        Returns:
            Number of cache files removed.
        """
        cache_dir = self._get_cache_dir(work_path)
        if cache_dir is None or not cache_dir.exists():
            return 0
        count = 0
        for f in cache_dir.glob("*.json"):
            try:
                f.unlink()
                count += 1
            except OSError:
                pass
        return count

    def diff(
        self,
        deployment_service: "DeploymentService",
        build_path: Path,
        plan_file: str,
        solution_controller: Optional["SolutionController"] = None,
        currency: Optional[str] = None,
        provisioner_filter: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Get cost diff for a terraform plan (used during deploy --dry-run).

        Args:
            deployment_service: Loaded deployment service.
            build_path: Base build directory.
            plan_file: Path to the terraform plan JSON file.
            solution_controller: Optional solution context.
            currency: Optional ISO 4217 currency code.
            provisioner_filter: Optional provisioner name to limit to.

        Returns:
            Tuple of (success, result_dict).
        """
        estimator = self._get_estimator()
        if estimator is None:
            return self._fail(_NO_ESTIMATOR_CONFIGURED_MSG)

        available, msg = estimator.ensure_available()
        if not available:
            return self._fail(msg)

        if not Path(plan_file).exists():
            return self._fail(f"Plan file not found: {plan_file}")

        provisioners = self._get_terraform_provisioners(deployment_service, provisioner_filter)
        if not provisioners:
            return self._fail("No terraform provisioners found in workspace.")

        # For diff, use the first matching provisioner (plan is per-provisioner)
        iac = provisioners[0]
        terraform_path = self._resolve_provisioner_path(iac, deployment_service, build_path, solution_controller)
        if terraform_path is None:
            return self._fail(f"Build artifacts not found for provisioner '{iac.name}'.")

        try:
            result = estimator.diff(str(terraform_path), plan_file, currency=currency)
            self._add_message(f"Cost diff retrieved for provisioner '{iac.name}'")
            return True, result
        except Exception as exc:
            return self._fail(f"Cost diff failed: {exc}")

    def is_available(self) -> bool:
        """Return True if a cost estimator integration is available and installed."""
        estimator = self._get_estimator()
        if estimator is None:
            return False
        available, _ = estimator.ensure_available()
        return available

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_estimator(self) -> Optional[ICostEstimator]:
        """Return the ICostEstimator integration declared in ``spec.integrations``, or None.

        Requires an explicit declaration (``type: infracost``, ``capabilities:
        [cost]``, not ``enabled: false``) — consistent with every other
        integration (secret stores, provisioners, etc.). Does NOT fall back to
        probing for an installed binary that isn't declared.

        Deliberately does not filter on live availability here (unlike
        ``IntegrationService.get_integration_with_capability``) so that a
        declared-but-not-installed integration is still returned — callers
        call ``ensure_available()`` on the result themselves to get a precise,
        actionable error message instead of a generic "not found".
        """
        from strata.services.integration_service import IntegrationService

        svc = IntegrationService.get_instance()
        if not svc.is_initialized():
            svc.initialize_integrations()
        for name in svc.get_integrations_with_capability(ICostEstimator):
            integration = svc.get_integration(name)
            if integration is not None:
                return integration
        return None

    def _get_terraform_provisioners(
        self,
        deployment_service: "DeploymentService",
        provisioner_filter: Optional[str] = None,
    ) -> List["WorkspaceIacModel"]:
        """Return terraform provisioners from the workspace.

        Args:
            deployment_service: Loaded deployment service (must have workspace resolved).
            provisioner_filter: Optional name to match a specific provisioner.

        Returns:
            List of WorkspaceIacModel entries with provisioner == "terraform".
        """
        ws_service = deployment_service.get_workspace_service()
        if ws_service is None or ws_service.model is None:
            return []

        provisioners = ws_service.model.spec.provisioners or []
        terraform_provisioners = [p for p in provisioners if p.provisioner == ProvisionerType.TERRAFORM]

        if provisioner_filter:
            terraform_provisioners = [p for p in terraform_provisioners if str(p.name) == provisioner_filter]

        return terraform_provisioners

    def _resolve_provisioner_path(
        self,
        iac: "WorkspaceIacModel",
        deployment_service: "DeploymentService",
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
    ) -> Optional[Path]:
        """Resolve the terraform working directory for a provisioner.

        Uses the same path resolution as deployers:
        - solution_controller.get_provisioner_path() when available
        - Fallback: deployment_service.get_build_path() / source_path

        Returns:
            Path to the provisioner directory, or None if it doesn't exist.
        """
        try:
            if solution_controller is not None:
                path = solution_controller.get_provisioner_path(deployment_service, build_path, iac)
            else:
                # Fallback: manual resolution matching get_provisioner_path logic
                if iac.source is None:
                    return None
                source_path = iac.source.target_path or iac.source.source_path
                if source_path is None:
                    return None
                path = deployment_service.get_build_path(build_path) / source_path

            return path if path.exists() else None
        except (ValueError, AttributeError):
            return None

    def _fail(self, message: str) -> Tuple[bool, Dict[str, Any]]:
        """Add error and return a failure tuple."""
        self._add_error(message)
        return False, {"error": message}

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _get_cache_dir(self, work_path: Optional[Path] = None) -> Optional[Path]:
        """Return the cache directory, or None if work_path is unavailable."""
        root = work_path or self._work_path
        if root is None:
            return None
        return get_cost_cache_dir(root)

    def _compute_cache_key(self, terraform_path: Path, currency: Optional[str]) -> str:
        """Compute a cache key from terraform directory content hash + currency.

        Hash is computed from file names + sizes + mtimes (fast, avoids reading
        all file content) — including mtime catches same-byte-count edits that
        name+size alone would miss. Adding currency ensures separate cache
        entries per currency.
        """
        h = hashlib.sha256()
        try:
            for f in sorted(terraform_path.rglob("*")):
                if f.is_file() and ".terraform" not in f.parts:
                    stat = f.stat()
                    h.update(f.name.encode())
                    h.update(str(stat.st_size).encode())
                    h.update(str(stat.st_mtime_ns).encode())
        except OSError:
            pass
        h.update((currency or "USD").encode())
        return h.hexdigest()[:16]

    def _read_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Return cached result if it exists and is within TTL, else None."""
        cache_dir = self._get_cache_dir()
        if cache_dir is None:
            return None
        cache_file = cache_dir / f"{cache_key}.json"
        if not cache_file.exists():
            return None
        try:
            age = time.time() - cache_file.stat().st_mtime
            if age > _CACHE_TTL_SECONDS:
                cache_file.unlink(missing_ok=True)
                return None
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, cache_key: str, data: Dict[str, Any]) -> None:
        """Write result to cache. Non-fatal on failure."""
        cache_dir = self._get_cache_dir()
        if cache_dir is None:
            return
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"{cache_key}.json"
            cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass  # cache write failures are non-fatal
