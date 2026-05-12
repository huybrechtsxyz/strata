"""Command to display workspace solution status."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from xyz_platform.commands.base_command import BaseCommand


class StatusCommand(BaseCommand):
    """Show workspace health: solution identity, active profile, repos, and integrations.

    Works both inside and outside an initialized workspace.  When no
    ``solution.json`` is found the output is limited to work-path and tool
    availability.
    """

    OPERATION = "status"
    INIT_REQUIRED = False  # degrades gracefully when workspace is not initialized

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

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

            if not self._run_execution():
                if self._is_console_output():
                    click.echo("\n❌  Execution failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution processing failed")
                self._finalize(success=False)
                return False

            self._finalize(success=True)
            return True

        except Exception as e:
            error_msg = f"Failed to show status: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _run_execution(self) -> bool:
        initialized = self._solution_controller.solution is not None

        # ── Solution identity ─────────────────────────────────────────
        solution_data: Dict[str, Any] = {
            "initialized": initialized,
            "work_path": str(self._work_path),
            "id": None,
            "name": None,
        }
        if initialized and self._solution_controller.solution is not None:
            solution_data["id"] = self._solution_controller.get_solution_id()
            solution_data["name"] = str(self._solution_controller.solution.meta.name)

        # ── Repositories ──────────────────────────────────────────────
        repos_data: List[Dict[str, Any]] = []
        if initialized:
            repos, repo_errors = self._solution_controller.get_repositories()
            self._errors.extend(repo_errors)
            for r in repos:
                if str(r.type) == "local":
                    url_path = Path(str(r.url))
                    if not url_path.is_absolute():
                        url_path = Path(os.getcwd()) / url_path
                    local_path = url_path.resolve()
                else:
                    local_path = self._work_path / r.path
                repos_data.append(
                    {
                        "name": str(r.name),
                        "url": r.url,
                        "path": r.path,
                        "type": r.type,
                        "branch": r.branch,
                        "cloned": local_path.is_dir(),
                    }
                )

        # ── Profiles ──────────────────────────────────────────────────
        profiles_data: Dict[str, Any] = {"active": None, "all": [], "paths": {}}
        if initialized:
            active_profile, _ = self._solution_controller.get_active_profile()
            all_profiles, _ = self._solution_controller.get_profiles()
            profiles_data["active"] = str(active_profile.name) if active_profile else None
            profiles_data["all"] = [str(p.name) for p in all_profiles]

            if active_profile:
                paths_dict, _ = self._solution_controller.get_profile_paths(str(active_profile.name))
                profiles_data["paths"] = {
                    kind: [{"name": str(c.name), "path": c.path} for c in items]
                    for kind, items in paths_dict.items()
                    if items
                }

        # ── Integrations ──────────────────────────────────────────────
        ic = self.get_integration_controller()
        _, integrations_status = ic.get_all_integrations_status()

        # ── Health signal ─────────────────────────────────────────────
        health, health_issues = self._compute_health(initialized, repos_data, profiles_data, integrations_status)

        self._output_data = {
            "health": {"status": health, "issues": health_issues},
            "solution": solution_data,
            "profiles": profiles_data,
            "repositories": repos_data,
            "integrations": integrations_status,
        }
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            self._print_console_output()
        return super()._after_execute()

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    def _print_console_output(self) -> None:
        sol = self._output_data["solution"]
        prof = self._output_data["profiles"]
        repos = self._output_data["repositories"]
        ints = self._output_data["integrations"]
        health = self._output_data["health"]

        # ── Health signal ─────────────────────────────────────────────
        click.echo("")
        status_icon = {"HEALTHY": "✅", "DEGRADED": "⚠️ ", "BROKEN": "❌"}.get(health["status"], "❓")
        click.echo(f"  {status_icon}  {health['status']}", nl=False)
        if health["issues"]:
            click.echo(f"  — {health['issues'][0]}", nl=False)
            if len(health["issues"]) > 1:
                click.echo(f"  (+{len(health['issues']) - 1} more)", nl=False)
        click.echo("")

        if not sol["initialized"]:
            click.echo("")
            click.echo(f"  Work path : {sol['work_path']}")
            click.echo("")
            click.echo("  ℹ️   No xyz workspace found in this directory.")
            click.echo("      Run `xyz init` to initialize a workspace here.")
            click.echo("")
            self._print_integrations(ints)
            return

        # ── Solution ──────────────────────────────────────────────────
        click.echo("")
        click.echo("  🗂️   Solution")
        click.echo(f"  Name      : {sol['name']}")
        click.echo(f"  ID        : {sol['id']}")
        click.echo(f"  Work path : {sol['work_path']}")
        click.echo("")

        # ── Profiles ──────────────────────────────────────────────────
        click.echo("  👤  Profiles")
        if prof["all"]:
            for p in prof["all"]:
                marker = "●" if p == prof["active"] else "○"
                click.echo(f"    {marker} {p}")
        else:
            click.echo("    ℹ️   No profiles configured.")
        click.echo("")

        # ── Active profile paths ───────────────────────────────────────
        if prof["active"] and prof["paths"]:
            click.echo(f"  📎  Active Profile Refs  ({prof['active']})")
            for kind, items in prof["paths"].items():
                click.echo(f"    {kind}  ({len(items)})")
                for item in items:
                    click.echo(f"      - {item['name']}  →  {item['path']}")
            click.echo("")

        # ── Repositories ──────────────────────────────────────────────
        cloned = sum(1 for r in repos if r["cloned"])
        missing = len(repos) - cloned
        click.echo(
            f"  📦  Repositories  ({len(repos)} registered, {cloned} cloned"
            + (f", {missing} missing" if missing else "")
            + ")"
        )
        if repos:
            for r in repos:
                clone_icon = "✅" if r["cloned"] else "❌"
                click.echo(f"    {clone_icon} {r['name']}  ({r['type']}/{r['branch']})  {r['path']}")
        else:
            click.echo("    ℹ️   No repositories configured.")
        click.echo("")

        # ── Integrations ──────────────────────────────────────────────
        self._print_integrations(ints)

    def _print_integrations(self, ints: Dict[str, Any]) -> None:
        click.echo("  🔌  Integrations")
        if ints:
            for name, info in sorted(ints.items()):
                ok = info.get("available", False)
                version = info.get("version") or "—"
                icon = "✅" if ok else "❌"
                click.echo(f"    {icon}  {name:<18}  {version}")
        else:
            click.echo("    ℹ️   No integrations registered.")
        click.echo("")

    # ------------------------------------------------------------------
    # Health computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_health(
        initialized: bool,
        repos: List[Dict],
        profiles: Dict[str, Any],
        integrations: Dict[str, Any],
    ) -> tuple[str, List[str]]:
        """Return (health_level, issues_list).

        BROKEN  — required integration unavailable, or workspace unreadable.
        DEGRADED — no active profile, missing repo clones, optional tool down.
        HEALTHY  — everything looks operable.
        """
        issues: List[str] = []

        if not initialized:
            return "DEGRADED", ["workspace not initialized — run xyz init"]

        # Required integrations broken (currently: none declared, flag any unavailable)
        unavailable = [n for n, i in integrations.items() if not i.get("available", False)]
        if unavailable:
            issues.append(f"{len(unavailable)} integration(s) unavailable: {', '.join(unavailable)}")

        # No active profile
        if not profiles.get("active"):
            issues.append("no active profile — run xyz profile activate <name>")

        # Missing clones
        missing = [r["name"] for r in repos if not r["cloned"]]
        if missing:
            issues.append(f"{len(missing)} repo(s) not cloned: {', '.join(missing)}")

        if not issues:
            return "HEALTHY", []
        if unavailable:
            return "BROKEN", issues
        return "DEGRADED", issues
