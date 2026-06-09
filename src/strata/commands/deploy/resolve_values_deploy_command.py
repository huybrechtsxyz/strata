"""Command to diagnose value resolution paths without retrieving actual values."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple, Union

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.value_controller import ValueController
from strata.models.store_models import (
    FeatureStoreModel,
    SecretStoreModel,
    SecretStoreType,
    VariableStoreModel,
)


class ResolveValuesDeployCommand(BaseDeployCommand):
    """Diagnose value resolution paths without revealing actual values.

    For each key in the environment, walks the resolution chain and reports:

    - Store type and reference
    - Integration registration status
    - Integration availability (binary, auth)
    - Whether the value would resolve (with ``--probe``)

    This is the pre-flight check for ``strata build`` — it answers
    "will resolution succeed?" without running a full build.
    """

    OPERATION = "deploy_values_resolve"

    def __init__(
        self,
        file: Optional[str] = None,
        key: Optional[str] = None,
        probe: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._key = key
        self._probe = probe

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def execute(self) -> bool:
        try:
            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            ok = self._run()
            self._after_execute()
            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_values_resolve: {exc}")
            self.logger.exception("deploy_values_resolve failed")
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _run(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        env_service = self._deployment_service.get_environment_service()
        if env_service is None:
            self._errors.append("No environment attached to this deployment.")
            return False

        # Initialize integrations once (needed for availability checks)
        ValueController._ensure_integrations_initialized()

        # Collect all items
        items: List[Tuple[Union[VariableStoreModel, SecretStoreModel, FeatureStoreModel], str]] = []
        for v in env_service.get_variables():
            items.append((v, "variable"))
        for s in env_service.get_secrets():
            items.append((s, "secret"))
        for f in env_service.get_features():
            items.append((f, "feature"))

        # Filter to single key if requested
        if self._key:
            items = [(item, t) for item, t in items if item.key == self._key]
            if not items:
                self._errors.append(f"Key '{self._key}' not found in environment variables, secrets, or features.")
                return False

        if self._is_console_output():
            mode = "with probe" if self._probe else "path only"
            click.echo(f"\n🔍  Value resolution diagnostic ({mode})")
            click.echo(f"    {len(items)} key(s)\n")

        results: List[Dict[str, Any]] = []
        any_failed = False

        for item, item_type in items:
            diag = self._diagnose_item(item, item_type)
            results.append(diag)
            if not diag["ok"]:
                any_failed = True
            if self._is_console_output():
                self._print_diagnostic(diag)

        # Summary
        passed = sum(1 for r in results if r["ok"])
        failed = sum(1 for r in results if not r["ok"])

        if self._is_console_output():
            click.echo("━" * 50)
            parts = []
            if passed:
                parts.append(f"✅ {passed} ok")
            if failed:
                parts.append(f"❌ {failed} would fail")
            click.echo(f"    Summary: {' │ '.join(parts)}\n")

        self._output_data = {
            "file": str(self._file_path),
            "mode": "probe" if self._probe else "path",
            "summary": {"ok": passed, "failed": failed},
            "keys": results,
        }

        return not any_failed

    # ------------------------------------------------------------------
    # Per-item diagnostic
    # ------------------------------------------------------------------

    def _diagnose_item(
        self,
        item: Union[VariableStoreModel, SecretStoreModel, FeatureStoreModel],
        item_type: str,
    ) -> Dict[str, Any]:
        """Walk the resolution chain for a single item and report each checkpoint."""
        store_value = item.store.value  # type: ignore[union-attr]
        reference = str(item.value)

        diag: Dict[str, Any] = {
            "key": item.key,
            "type": item_type,
            "store": store_value,
            "reference": reference,
            "ok": False,
            "checks": [],
        }

        # --- constant ---
        if store_value == "constant":
            diag["checks"].append({"check": "store", "status": "ok", "detail": "literal value in YAML"})
            diag["ok"] = True
            return diag

        # --- environment ---
        if store_value == "environment":
            return self._diagnose_env_var(diag, reference)

        # --- github ---
        if isinstance(item, SecretStoreModel) and item.store == SecretStoreType.GITHUB:
            return self._diagnose_github(diag, reference)

        # --- integration-backed ---
        return self._diagnose_integration(diag, item, item_type, store_value, reference)

    def _diagnose_env_var(self, diag: Dict[str, Any], env_var: str) -> Dict[str, Any]:
        """Diagnose environment variable resolution."""
        is_set = os.environ.get(env_var) is not None
        diag["checks"].append(
            {
                "check": "env_var",
                "status": "ok" if is_set else "fail",
                "detail": f"${env_var}" + (" (set)" if is_set else " (not set)"),
            }
        )
        diag["ok"] = is_set
        return diag

    def _diagnose_github(self, diag: Dict[str, Any], reference: str) -> Dict[str, Any]:
        """Diagnose GitHub Actions secret resolution."""
        env_key = reference.upper()
        in_ci = os.environ.get("GITHUB_ACTIONS") == "true"
        is_set = os.environ.get(env_key) is not None

        diag["checks"].append(
            {
                "check": "context",
                "status": "ok" if in_ci else "warn",
                "detail": "GitHub Actions" if in_ci else "not in GitHub Actions (local run)",
            }
        )
        diag["checks"].append(
            {
                "check": "env_var",
                "status": "ok" if is_set else "fail",
                "detail": f"${env_key}" + (" (set)" if is_set else " (not set)"),
            }
        )
        diag["ok"] = is_set
        return diag

    def _diagnose_integration(
        self,
        diag: Dict[str, Any],
        item: Union[VariableStoreModel, SecretStoreModel, FeatureStoreModel],
        item_type: str,
        store_type: str,
        reference: str,
    ) -> Dict[str, Any]:
        """Diagnose integration-backed store resolution."""
        # Check 1: Integration registered?
        integration = ValueController._get_integration_by_type(store_type)
        if integration is None:
            diag["checks"].append(
                {
                    "check": "integration",
                    "status": "fail",
                    "detail": f"no integration registered for '{store_type}'",
                }
            )
            return diag

        diag["checks"].append(
            {
                "check": "integration",
                "status": "ok",
                "detail": f"'{store_type}' registered",
            }
        )

        # Check 2: Integration available?
        available, err = integration.ensure_available()
        if not available:
            diag["checks"].append(
                {
                    "check": "available",
                    "status": "fail",
                    "detail": err or "not available",
                }
            )
            return diag

        info = integration.get_info()
        version = info.get("version", "unknown")
        diag["checks"].append(
            {
                "check": "available",
                "status": "ok",
                "detail": f"v{version}" if version else "available",
            }
        )

        # Check 3 (optional): Probe — actually try to resolve
        if self._probe:
            probe_ok = self._probe_integration(integration, item, item_type, reference)
            diag["checks"].append(
                {
                    "check": "probe",
                    "status": "ok" if probe_ok else "fail",
                    "detail": "reference found" if probe_ok else "reference not found in store",
                }
            )
            diag["ok"] = probe_ok
        else:
            diag["ok"] = True

        return diag

    def _probe_integration(
        self,
        integration: Any,
        item: Union[VariableStoreModel, SecretStoreModel, FeatureStoreModel],
        item_type: str,
        reference: str,
    ) -> bool:
        """Actually attempt resolution (without revealing the value)."""
        try:
            if item_type == "variable":
                return integration.get_variable(reference) is not None
            elif item_type == "secret":
                return integration.get_secret(reference) is not None
            elif item_type == "feature":
                return integration.get_feature(reference) is not None
        except Exception:
            return False
        return False

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    def _print_diagnostic(self, diag: Dict[str, Any]) -> None:
        key = diag["key"]
        item_type = diag["type"]
        store = diag["store"]
        ok = diag["ok"]

        icon = "✅" if ok else "❌"
        click.echo(f"  {icon} {key}  ({item_type}, store: {store})")

        # Reference
        click.echo(f"       Reference: {diag['reference']}")

        # Checks
        for check in diag["checks"]:
            status = check["status"]
            if status == "ok":
                marker = "✅"
            elif status == "warn":
                marker = "⚠️ "
            else:
                marker = "❌"
            click.echo(f"       {marker} {check['check']}: {check['detail']}")

        click.echo()
