"""Command to run a workspace health check and report diagnostic results."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.guide_controller import GuideController
from strata.controllers.tools_controller import ToolsController
from strata.utils.version import get_version

_STATUS_ICON: Dict[str, str] = {
    "pass": "✅",
    "warn": "⚠️ ",
    "fail": "❌",
    "skip": "⏭️ ",
}

_CATEGORIES = ("runtime", "workspace", "tools", "config", "auth")

# Guide phase index → check name (phases 1–6; 7/8 are build artefacts, not diagnostics)
_PHASE_CHECK_NAME = {
    1: "solution_initialized",
    2: "repos_registered",
    3: "repos_cloned",
    4: "profile_created",
    5: "profile_activated",
    6: "file_refs_registered",
}


@dataclass
class CheckResult:
    name: str
    status: Literal["pass", "warn", "fail", "skip"]
    value: Optional[str] = None
    fix_hint: Optional[str] = None


class DoctorSlnCommand(BaseCommand):
    """Run a workspace health check and report diagnostic results.

    Checks five categories:

    ``runtime``
        Python version and strata version.

    ``workspace``
        Solution initialization, repositories cloned, active profile.

    ``tools``
        External tool availability (terraform, ansible, docker, etc.).
        When ``--file`` is provided, tools required by the deployment are
        marked as required; missing required tools are a failure.

    ``config``
        Profile file references resolve to files on disk.

    ``auth``
        Authentication and backend reachability.
        Skipped unless ``--deep`` is supplied to avoid slow network calls.
        Pass ``--login`` to actively drive a login for integrations that support it
        (ADR-0067), instead of only printing a fix hint.

    Exit code ``0`` when all checks pass (warnings are allowed).
    Exit code ``3`` when one or more checks fail.
    """

    OPERATION = "sln_doctor"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        category: Optional[str] = None,
        deep: bool = False,
        login: bool = False,
        ai: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._file = file
        self._category = category
        self._deep = deep
        self._login = login
        self._ai = ai
        self._has_check_failures = False

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def has_validation_errors(self) -> bool:
        """Return True when one or more health checks failed (triggers exit code 3)."""
        return self._has_check_failures

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — run super for side-effects only.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        # Validate --category filter
        categories: tuple[str, ...] = _CATEGORIES
        if self._category:
            if self._category not in _CATEGORIES:
                self._errors.append(f"Unknown category '{self._category}'. Available: {', '.join(_CATEGORIES)}")
                return False
            categories = (self._category,)

        # Run each category
        results: Dict[str, List[CheckResult]] = {}
        for cat in categories:
            results[cat] = self._run_category(cat)

        # Count outcomes
        all_checks = [c for checks in results.values() for c in checks]
        passed = sum(1 for c in all_checks if c.status == "pass")
        warned = sum(1 for c in all_checks if c.status == "warn")
        failed = sum(1 for c in all_checks if c.status == "fail")

        # Console rendering
        if self._is_console_output():
            click.echo("\n  🩺  Doctor — workspace health check\n")
            for cat, checks in results.items():
                self._print_category(cat, checks)

            click.echo("  " + "─" * 48)
            parts: List[str] = []
            if passed:
                parts.append(f"{passed} passed")
            if warned:
                parts.append(f"{warned} warning{'s' if warned > 1 else ''}")
            if failed:
                parts.append(f"{failed} failed")
            click.echo(f"  Result: {' │ '.join(parts) if parts else 'nothing checked'}\n")

        # Structured output data
        self._output_data = {
            "summary": {"passed": passed, "warnings": warned, "failed": failed},
            "categories": [
                {
                    "name": cat,
                    "checks": [
                        {
                            "name": c.name,
                            "status": c.status,
                            "value": c.value,
                            "fix_hint": c.fix_hint,
                        }
                        for c in checks
                    ],
                }
                for cat, checks in results.items()
            ],
        }

        self._has_check_failures = failed > 0

        if self._ai and failed > 0:
            self._run_ai_doctor_analysis()

        return failed == 0

    def _run_ai_doctor_analysis(self) -> None:
        """Run AI explanation of failed doctor checks."""
        from strata.integrations.ai import find_ai_integration

        # Build a flat list of failed checks with category context
        failed_checks: List[Dict[str, Any]] = []
        for cat_entry in self._output_data.get("categories") or []:
            cat_name = cat_entry.get("name", "?")
            for check in cat_entry.get("checks", []):
                if check.get("status") == "fail":
                    failed_checks.append({"category": cat_name, **check})

        if not failed_checks:
            return

        # Use workspace-level configuration (no deployment file required)
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

        context = {"workspace": str(self._work_path)}

        if self._is_console_output():
            click.echo(f"\n  \U0001f916  AI doctor analysis ({integration.integration_name}) \u2026")

        try:
            response = integration.explain_doctor_results(failed_checks, context)
        except Exception as exc:
            self._messages.append(f"AI doctor analysis failed: {exc}")
            return

        self._output_data["ai_analysis"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
        }

        if self._is_console_output():
            self._print_ai_doctor(response.content)

    def _print_ai_doctor(self, content: str) -> None:
        import json as _json

        sep = "\u2500" * 48
        click.echo(f"\n  {sep}")
        click.echo("  \U0001f916  AI Doctor Analysis")
        click.echo(f"  {sep}")
        try:
            parsed = _json.loads(content)
            click.echo(f"\n  {parsed.get('summary', '')}")
            if parsed.get("root_cause"):
                click.echo(f"  Root cause: {parsed['root_cause']}")
            if parsed.get("remediation"):
                click.echo("  Remediation:")
                for item in parsed["remediation"]:
                    if isinstance(item, dict):
                        click.echo(f"    [{item.get('check', '?')}]")
                        for i, step in enumerate(item.get("steps", []), 1):
                            click.echo(f"      {i}. {step}")
                    else:
                        click.echo(f"    \u2192 {item}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo("")

    # ------------------------------------------------------------------
    # Category dispatcher
    # ------------------------------------------------------------------

    def _run_category(self, category: str) -> List[CheckResult]:
        runner = {
            "runtime": self._check_runtime,
            "workspace": self._check_workspace,
            "tools": self._check_tools,
            "config": self._check_config,
            "auth": self._check_auth,
        }[category]
        try:
            return runner()
        except Exception as exc:
            self.logger.warning("Doctor category check raised", category=category, error=str(exc))
            return [
                CheckResult(
                    name=f"{category}_error",
                    status="fail",
                    fix_hint=f"Internal error running '{category}' checks: {exc}",
                )
            ]

    # ------------------------------------------------------------------
    # Category: runtime
    # ------------------------------------------------------------------

    def _check_runtime(self) -> List[CheckResult]:
        results: List[CheckResult] = []

        # Python version
        major, minor, micro = sys.version_info[:3]
        version_str = f"{major}.{minor}.{micro}"
        if (major, minor) >= (3, 13):
            results.append(
                CheckResult(
                    "python_version",
                    "pass",
                    value=f"Python {version_str}",
                )
            )
        else:
            results.append(
                CheckResult(
                    "python_version",
                    "fail",
                    value=f"Python {version_str}",
                    fix_hint="strata requires Python 3.13 or later. Upgrade your Python installation.",
                )
            )

        # strata version
        results.append(
            CheckResult(
                "strata_version",
                "pass",
                value=f"strata {get_version()}",
            )
        )

        return results

    # ------------------------------------------------------------------
    # Category: workspace
    # ------------------------------------------------------------------

    def _check_workspace(self) -> List[CheckResult]:
        results: List[CheckResult] = []

        # .strata/ directory
        strata_dir = self._work_path / ".strata"
        if not strata_dir.is_dir():
            results.append(
                CheckResult(
                    "strata_dir",
                    "fail",
                    value=str(strata_dir),
                    fix_hint="Run 'strata sln init' to initialize a workspace.",
                )
            )
            return results  # nothing more to check

        results.append(CheckResult("strata_dir", "pass", value=str(strata_dir)))

        # Use GuideController for the remaining workspace phases
        guide = GuideController(self._work_path)
        guide.load()
        checklist = guide.evaluate()

        for item in checklist:
            if item.phase > 6:
                break  # skip build / SBOM phases — not diagnostics
            check_name = _PHASE_CHECK_NAME.get(item.phase, f"phase_{item.phase}")
            if item.status == "ok":
                results.append(CheckResult(check_name, "pass", value=item.label))
            elif item.status == "warn":
                results.append(
                    CheckResult(
                        check_name,
                        "warn",
                        value=item.label,
                        fix_hint=item.detail,
                    )
                )
            else:  # pending
                results.append(
                    CheckResult(
                        check_name,
                        "fail",
                        value=item.label,
                        fix_hint=item.detail or "Run 'strata guide' for step-by-step instructions.",
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Category: tools
    # ------------------------------------------------------------------

    def _check_tools(self) -> List[CheckResult]:
        results: List[CheckResult] = []
        tc = ToolsController()
        _, rows, errors = tc.status(
            deployment_file=self._file,
            work_path=str(self._work_path),
        )
        for err in errors:
            self._messages.append(err)

        for row in rows:
            name: str = row["name"]
            available: bool = row["available"]
            version: Optional[str] = row.get("version")
            requirement: Optional[str] = row.get("requirement")  # "required" | "optional" | None

            value_str = f"{name} {version}" if (available and version) else name

            if self._file:
                # Deployment file provided — only show referenced tools
                if requirement is None:
                    continue  # not referenced, omit
                if available:
                    results.append(CheckResult(f"tool_{name}", "pass", value=value_str))
                elif requirement == "required":
                    results.append(
                        CheckResult(
                            f"tool_{name}",
                            "fail",
                            value=f"{name} not found",
                            fix_hint=f"'{name}' is required by this deployment. Install it and ensure it is on PATH.",
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            f"tool_{name}",
                            "warn",
                            value=f"{name} not found",
                            fix_hint=f"'{name}' is optional. Some features may be unavailable.",
                        )
                    )
            else:
                # No deployment file — only report available tools by default;
                # show unavailable ones in verbose mode (unknown relevance).
                if available:
                    results.append(CheckResult(f"tool_{name}", "pass", value=value_str))
                elif self._is_verbose():
                    results.append(
                        CheckResult(
                            f"tool_{name}",
                            "warn",
                            value=f"{name} not found",
                            fix_hint=f"Provide -f FILE to check if '{name}' is required by your deployment.",
                        )
                    )

        return results

    # ------------------------------------------------------------------
    # Category: config
    # ------------------------------------------------------------------

    def _check_config(self) -> List[CheckResult]:
        results: List[CheckResult] = []
        solution = self._solution_controller.solution

        if solution is None:
            results.append(
                CheckResult(
                    "config_profile_files",
                    "skip",
                    fix_hint="Workspace not initialized — run 'strata sln init' first.",
                )
            )
            return results

        active_profile, _ = self._solution_controller.get_active_profile()
        if active_profile is None:
            results.append(
                CheckResult(
                    "config_profile_files",
                    "skip",
                    fix_hint="No active profile. Run 'strata profile activate <name>' first.",
                )
            )
            return results

        # Gather all declared file reference paths
        ref_groups: Dict[str, Any] = {
            "configfile": active_profile.configfile_paths or [],
            "envfile": active_profile.envfile_paths or [],
            "secretfile": active_profile.secretfile_paths or [],
            "datafile": active_profile.datafile_paths or [],
        }

        has_any = any(refs for refs in ref_groups.values())
        if not has_any:
            results.append(
                CheckResult(
                    "config_profile_files",
                    "warn",
                    value="No file references registered",
                    fix_hint="Use 'strata ref env' / 'strata ref config' to register profile files.",
                )
            )
            return results

        for kind, refs in ref_groups.items():
            for ref in refs:
                raw_path = Path(ref.path)
                resolved = raw_path if raw_path.is_absolute() else self._work_path / raw_path
                if resolved.exists():
                    results.append(
                        CheckResult(
                            f"config_{kind}",
                            "pass",
                            value=str(resolved),
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            f"config_{kind}",
                            "fail",
                            value=str(resolved),
                            fix_hint=f"File not found. Check your profile's {kind} configuration.",
                        )
                    )

        return results

    # ------------------------------------------------------------------
    # Category: auth
    # ------------------------------------------------------------------

    def _check_auth(self) -> List[CheckResult]:
        if not self._deep:
            return [
                CheckResult(
                    "auth",
                    "skip",
                    value="Skipped (use --deep to run auth checks)",
                )
            ]

        results: List[CheckResult] = []
        from strata.integrations.factory import IntegrationFactory

        for type_str in IntegrationFactory.get_known_types():
            try:
                integration = IntegrationFactory.create_by_type(type_str)
                if not integration.is_available():
                    continue  # not installed — tools category already reported
                if not hasattr(integration, "check_auth"):
                    continue
                ok, detail = integration.check_auth()
                if not ok and self._login and hasattr(integration, "login"):
                    ok, detail = integration.login()
                results.append(
                    CheckResult(
                        f"auth_{type_str}",
                        "pass" if ok else "fail",
                        value=detail,
                        fix_hint=None if ok else f"Re-authenticate: run the login command for '{type_str}'.",
                    )
                )
            except Exception:
                pass  # best-effort

        # Identity-provider integrations need real configuration (issuer, client_id) to
        # check — unlike az/aws/gcloud above, a bare default IntegrationModel can't reach
        # anything, so these are resolved from the workspace's actual spec.integrations.
        results.extend(self._check_identity_integrations())

        if not results:
            results.append(
                CheckResult(
                    "auth",
                    "skip",
                    value="No integrations support auth checks",
                )
            )

        return results

    def _check_identity_integrations(self) -> List[CheckResult]:
        """Check auth for `identity`-capable integrations actually configured in this workspace.

        Unlike the bare-type loop above (which needs no config to check az/aws/gcloud
        login state), an identity-provider integration requires real config — issuer,
        client_id — from ``spec.integrations``, so it is resolved from the loaded
        workspace configuration rather than instantiated with a default IntegrationModel.
        Best-effort: skipped entirely if no workspace/profile/config can be loaded.
        """
        results: List[CheckResult] = []
        try:
            from strata.controllers.solution_controller import SolutionController
            from strata.integrations.capabilities import IIdentityProvider
            from strata.services.configuration_service import ConfigurationService
            from strata.services.integration_service import IntegrationService

            sol = SolutionController(work_path=self._work_path)
            sol.load()
            profile, _ = sol.get_active_profile()
            if not profile:
                return results

            for cp in [str(p.path) for p in (profile.configfile_paths or [])]:
                ConfigurationService.load(cp)

            svc = IntegrationService.get_instance()
            if not svc.is_initialized():
                svc.initialize_integrations()

            for name in svc.get_integrations_with_capability(IIdentityProvider):
                integration = svc.get_integration(name)
                if integration is None:
                    continue
                ok, detail = integration.check_auth()
                if not ok and self._login and hasattr(integration, "login"):
                    ok, detail = integration.login()
                results.append(
                    CheckResult(
                        f"auth_identity_{name}",
                        "pass" if ok else "fail",
                        value=detail,
                        fix_hint=None if ok else "Run 'strata sln doctor --deep --login' to sign in.",
                    )
                )
        except Exception as exc:
            self.logger.debug("doctor_identity_check_skipped", error=str(exc))
        return results

    # ------------------------------------------------------------------
    # Console rendering
    # ------------------------------------------------------------------

    def _print_category(self, category: str, checks: List[CheckResult]) -> None:
        click.echo(f"  {category.capitalize()}")
        for check in checks:
            icon = _STATUS_ICON.get(check.status, "   ")
            label = check.value or check.name.replace("_", " ")
            click.echo(f"    {icon}  {label}")
            if check.fix_hint and check.status in ("fail", "warn"):
                click.echo(f"         → {check.fix_hint}")
            elif check.fix_hint and self._is_verbose():
                click.echo(f"         → {check.fix_hint}")
        click.echo("")
