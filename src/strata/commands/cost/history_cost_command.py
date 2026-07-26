"""Command to show cost history for a deployment."""

from typing import Any, Dict, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand


class HistoryCostCommand(BaseDeployCommand):
    """Show historical cost snapshots for a deployment.

    Reads from ``.strata/cost/{deployment}.cost-history.json`` — the history
    file is populated automatically each time ``strata cost show`` runs.
    With ``--ai``, analyses the history for trends, spikes, and cost anomalies.

    Exit codes:
      0  — history displayed (or empty history, which is not an error)
      1  — system error (deployment file not found, etc.)
    """

    OPERATION = "cost_history"
    SHOW_CHROME = True

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        last: int = 10,
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
        self._last = last
        self._ai = ai

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        from strata.utils.cost_history import CostHistoryStore

        deployment_name = str(self._deployment_service.get_name())
        store = CostHistoryStore(self._work_path, deployment_name)
        store.load()
        snapshots = store.list_snapshots(last=self._last)

        self._output_data = {
            "deployment": deployment_name,
            "snapshots": snapshots,
        }

        if self._is_console_output():
            self._render_history(deployment_name, snapshots)

        if self._ai and snapshots:
            self._run_ai_cost_analysis(deployment_name, snapshots)

        return True

    # -------------------------------------------------------------------------
    # Console rendering
    # -------------------------------------------------------------------------

    def _render_history(self, deployment_name: str, snapshots: List[Dict[str, Any]]) -> None:
        if not snapshots:
            click.echo(f"\n  No cost history found for '{deployment_name}'.")
            click.echo("  Run: strata cost show -f <deployment.yaml>\n")
            return

        click.echo("")
        click.echo("─" * 70)
        click.echo(f"💰 Cost History — {deployment_name} (last {len(snapshots)} snapshots)")
        click.echo("─" * 70)
        click.echo(f"\n{'Date (UTC)':<22} {'Version':<10} {'Monthly':>14} {'Delta':>12}")
        click.echo(f"{'─' * 22} {'─' * 10} {'─' * 14} {'─' * 12}")

        for snap in snapshots:
            recorded = snap.get("recorded_at", "—")[:19].replace("T", " ")
            version = snap.get("version", "—")[:9]
            total = snap.get("total_monthly")
            currency = snap.get("currency", "")
            delta = snap.get("delta_from_previous")

            total_str = f"{total:>12.2f} {currency}" if total is not None else "—"
            if delta is None:
                delta_str = "—"
            elif delta >= 0:
                delta_str = f"+{delta:.2f}"
            else:
                delta_str = f"{delta:.2f}"

            click.echo(f"{recorded:<22} {version:<10} {total_str:>14} {delta_str:>12}")

        click.echo("")

    # -------------------------------------------------------------------------
    # AI cost trend analysis
    # -------------------------------------------------------------------------

    def _run_ai_cost_analysis(self, deployment_name: str, snapshots: List[Dict[str, Any]]) -> None:
        """Run AI trend analysis on the cost history snapshots."""
        from pathlib import Path

        from strata.integrations.ai import find_ai_integration

        config_svc = None
        try:
            from strata.services.configuration_service import ConfigurationService

            if self._configuration_service:
                config_svc = self._configuration_service
            else:
                from strata.controllers.solution_controller import SolutionController

                sol = SolutionController(work_path=self._work_path)
                sol.load()
                profile, _ = sol.get_active_profile()
                if profile:
                    for cp in [str(p.path) for p in (profile.configfile_paths or [])]:
                        svc = ConfigurationService.load(cp)
                        if svc.model:
                            config_svc = svc
                            break
        except Exception:
            pass

        integration = find_ai_integration(config_svc)
        if integration is None or not integration.ensure_available()[0]:
            if self._is_console_output():
                click.echo("  ⚠  --ai flag set but no reachable ai_agent integration configured")
            return

        stats = self._compute_cost_stats(snapshots)

        context: Dict[str, Any] = {
            "deployment": deployment_name,
        }

        if self._is_console_output():
            click.echo(f"\n  🤖  AI cost analysis ({integration.integration_name}) …\n")

        work_path = Path(self._work_path) if self._work_path else None
        try:
            response = integration.analyse_cost_trend(snapshots, stats, context, work_path=work_path)
        except Exception as exc:
            self._messages.append(f"AI cost analysis failed: {exc}")
            return

        self._output_data.setdefault("ai_analysis", {})["cost_trend"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
            "snapshots_analysed": len(snapshots),
        }

        if self._is_console_output():
            self._print_ai_cost_analysis(response.content)

    def _compute_cost_stats(self, snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Pre-compute cost statistics for the AI prompt."""
        totals: list[float] = [float(s["total_monthly"]) for s in snapshots if s.get("total_monthly") is not None]
        currency = snapshots[0].get("currency", "USD") if snapshots else "USD"

        if not totals:
            return {
                "total": len(snapshots),
                "currency": currency,
                "earliest_cost": 0.0,
                "latest_cost": 0.0,
                "min_cost": 0.0,
                "max_cost": 0.0,
                "avg_cost": 0.0,
                "net_delta": 0.0,
                "net_delta_pct": 0.0,
                "max_spike_delta": 0.0,
                "max_spike_at": "—",
            }

        # snapshots are newest-first; earliest is last element
        earliest = totals[-1]
        latest = totals[0]
        net_delta = latest - earliest
        net_delta_pct = (net_delta / earliest * 100) if earliest else 0.0

        # Find biggest single-step spike
        max_spike_delta = 0.0
        max_spike_at = "—"
        for snap in snapshots:
            d = snap.get("delta_from_previous")
            if d is not None and d > max_spike_delta:
                max_spike_delta = d
                max_spike_at = snap.get("recorded_at", "?")[:19]

        return {
            "total": len(snapshots),
            "currency": currency,
            "earliest_cost": earliest,
            "latest_cost": latest,
            "min_cost": min(totals),
            "max_cost": max(totals),
            "avg_cost": sum(totals) / len(totals),
            "net_delta": net_delta,
            "net_delta_pct": net_delta_pct,
            "max_spike_delta": max_spike_delta,
            "max_spike_at": max_spike_at,
        }

    def _print_ai_cost_analysis(self, content: str) -> None:
        import json as _json

        sep = "─" * 70
        click.echo(f"  {sep}")
        try:
            parsed = _json.loads(content)
            trend = parsed.get("trend", "")
            trend_icon = {
                "stable": "🟢",
                "rising": "🔴",
                "falling": "🟢",
                "volatile": "🟠",
            }.get(trend.lower(), "ℹ️")

            click.echo(f"  {trend_icon}  {parsed.get('summary', '')}\n")

            tc = parsed.get("total_change") or {}
            if tc:
                frm = tc.get("from_cost", 0)
                to = tc.get("to_cost", 0)
                delta = tc.get("delta", 0)
                pct = tc.get("delta_pct", 0)
                cur = tc.get("currency", "")
                sign = "+" if delta >= 0 else ""
                click.echo(f"  Cost window: {frm:.2f} → {to:.2f} {cur}  ({sign}{delta:.2f}, {sign}{pct:.1f}%)\n")

            spikes: list = parsed.get("spikes") or []
            if spikes:
                click.echo("  Cost spikes:")
                for spike in spikes:
                    ts = spike.get("recorded_at", "?")[:19]
                    ver = spike.get("version", "")
                    d = spike.get("delta", 0)
                    pct = spike.get("delta_pct", 0)
                    cause = spike.get("likely_cause", "")
                    provs = spike.get("contributing_provisioners") or []
                    ver_str = f" v{ver}" if ver else ""
                    prov_str = f" [{', '.join(provs)}]" if provs else ""
                    click.echo(f"    [{ts}]{ver_str}{prov_str}  +{d:.2f} (+{pct:.1f}%)")
                    if cause:
                        click.echo(f"      → {cause}")
                click.echo("")

            recs: list = parsed.get("recommendations") or []
            if recs:
                click.echo("  Recommendations:")
                for i, rec in enumerate(recs, 1):
                    click.echo(f"    {i}. {rec}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo(f"  {sep}\n")
