"""Command to display download and setup guidance for a tool integration."""

from __future__ import annotations

import os
import platform
from typing import Any, ClassVar, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.tools_controller import ToolsController
from strata.logger import get_logger


class InstallToolsCommand(BaseCommand):
    """Show download URL, required env vars, and auth methods for an integration.

    Does not install anything — purely informational.
    With ``--ai``, combines runtime state with static setup info to produce a
    tailored, state-aware installation guide via the configured AI provider.
    """

    OPERATION = "tools_install"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        name: str,
        env_file: Optional[str] = None,
        ai: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)
        self._name = name
        self._env_file = env_file
        self._ai = ai

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — run super for side-effects only.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = ToolsController()
        success, info, errors = controller.install_info(self._name)
        for err in errors:
            self._errors.append(err)
        if not success:
            return False
        if self._env_file:
            self._write_env_file(info)
        if self._is_console_output():
            self._print_guide(info)
        self._output_data["integration"] = info

        if self._ai:
            # Merge runtime check state (available, version) into the detail dict
            _, check_detail, _ = controller.check(self._name)
            self._run_ai_setup_guide(check_detail if check_detail else info)

        return True

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    def _print_guide(self, info: dict) -> None:
        name = info.get("name", self._name)
        install_url = info.get("install_url") or "—"
        command = info.get("command") or "SDK only — no CLI binary"
        env_vars: list = info.get("env_vars") or []
        auth_methods: list = info.get("auth_methods") or []
        yaml_example: str = info.get("yaml_example") or ""

        click.echo("")
        click.echo(f"  Integration : {name}")
        click.echo(f"  CLI command : {command}")
        click.echo(f"  Download    : {install_url}")
        click.echo("")

        if env_vars:
            click.echo("  Environment variables:")
            col = max(len(ev["name"]) for ev in env_vars) + 2
            for ev in env_vars:
                req = "required" if ev.get("required") else "optional"
                is_set = "✓ set" if os.environ.get(ev["name"]) else "✗ not set"
                click.echo(f"    {ev['name']:<{col}}  {req:<8}  {is_set:<10}  {ev.get('purpose', '')}")
            click.echo("")

        if auth_methods:
            click.echo("  Authentication methods:")
            for am in auth_methods:
                click.echo(f"    • {am.get('method', '')}")
                click.echo(f"      {am.get('description', '')}")
            click.echo("")

        if yaml_example:
            click.echo("  YAML example:")
            for line in yaml_example.splitlines():
                click.echo(f"    {line}")
            click.echo("")

        if self._env_file:
            click.echo(f"  Env-file written to: {self._env_file}")
            click.echo("")

    # ------------------------------------------------------------------
    # Env-file writer
    # ------------------------------------------------------------------

    def _write_env_file(self, info: dict) -> None:
        if not self._env_file:
            return
        name = info.get("name", self._name)
        install_url = info.get("install_url") or ""
        env_vars: list = info.get("env_vars") or []

        lines = [
            f"# {name} environment variables",
            f"# Download: {install_url}",
            "# Source this file before running strata commands that use this integration.",
            "# Keep this file on your machine — do NOT commit it to source control.",
            "",
        ]
        for ev in env_vars:
            req = "required" if ev.get("required") else "optional"
            lines.append(f"# {ev['name']} ({req}) — {ev.get('purpose', '')}")
            existing = os.environ.get(ev["name"])
            value = existing if existing else ""
            lines.append(f"# {ev['name']}={value}")
            lines.append("")

        content = "\n".join(lines)
        with open(self._env_file, "w", encoding="utf-8") as fh:
            fh.write(content)

    # ------------------------------------------------------------------
    # AI setup guide
    # ------------------------------------------------------------------

    def _run_ai_setup_guide(self, tool_detail: Dict[str, Any]) -> None:
        """Run AI-powered tailored setup guide for the integration."""
        from strata.integrations.ai import find_ai_integration

        # Try to load a configuration service from the active workspace profile
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
        if integration is None or not integration.ensure_available()[0]:
            if self._is_console_output():
                click.echo("  \u26a0  --ai flag set but no reachable ai_agent integration configured")
            return

        os_name = platform.system()
        workspace_context = self._build_workspace_context()

        if self._is_console_output():
            click.echo(f"\n  \U0001f916  AI setup guide ({integration.integration_name}) \u2026\n")

        work_path = self._work_path
        try:
            from pathlib import Path

            response = integration.guide_tool_setup(
                tool_detail,
                os_name,
                workspace_context,
                work_path=Path(work_path) if work_path else None,
            )
        except Exception as exc:
            self._messages.append(f"AI setup guide failed: {exc}")
            return

        self._output_data.setdefault("ai_analysis", {})["tool_setup"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
        }

        if self._is_console_output():
            self._print_ai_setup_guide(response.content)

    def _build_workspace_context(self) -> Dict[str, Any]:
        """Collect provisioner/backend types from the active workspace profile."""
        context: Dict[str, Any] = {}
        try:
            from strata.controllers.solution_controller import SolutionController
            from strata.services.workspace_service import WorkspaceService

            sol = SolutionController(work_path=self._work_path)
            sol.load()
            profile, _ = sol.get_active_profile()
            if profile:
                for dep_ref in profile.configfile_paths or []:
                    try:
                        import yaml

                        from strata.models.deployment_model import DeploymentModel

                        dep_path = dep_ref.path if hasattr(dep_ref, "path") else str(dep_ref)
                        raw = __import__("pathlib").Path(dep_path).read_text(encoding="utf-8")
                        dep = DeploymentModel.model_validate(yaml.safe_load(raw))
                        ws_file = dep.spec.workspace.file if dep.spec.workspace else None
                        if ws_file:
                            import os as _os

                            ws_path = _os.path.join(_os.path.dirname(dep_path), ws_file)
                            ws_svc = WorkspaceService.load(ws_path)
                            if ws_svc.is_validated() and ws_svc.model:
                                prov_types: List[str] = []
                                backend_types: List[str] = []
                                for prov in ws_svc.model.spec.provisioners or []:
                                    prov_types.append(prov.provisioner)
                                    if hasattr(prov, "backend") and prov.backend:
                                        bt = getattr(prov.backend, "type", None)
                                        if bt:
                                            backend_types.append(str(bt))
                                if prov_types:
                                    context["provisioner_types"] = list(dict.fromkeys(prov_types))
                                if backend_types:
                                    context["backend_types"] = list(dict.fromkeys(backend_types))
                    except Exception:
                        pass
        except Exception:
            pass
        return context

    def _print_ai_setup_guide(self, content: str) -> None:
        import json as _json

        sep = "\u2500" * 60
        click.echo(f"  {sep}")
        try:
            parsed = _json.loads(content)
            status_summary = parsed.get("status_summary", "")
            if status_summary:
                click.echo(f"  {status_summary}\n")

            already_done: List[str] = parsed.get("already_done") or []
            if already_done:
                click.echo("  Already configured:")
                for item in already_done:
                    click.echo(f"    \u2713 {item}")
                click.echo("")

            steps: List[Dict[str, Any]] = parsed.get("steps") or []
            if steps:
                click.echo("  Steps:")
                for step in steps:
                    order = step.get("order", "?")
                    title = step.get("title", "")
                    cmds: List[str] = step.get("commands") or []
                    notes = step.get("notes", "")
                    click.echo(f"\n  {order}. {title}")
                    for cmd in cmds:
                        click.echo(f"       {cmd}")
                    if notes:
                        click.echo(f"     \u2192 {notes}")

            verify = parsed.get("verify_command", "")
            if verify:
                click.echo(f"\n  Verify: {verify}")

            refs: List[str] = parsed.get("references") or []
            if refs:
                click.echo("\n  References:")
                for ref in refs:
                    click.echo(f"    {ref}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo(f"  {sep}\n")
