#!/usr/bin/env python3
"""Command to evaluate policies against a deployment outside the deploy pipeline."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from strata.commands.base_command import BaseCommand
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService

# Phases the policy engine understands, in evaluation order
_ALL_PHASES = ("validate", "build", "plan", "deploy")


class CheckPolicyCommand(BaseCommand):
    """Evaluate declared policies against a deployment without running a deploy.

    Loads the active configuration and the given deployment file, builds a
    ``PolicyContext`` for each requested phase, evaluates all matching enabled
    policies, and reports the results.

    Context that is not available (e.g. no ``platform.json`` for the build
    phase, no plan file for the plan phase) is reported explicitly so the
    operator knows what command to run to unlock those checks.

    Exit codes:
      0 — all policies passed (or no policies declared)
      3 — one or more ``deny``-enforcement policies failed
    """

    OPERATION = "policy_check"

    def __init__(
        self,
        file: Optional[str] = None,
        phase: Optional[Tuple[str, ...]] = None,
        plan_file: Optional[str] = None,
        ai: bool = False,
        no_cache_warm: bool = False,
        work_path: Optional[str] = None,
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
        self._raw_file: Optional[str] = file
        self._file_path: Optional[Path] = Path(file) if file else None
        self._requested_phases: Tuple[str, ...] = phase if phase else _ALL_PHASES
        self._raw_plan_file: Optional[str] = plan_file
        self._ai: bool = ai
        self._no_cache_warm: bool = no_cache_warm

        self._configuration_service: Optional[ConfigurationService] = None
        self._deployment_service: Optional[DeploymentService] = None

        # Populated during _run_execution
        self._results: List[Dict[str, Any]] = []
        self._notes: List[Dict[str, str]] = []
        self._denied: bool = False
        # True when the deployment file itself failed schema validation (as opposed
        # to a deny-enforcement policy denial, which also maps to exit 3 via _denied).
        self._validation_failed: bool = False

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def has_validation_errors(self) -> bool:
        """True for exit code 3: either the deployment file failed validation, or
        one or more deny-enforcement policies were violated (see class docstring).
        """
        return self._denied or self._validation_failed

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _execute(self) -> bool:
        if not self._run_execution():
            if self._is_console_output():
                click.echo("\n❌  Execution failed")
            return False
        if self._ai and any(not r["passed"] for r in self._results):
            self._run_ai_policy_review()
        return not self._denied

    def _run_execution(self) -> bool:
        from strata.validators.policies.base_policy import PolicyContext
        from strata.validators.policies.policy_engine import PolicyEngine

        # --- Load services ---
        self._configuration_service = self._load_configuration_service()
        if self._configuration_service is None:
            return False

        if not self._raw_file:
            self._errors.append("A deployment file is required. Use --file <deploy.yaml>.")
            return False

        if not self._load_deployment_service():
            return False

        # --- Collect all declared policies ---
        spec = self._configuration_service.model.spec if self._configuration_service.model else None
        all_policy_models = list(getattr(spec, "policies", None) or [])
        enabled_policies = [p for p in all_policy_models if p.enabled and p.phase in self._requested_phases]

        if not enabled_policies:
            self._output_data = self._build_output_data([])
            return True

        # --- Resolve phase-specific context artifacts ---
        build_path = self._work_path / "build"
        platform_artifact, platform_note = self._load_platform_artifact(build_path)
        plan_data, plan_note = self._load_plan_data(build_path)

        if platform_note:
            self._notes.append({"phase": "build", "message": platform_note})
        if plan_note:
            self._notes.append({"phase": "plan", "message": plan_note})

        # ADR-0026: opportunistically warm the resolved-model cache with the
        # platform.json we just read off disk. Cheap (no re-resolution — the file
        # was already loaded above) and best-effort; never fails the policy check.
        if platform_artifact is not None and not self._no_cache_warm:
            self._warm_model_cache(platform_artifact)

        # --- Evaluate per phase ---
        for phase in _ALL_PHASES:
            if phase not in self._requested_phases:
                continue

            phase_policies = [p for p in enabled_policies if p.phase == phase]
            if not phase_policies:
                continue

            context = PolicyContext(
                phase=phase,
                work_path=self._work_path,
                deployment_service=self._deployment_service,
                configuration_service=self._configuration_service,
                platform_artifact=platform_artifact if phase in ("build", "plan", "deploy") else None,
                plan_data=plan_data if phase == "plan" else None,
                build_path=build_path,
            )

            engine = PolicyEngine(phase_policies)
            phase_results = engine.evaluate(phase, context)

            for policy_model, result in zip(phase_policies, phase_results, strict=False):
                entry: Dict[str, Any] = {
                    "policy": result.policy_name,
                    "type": policy_model.type,
                    "phase": phase,
                    "enforcement": result.enforcement,
                    "passed": result.passed,
                    "violations": result.violations or [],
                }
                self._results.append(entry)

                if not result.passed:
                    self._forward_policy_violation_audit_event(result)
                    if result.enforcement == "deny":
                        self._denied = True

        self._output_data = self._build_output_data(self._results)
        return True

    def _after_execute(self) -> bool:
        if self._is_console_output():
            self._render_console()
        return super()._after_execute()

    # ------------------------------------------------------------------
    # Artifact loaders
    # ------------------------------------------------------------------

    def _warm_model_cache(self, platform_artifact: Any) -> None:
        """Best-effort cache warm (ADR-0026) using the platform.json already read from disk."""
        from strata.controllers.cache_controller import warm_platform_model_best_effort

        warm_platform_model_best_effort(self._work_path, self._deployment_service, platform_artifact, self.logger)

    def _load_platform_artifact(self, build_path: Path) -> Tuple[Optional[Any], Optional[str]]:
        """Try to load platform.json from the deployment build directory.

        Returns ``(artifact, None)`` on success, ``(None, note_message)`` when
        the file is absent — the note tells the operator what to run.
        """
        if self._deployment_service is None:
            return None, None

        platform_path = self._deployment_service.get_build_path(build_path) / "platform.json"
        if not platform_path.exists():
            return (
                None,
                (
                    f"No platform artifact found at {platform_path}. "
                    "Build-phase policies were skipped. "
                    "Run `strata build run --file <deploy.yaml>` to generate it."
                ),
            )

        try:
            from strata.models.platform_artifact_model import PlatformArtifactModel

            with open(platform_path, encoding="utf-8") as fh:
                data = json.load(fh)
            artifact = PlatformArtifactModel.model_validate(data)
            return artifact, None
        except Exception as exc:
            return (
                None,
                f"Could not load platform.json ({exc}). Build-phase policies were skipped.",
            )

    def _load_plan_data(self, build_path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Try to load plan JSON from ``--plan-file`` or auto-discover in the build dir.

        Returns ``(plan_data_dict, None)`` on success, ``(None, note_message)``
        when no plan file is available.
        """
        # 1. Explicit --plan-file
        if self._raw_plan_file:
            plan_path = Path(self._raw_plan_file)
            if not plan_path.is_absolute():
                plan_path = self._work_path / self._raw_plan_file
            if not plan_path.exists():
                return None, f"Plan file not found: {plan_path}. Plan-phase policies were skipped."
            try:
                with open(plan_path, encoding="utf-8") as fh:
                    return json.load(fh), None
            except Exception as exc:
                return None, f"Could not read plan file ({exc}). Plan-phase policies were skipped."

        # 2. Auto-discover: build/{name}-{v}/{stage}.tfplan.json
        if self._deployment_service is not None:
            deployment_build = self._deployment_service.get_build_path(build_path)
            candidates = sorted(deployment_build.glob("*.tfplan.json")) if deployment_build.exists() else []
            if candidates:
                try:
                    with open(candidates[0], encoding="utf-8") as fh:
                        return json.load(fh), None
                except Exception as exc:
                    return None, f"Could not read auto-discovered plan file ({exc}). Plan-phase policies were skipped."

        return (
            None,
            (
                "No plan file found. Plan-phase policies were skipped. "
                "Run `strata deploy run --dry-run --file <deploy.yaml>` to generate one, "
                "or pass `--plan-file <path>` explicitly."
            ),
        )

    # ------------------------------------------------------------------
    # Console rendering
    # ------------------------------------------------------------------

    def _render_console(self) -> None:
        deploy_label = str(self._file_path) if self._file_path else "(unknown)"
        phases_label = ", ".join(self._requested_phases)
        click.echo(f"\n🔍  Policy check  —  {deploy_label}  ·  phases: {phases_label}")
        click.echo("─" * 70)

        # Notes about missing context
        for note in self._notes:
            click.echo(f"  ℹ  [{note['phase']}] {note['message']}")
        if self._notes:
            click.echo("")

        if not self._results:
            click.echo("  (no matching policies to evaluate)\n")
            return

        # Column header
        click.echo(f"  {'Phase':<10}{'Policy':<26}{'Enforcement':<14}Result")
        click.echo(f"  {'─' * 9} {'─' * 24} {'─' * 12} {'─' * 20}")

        for entry in self._results:
            phase_str = entry["phase"][:9]
            name_str = entry["policy"][:24]
            enf = entry["enforcement"]

            if enf == "deny":
                enf_col = click.style(f"{enf:<14}", fg="red")
            elif enf == "warn":
                enf_col = click.style(f"{enf:<14}", fg="yellow")
            else:
                enf_col = click.style(f"{enf:<14}", fg="bright_black")

            if entry["passed"]:
                result_col = click.style("✓  passed", fg="green")
                click.echo(f"  {phase_str:<10}{name_str:<26}{enf_col}{result_col}")
            else:
                if enf == "deny":
                    result_col = click.style("✗  DENIED", fg="red", bold=True)
                elif enf == "warn":
                    result_col = click.style("⚠  warning", fg="yellow")
                else:
                    result_col = click.style("·  audit", fg="bright_black")
                click.echo(f"  {phase_str:<10}{name_str:<26}{enf_col}{result_col}")
                for v in entry["violations"]:
                    click.echo(f"      {click.style('↳', fg='red')} {v}")

        click.echo("")
        passed = sum(1 for r in self._results if r["passed"])
        total = len(self._results)
        denied = sum(1 for r in self._results if not r["passed"] and r["enforcement"] == "deny")
        warned = sum(1 for r in self._results if not r["passed"] and r["enforcement"] == "warn")
        audited = sum(1 for r in self._results if not r["passed"] and r["enforcement"] == "audit")

        summary_parts = [f"{passed}/{total} passed"]
        if denied:
            summary_parts.append(click.style(f"{denied} denied", fg="red", bold=True))
        if warned:
            summary_parts.append(click.style(f"{warned} warning(s)", fg="yellow"))
        if audited:
            summary_parts.append(f"{audited} audit finding(s)")

        click.echo("  " + "  ·  ".join(summary_parts))
        if self._denied:
            click.echo(click.style("\n  ✗  One or more deny policies failed.", fg="red", bold=True))
        else:
            click.echo(click.style("\n  ✓  All checks passed.", fg="green"))
        click.echo("")

    # ------------------------------------------------------------------
    # AI policy review
    # ------------------------------------------------------------------

    def _run_ai_policy_review(self) -> None:
        """Run AI explanation of failed policies after evaluation."""
        from strata.integrations.ai import find_ai_integration

        # configuration_service is already loaded by _run_execution
        integration = find_ai_integration(self._configuration_service)
        if integration is None:
            if self._is_console_output():
                click.echo("  \u26a0  --ai flag set but no ai_agent integration configured")
            return
        ok, msg = integration.ensure_available()
        if not ok:
            self._messages.append(f"AI provider unavailable: {msg}")
            return

        # Build violations list — include phase context for richer AI input
        violations = [
            {
                "policy": r["policy"],
                "phase": r["phase"],
                "enforcement": r["enforcement"],
                "violations": r["violations"],
            }
            for r in self._results
            if not r["passed"]
        ]

        context = {
            "deployment": str(self._file_path or self._raw_file or "unknown"),
            "work_path": str(self._work_path),
        }

        if self._is_console_output():
            click.echo(f"\n  \U0001f916  AI policy review ({integration.integration_name}) \u2026")

        try:
            response = integration.review_policy_violations(violations, context)
        except Exception as exc:
            self._messages.append(f"AI policy review failed: {exc}")
            return

        self._output_data["ai_analysis"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
        }

        if self._is_console_output():
            self._print_ai_policy_review(response.content)

    def _print_ai_policy_review(self, content: str) -> None:
        import json as _json

        sep = "\u2500" * 48
        click.echo(f"\n  {sep}")
        click.echo("  \U0001f916  AI Policy Review")
        click.echo(f"  {sep}")
        try:
            parsed = _json.loads(content)
            severity = str(parsed.get("severity", "?")).upper()
            severity_icon = {
                "LOW": "\U0001f7e2",
                "MEDIUM": "\U0001f7e1",
                "HIGH": "\U0001f7e0",
                "CRITICAL": "\U0001f534",
            }.get(severity, "\u26aa")
            click.echo(f"\n  {severity_icon}  {parsed.get('summary', '')}")
            if parsed.get("violations"):
                click.echo("\n  Violations:")
                for v in parsed["violations"]:
                    if isinstance(v, dict):
                        policy = v.get("policy", "?")
                        desc = v.get("description", "")
                        fix = v.get("fix", "")
                        click.echo(f"    \u2022 [{policy}] {desc}")
                        if fix:
                            click.echo(f"      \u2192 Fix: {fix}")
                    else:
                        click.echo(f"    \u2022 {v}")
            if parsed.get("recommendations"):
                click.echo("\n  Recommendations:")
                for r in parsed["recommendations"]:
                    click.echo(f"    \u2192 {r}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo("")

    # ------------------------------------------------------------------
    # Output data
    # ------------------------------------------------------------------

    def _build_output_data(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        passed = sum(1 for r in results if r["passed"])
        denied = sum(1 for r in results if not r["passed"] and r["enforcement"] == "deny")
        return {
            "deployment": str(self._file_path) if self._file_path else None,
            "phases": list(self._requested_phases),
            "policies_checked": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "denied": denied,
            "notes": self._notes,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Service loaders (same pattern as ListPolicyCommand)
    # ------------------------------------------------------------------

    def _load_configuration_service(self) -> Optional[ConfigurationService]:
        from strata.utils.system import resolve_path

        if self._solution_controller.solution is None:
            self._errors.append("policy check requires an initialized workspace. Run `strata sln init` first.")
            return None

        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            self._errors.append("No active profile. Run `strata profile activate <name>` first.")
            return None

        configfile_paths = profile.configfile_paths or []
        if not configfile_paths:
            self._errors.append(
                "policy check requires at least one configfile path on the active profile. "
                "Add one with `strata ref configfile add`."
            )
            return None

        repo_map = self._solution_controller.get_repo_map()
        resolved_paths = []
        for entry in configfile_paths:
            try:
                resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
            except ValueError as exc:
                self.logger.debug("Config source skipped", name=str(entry.name), reason=str(exc))
                continue
            if not resolved.exists():
                self.logger.debug("Config source not found", name=str(entry.name), path=str(resolved))
                continue
            resolved_paths.append(str(resolved))

        if not resolved_paths:
            self._errors.append("No configfile_paths resolved to existing files. Check your profile refs.")
            return None

        try:
            ConfigurationService.reset()
            config_svc = ConfigurationService.get_instance()
            success, load_errors = config_svc.load_from_paths(resolved_paths)
            if not success:
                self._errors.append(f"Failed to load configuration: {'; '.join(load_errors)}")
                return None
            return config_svc
        except Exception as exc:
            self._errors.append(f"Failed to load configuration service: {exc}")
            return None

    def _load_deployment_service(self) -> bool:
        from strata.utils.system import resolve_path

        assert self._raw_file is not None

        repo_map = self._solution_controller.get_repo_map() if self._solution_controller else {}
        try:
            resolved = resolve_path(str(self._work_path), self._raw_file, repo_map=repo_map)
        except ValueError as exc:
            raise click.UsageError(f"Deployment file reference error: {exc}") from exc

        if not resolved.exists():
            raise click.UsageError(f"Deployment file not found: {resolved}")

        self._file_path = resolved
        try:
            svc = DeploymentService.load(str(self._file_path), validate=True)
            if not svc.is_validated():
                self._errors.extend(svc.get_validation_errors())
                self._validation_failed = True
                return False
            self._deployment_service = svc
            return True
        except Exception as exc:
            self._errors.append(f"Failed to load deployment file: {exc}")
            return False
