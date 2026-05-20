"""Command that shows what would change in the environment before deploying.

Combines two layers into a single read-only view:

1. **Artifact diff** — which ``.tfvars.json`` / ``platform.json`` files would
   change vs. what is already on disk in ``.strata/build/<deployment>/``.

2. **Terraform plan** — resource-level add / change / destroy per stage,
   run against remote state.

This is a composition of the existing ``build plan`` internals with
deployment-oriented framing and output.
"""

from typing import Any, Dict, List, Optional

import click

from strata.commands.builders.plan_build_command import PlanBuildCommand


class DiffCommand(PlanBuildCommand):
    """Show what would change in the environment before deploying.

    Inherits all build-plan logic (artifact diff + terraform plan) and
    re-frames the output for deployment review.
    """

    OPERATION = "diff"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            stage=stage,
            artifacts_only=False,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )

    # ------------------------------------------------------------------
    # Override console output for deployment-oriented framing
    # ------------------------------------------------------------------

    def _print_console(
        self,
        deployment_name: str,
        diff_rows: List[Dict[str, Any]],
        plan_results: List[Dict[str, Any]],
    ) -> None:
        click.echo(f"\n🔍  Diff — {deployment_name}")
        click.echo(f"  {self._SEP}")
        click.echo("  Showing what would change if you deploy now.\n")

        self._print_artifact_diff(diff_rows)

        for pr in plan_results:
            self._print_terraform_plan(pr)

        self._print_diff_summary(diff_rows, plan_results)

    def _print_diff_summary(
        self,
        diff_rows: List[Dict[str, Any]],
        plan_results: List[Dict[str, Any]],
    ) -> None:
        click.echo(f"\n  {self._SEP}")

        new_c = sum(1 for r in diff_rows if r["status"] == "new")
        chg_c = sum(1 for r in diff_rows if r["status"] == "changed")
        unc_c = sum(1 for r in diff_rows if r["status"] == "unchanged")

        parts = []
        if new_c:
            parts.append(click.style(f"{new_c} new", fg="green"))
        if chg_c:
            parts.append(click.style(f"{chg_c} changed", fg="yellow"))
        if unc_c:
            parts.append(f"{unc_c} unchanged")

        click.echo(f"  Artifacts: {', '.join(parts) if parts else 'none'}")

        if plan_results:
            tf_ok = sum(1 for p in plan_results if p["ok"])
            tf_fail = len(plan_results) - tf_ok
            tf_line = f"  Terraform: {tf_ok} stage(s) planned"
            if tf_fail:
                tf_line += click.style(f", {tf_fail} failed", fg="red")
            click.echo(tf_line)

        # Final verdict
        has_changes = new_c > 0 or chg_c > 0
        has_tf_ok = any(p["ok"] for p in plan_results)
        if has_changes or has_tf_ok:
            click.echo(
                click.style("\n  ⚡ Changes detected.", fg="yellow")
                + " Review above, then run: strata deploy run -f <file>"
            )
        else:
            click.echo(click.style("\n  ✅ No changes detected.", fg="green") + " Environment is up to date.")

        click.echo("")
