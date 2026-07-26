"""Command to list resolved variables, secrets, and feature flags for a deployment."""

from typing import Any, Dict, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.value_controller import ValueController
from strata.models.store_models import (
    FeatureStoreModel,
    SecretStoreModel,
    VariableStoreModel,
)


def _mask(value: Any) -> str:
    """Return first 3 chars + five asterisks, or all asterisks if shorter."""
    s = str(value)
    if len(s) <= 3:
        return "*" * len(s)
    return s[:3] + "*****"


class ListValuesDeployCommand(BaseDeployCommand):
    """List all variables, secrets, and feature flags declared for a deployment.

    Resolves every declared entry against its store backend and displays:
      - variables : full resolved value
      - secrets   : masked  (first 3 chars + *****)
      - features  : true / false

    Entries that cannot be resolved show the error inline.

    Use ``strata values get`` to reveal a full secret or variable value.
    """

    OPERATION = "deploy_values_list"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        type_filter: Optional[str] = None,
        show_store: bool = False,
        unresolved_only: bool = False,
        trace: bool = False,
        ai: bool = False,
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
        self._stage = stage
        self._type_filter = type_filter  # "variables" | "secrets" | "features" | None (all)
        self._show_store = show_store
        self._unresolved_only = unresolved_only
        self._trace = trace
        self._ai = ai

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _execute(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        env_service = self._deployment_service.get_environment_service()
        if env_service is None:
            if self._is_console_output():
                click.echo("\n⚠️   No environment attached to this deployment — nothing to list.\n")
            self._output_data = {"file": str(self._file_path), "variables": [], "secrets": [], "features": []}
            return True

        # Collect declared items per type
        declared_vars = env_service.get_variables()
        declared_secrets = env_service.get_secrets()
        declared_features = env_service.get_features()

        # Resolve all using ValueController (does not raise on individual failures)
        controller = ValueController()
        _, resolved, _ = controller.resolve_values(self._deployment_service, strict=False)

        # Build per-type result rows
        var_rows = self._build_var_rows(
            declared_vars, resolved.variables, resolved.errors, resolved.variable_notes, resolved.variable_sources
        )
        secret_rows = self._build_secret_rows(
            declared_secrets, resolved.secrets, resolved.errors, resolved.secret_notes, resolved.secret_sources
        )
        feature_rows = self._build_feature_rows(
            declared_features, resolved.features, resolved.errors, resolved.feature_notes, resolved.feature_sources
        )

        if self._unresolved_only:
            var_rows = [r for r in var_rows if not r["ok"]]
            secret_rows = [r for r in secret_rows if not r["ok"]]
            feature_rows = [r for r in feature_rows if not r["ok"]]

        self._output_data = {
            "file": str(self._file_path),
            "variables": var_rows,
            "secrets": secret_rows,
            "features": feature_rows,
            "merge_order": resolved.merge_order,
        }

        any_failed = (
            any(not r["ok"] for r in var_rows)
            or any(not r["ok"] for r in secret_rows)
            or any(not r["ok"] for r in feature_rows)
        )

        if self._is_console_output():
            self._print_console(var_rows, secret_rows, feature_rows)

        # AI analysis — run when there are unresolved values
        if self._ai and any_failed:
            self._run_ai_values_analysis(var_rows, secret_rows, feature_rows)

        # Exit code 3 if any entry failed to resolve
        if any_failed:
            self._errors.append("One or more values could not be resolved.")
            return False

        return True

    # ------------------------------------------------------------------
    # Row builders
    # ------------------------------------------------------------------

    def _build_var_rows(
        self,
        declared: List[VariableStoreModel],
        resolved: Dict[str, Any],
        errors: List[str],
        notes: Dict[str, str],
        sources: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        rows = []
        for item in declared:
            key = item.key
            if key in resolved:
                display = str(resolved[key])
                rows.append(
                    {
                        "key": key,
                        "store": item.store.value,
                        "store_ref": str(item.value),
                        "display": display,
                        "ok": True,
                        "note": notes.get(key, ""),
                        "description": item.description,
                        "source": sources.get(key, ""),
                    }
                )
            else:
                err = next((e for e in errors if f"'{key}'" in e), "resolution failed")
                rows.append(
                    {
                        "key": key,
                        "store": item.store.value,
                        "store_ref": str(item.value),
                        "display": f"❌  {err}",
                        "ok": False,
                        "note": "",
                        "description": item.description,
                        "source": "",
                    }
                )
        return rows

    def _build_secret_rows(
        self,
        declared: List[SecretStoreModel],
        resolved: Dict[str, Any],
        errors: List[str],
        notes: Dict[str, str],
        sources: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        rows = []
        for item in declared:
            key = item.key
            if key in resolved:
                rows.append(
                    {
                        "key": key,
                        "store": item.store.value,
                        "store_ref": str(item.value),
                        "display": _mask(resolved[key]),
                        "ok": True,
                        "note": notes.get(key, ""),
                        "description": item.description,
                        "source": sources.get(key, ""),
                    }
                )
            else:
                err = next((e for e in errors if f"'{key}'" in e), "resolution failed")
                rows.append(
                    {
                        "key": key,
                        "store": item.store.value,
                        "store_ref": str(item.value),
                        "display": f"❌  {err}",
                        "ok": False,
                        "note": "",
                        "description": item.description,
                        "source": "",
                    }
                )
        return rows

    def _build_feature_rows(
        self,
        declared: List[FeatureStoreModel],
        resolved: Dict[str, Optional[bool]],
        errors: List[str],
        notes: Dict[str, str],
        sources: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        rows = []
        for item in declared:
            key = item.key
            if key in resolved:
                val = resolved[key]
                display = str(val).lower() if val is not None else "none"
                rows.append(
                    {
                        "key": key,
                        "store": item.store.value,
                        "store_ref": str(item.value),
                        "display": display,
                        "ok": True,
                        "note": notes.get(key, ""),
                        "description": item.description,
                        "source": sources.get(key, ""),
                    }
                )
            else:
                err = next((e for e in errors if f"'{key}'" in e), "resolution failed")
                rows.append(
                    {
                        "key": key,
                        "store": item.store.value,
                        "store_ref": str(item.value),
                        "display": f"❌  {err}",
                        "ok": False,
                        "note": "",
                        "description": item.description,
                        "source": "",
                    }
                )
        return rows

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    def _print_console(
        self,
        var_rows: List[Dict[str, Any]],
        secret_rows: List[Dict[str, Any]],
        feature_rows: List[Dict[str, Any]],
    ) -> None:
        tf = self._type_filter

        if tf is None or tf == "variables":
            self._print_section("VARIABLES", var_rows)
        if tf is None or tf == "secrets":
            self._print_section("SECRETS", secret_rows)
        if tf is None or tf == "features":
            self._print_section("FEATURES", feature_rows)

        if self._trace and len(self._output_data.get("merge_order", [])) > 1:
            order = self._output_data["merge_order"]
            click.echo("\n  Merge order: " + " \u2192 ".join(order))

    def _print_section(self, title: str, rows: List[Dict[str, Any]]) -> None:
        col_key = max((len(r["key"]) for r in rows), default=3)
        col_key = max(col_key, len("KEY"))
        col_store = max((len(r["store"]) for r in rows), default=5)
        col_store = max(col_store, len("STORE"))
        col_ref = max((len(r["store_ref"]) for r in rows), default=9) if self._show_store else 0
        col_val = max((len(r["display"]) for r in rows), default=5)
        col_val = max(col_val, len("VALUE / STATUS"))
        col_src = max((len(r.get("source", "")) for r in rows), default=6) if self._trace else 0
        col_src = max(col_src, len("SOURCE")) if self._trace else 0

        extra = (col_ref + 2 if self._show_store else 0) + (col_src + 2 if self._trace else 0)
        sep = "\u2500" * (col_key + col_store + col_val + extra + 6)

        click.echo(f"\n  {title}")
        click.echo(f"  {sep}")

        header = f"  {'KEY':<{col_key}}  {'STORE':<{col_store}}"
        if self._show_store:
            header += f"  {'STORE REF':<{col_ref}}"
        header += f"  {'VALUE / STATUS':<{col_val}}"
        if self._trace:
            header += f"  {'SOURCE':<{col_src}}"
        click.echo(header)

        click.echo(f"  {sep}")

        if not rows:
            click.echo("  (none)")
        for row in rows:
            line = f"  {row['key']:<{col_key}}  {row['store']:<{col_store}}"
            if self._show_store:
                line += f"  {row['store_ref']:<{col_ref}}"
            line += f"  {row['display']:<{col_val}}"
            if self._trace:
                line += f"  {row.get('source', ''):<{col_src}}"
            if row.get("note"):
                line += f"  [{row['note']}]"
            click.echo(line)
