"""Show in-flight promotions (strata promote status)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.promote.base_promote_command import BasePromoteCommand
from strata.controllers.promote_controller import PromoteController


class StatusPromoteCommand(BasePromoteCommand):
    """Show all in-flight promotions from the local activity log directory."""

    OPERATION = "promote_status"

    def __init__(
        self,
        work_path: Optional[str] = None,
        ai: bool = False,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._ai = ai
        self._controller: Optional[PromoteController] = None
        self._result: list = []

    def get_required_integrations(self) -> dict:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self._controller = PromoteController()
        return True

    def _execute(self) -> bool:
        assert self._controller is not None
        self._result = self._controller.get_status(Path(str(self._work_path)))
        self._output_data = self._result
        self._render()
        if self._ai and self._result:
            self._run_ai_promotion_analysis()
        return True

    def _render(self) -> None:
        if self._output_format == "json":
            click.echo(json.dumps({"success": True, "promotions": self._result}, indent=2))
        elif self._output_format == "text":
            for p in self._result:
                click.echo(f"{p['target']}\t{p.get('version', '?')}\t{p['ring']}\t{p['status']}")
        elif not self._output_quiet:
            if not self._result:
                click.echo("No in-flight promotions found.")
                return
            click.echo("In-flight promotions:")
            for p in self._result:
                status_icon = "🔄" if p["status"] == "in-progress" else "✅"
                click.echo(
                    f"  {status_icon}  {p['target']} → {p['ring']}  "
                    f"{p.get('previous_version', '?')} → {p.get('version', '?')}  "
                    f"[{p['status']}]"
                )
                if p.get("branch"):
                    click.echo(f"       branch: {p['branch']}")

    # -------------------------------------------------------------------------
    # AI promotion analysis
    # -------------------------------------------------------------------------

    def _run_ai_promotion_analysis(self) -> None:
        """Explain in-flight promotions and recommend next actions."""
        from strata.integrations.ai import find_ai_integration

        # StatusPromoteCommand has no configuration service — try loading one
        config_svc = None
        try:
            from strata.controllers.solution_controller import SolutionController
            from strata.services.configuration_service import ConfigurationService

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
        if integration is None:
            if not self._output_quiet:
                click.echo("  ⚠  --ai flag set but no ai_agent integration configured")
            return
        ok, msg = integration.ensure_available()
        if not ok:
            self._messages.append(f"AI provider unavailable: {msg}")
            return

        context = {"workspace": str(self._work_path)}
        if not self._output_quiet:
            click.echo(f"\n  🤖  AI promotion analysis ({integration.integration_name}) …")

        try:
            response = integration.explain_promotion_status(self._result, context)
        except Exception as exc:
            self._messages.append(f"AI promotion analysis failed: {exc}")
            return

        if not self._output_quiet:
            self._print_ai_promotion(response.content)

    def _print_ai_promotion(self, content: str) -> None:
        import json as _json

        sep = "\u2500" * 48
        click.echo(f"\n  {sep}")
        click.echo("  🤖  AI Promotion Analysis")
        click.echo(f"  {sep}")
        try:
            parsed = _json.loads(content)
            click.echo(f"\n  {parsed.get('summary', '')}")
            if parsed.get("attention"):
                click.echo("\n  ⚠  Needs attention:")
                for a in parsed["attention"]:
                    click.echo(f"    • {a}")
            if parsed.get("promotions"):
                click.echo("\n  Promotion details:")
                for p in parsed["promotions"]:
                    if isinstance(p, dict):
                        target = p.get("target", "?")
                        ring = p.get("ring", "?")
                        assessment = p.get("assessment", "")
                        next_action = p.get("next_action", "")
                        click.echo(f"    [{ring}] {target}: {assessment}")
                        if next_action:
                            click.echo(f"      → {next_action}")
            if parsed.get("recommendations"):
                click.echo("\n  Recommendations:")
                for r in parsed["recommendations"]:
                    click.echo(f"    → {r}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo("")
