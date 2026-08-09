"""Command to validate a single platform YAML file or run cross-manifest overlap checks."""

import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import yaml

from strata.commands.base_command import BaseCommand
from strata.controllers.lifecycle_controller import LifecycleController
from strata.validators.platform_validator import PlatformValidator


class ValidateCommand(BaseCommand):
    """Validate a platform YAML file, or run cross-manifest overlap checks.

    Single-file mode (``--file``): works both inside and outside an initialized
    workspace.  ``--deep`` enables Phase 2 cross-reference validation.

    Overlap mode (``--path``): requires an initialized workspace with an active
    profile.  Discovers all deployment manifests matching the glob, runs
    per-file validation, then checks for cross-manifest overlaps.
    """

    OPERATION = "validate"

    def __init__(
        self,
        file: Optional[str] = None,
        path: Optional[str] = None,
        deep: bool = False,
        verify_digests: bool = False,
        explain: bool = False,
        ai: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file_path_raw: Optional[str] = file
        self._path_glob: Optional[str] = path
        self._deep: bool = deep
        self._verify_digests: bool = verify_digests
        self._explain: bool = explain
        self._ai: bool = ai
        self._resolved_file: Optional[Path] = None
        self._validator: Optional[PlatformValidator] = None
        self._detected_kind: Optional[str] = None
        self._policy_configuration_service: Optional[Any] = None
        self._validate_policy_denied: bool = False
        self._overlap_manifest_paths: List[Path] = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def has_validation_errors(self) -> bool:
        """Return True when the validator or policy engine found errors."""
        validator_errors = self._validator.has_errors() if self._validator else False
        return validator_errors or self._validate_policy_denied

    # ------------------------------------------------------------------
    # Lifecycle overrides
    # ------------------------------------------------------------------

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        if not self._run_execution():
            if self._is_console_output():
                click.echo("\n❌  Execution failed")
            return False
        # Evaluate validate-phase policies — failures are validation errors
        # (exit code 3), not system failures (exit code 1).
        self._evaluate_validate_policies()
        if self._ai:
            self._run_ai_validation_review()
        return True

    def _before_execute(self) -> bool:
        """Resolve file/path and verify preconditions."""
        if not super()._before_execute():
            return False

        if self._path_glob:
            return self._before_execute_overlap()

        # Single-file mode
        if not self._file_path_raw:
            raise click.UsageError("Missing option '-f' / '--file'. Specify the deployment YAML file path.")

        if self._file_path_raw.startswith("@"):
            try:
                from strata.utils.system import resolve_path

                repo_map = self._solution_controller.get_repo_map()
                candidate = resolve_path(str(self._work_path), self._file_path_raw, repo_map=repo_map)
            except Exception as e:
                raise click.UsageError(f"Cannot resolve '{self._file_path_raw}': {e}") from e
        else:
            candidate = Path(self._file_path_raw)
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            candidate = candidate.resolve()

        if not candidate.exists():
            raise click.UsageError(f"File not found: {candidate}")

        self._resolved_file = candidate
        self.logger.debug("Resolved file path", path=str(self._resolved_file))
        return True

    def _before_execute_overlap(self) -> bool:
        """Validate preconditions and resolve manifest paths for --path mode."""
        if self._solution_controller.solution is None:
            raise click.UsageError(
                "Overlap check requires an initialized workspace with an active profile. Run `strata sln init` first."
            )
        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            raise click.UsageError("No active profile. Run `strata profile activate <name>` first.")

        glob_pattern = self._path_glob
        repo_map = self._solution_controller.get_repo_map()
        configfile_paths = profile.configfile_paths or []

        from strata.utils.system import resolve_path

        seen: set = set()
        matched: List[Path] = []
        for entry in configfile_paths:
            try:
                resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
            except Exception:
                continue
            if not resolved.exists():
                continue
            # Match relative-to-work_path against the glob
            try:
                rel = resolved.relative_to(self._work_path)
            except ValueError:
                rel = resolved
            if not fnmatch.fnmatch(str(rel).replace("\\", "/"), glob_pattern or "**"):
                continue
            canonical = str(resolved.resolve())
            if canonical in seen:
                continue
            seen.add(canonical)
            try:
                raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
                if raw.get("kind") == "deployment":
                    matched.append(resolved)
            except Exception:
                continue

        if not matched:
            raise click.UsageError(f"No deployment manifests found matching '{glob_pattern}' in the active profile.")

        self._overlap_manifest_paths = matched
        self.logger.debug("Overlap manifests found", count=len(matched), glob=glob_pattern)
        return True

    def _run_execution(self) -> bool:
        """Run validation — single-file or overlap mode."""
        if self._path_glob:
            return self._run_overlap_execution()
        return self._run_single_file_execution()

    def _run_single_file_execution(self) -> bool:
        """Run the validator pipeline on a single file and collect errors."""
        config_svc = self._load_configuration_service()
        # _load_configuration_service appends to self._errors on hard failure
        if self._deep and config_svc is None:
            return False  # exit code 1 — system error, not validation error
        self._policy_configuration_service = config_svc

        assert self._resolved_file is not None  # guaranteed by _before_execute
        solution_repo_map = self._solution_controller.get_repo_map()
        self._validator = PlatformValidator(
            file_path=self._resolved_file,
            configuration_service=config_svc,
            repo_map=solution_repo_map,
            verify_digests=self._verify_digests,
            lifecycle_controller=LifecycleController(),
        )

        work_path = self._work_path

        # Run phases in sequence; stop on first failure
        phases = [
            ("before_validate", self._validator.before_validate),
            ("validate", self._validator.validate),
            ("after_validate", self._validator.after_validate),
        ]
        all_phases_passed = True
        for phase_name, phase_fn in phases:
            self.logger.debug("Running validator phase", phase=phase_name, file=str(self._resolved_file))
            result = phase_fn(work_path)
            if not result:
                self.logger.warning(
                    "Validator phase returned False",
                    phase=phase_name,
                    file=str(self._resolved_file),
                )
                all_phases_passed = False
                break

        self._detected_kind = self._validator.detected_kind.value if self._validator.detected_kind else None
        validation_passed = all_phases_passed and not self._validator.has_errors()

        self.logger.debug(
            "Validation complete",
            file=str(self._resolved_file),
            kind=self._detected_kind,
            deep=self._deep,
            validation_passed=validation_passed,
            error_count=len(self._validator.get_errors()),
        )

        self._output_data = {
            "file": str(self._resolved_file),
            "kind": self._detected_kind,
            "deep": self._deep,
            "validation_passed": validation_passed,
            "errors": [e.to_dict() for e in self._validator.get_structured_errors()],
        }

        # Include non-fatal warnings (e.g. shadowed overrides)
        if self._validator.has_warnings():
            self._output_data["warnings"] = self._validator.get_warnings()
            if self._is_console_output():
                for w in self._validator.get_warnings():
                    click.echo(f"  ⚠️   {w}")

        # Add explanation and suggestions to output data
        if validation_passed and self._explain:
            explanation = self._generate_explanation()
            if explanation:
                self._output_data["explanation"] = explanation
        if not validation_passed:
            suggestions = self._generate_fix_suggestions()
            if suggestions:
                self._output_data["suggestions"] = suggestions

        return True

    def _run_overlap_execution(self) -> bool:
        """Run cross-manifest overlap checks via OverlapController."""
        from strata.controllers.overlap_controller import OverlapController

        config_svc = self._load_configuration_service_for_overlap()
        if config_svc is None:
            return False

        repo_map = self._solution_controller.get_repo_map()
        controller = OverlapController(
            configuration_service=config_svc,
            repo_map=repo_map,
            work_path=self._work_path,
        )

        no_critical = controller.run(self._overlap_manifest_paths)
        errors = controller.get_overlap_errors()
        warnings = controller.get_overlap_warnings()

        # Critical overlaps are validation errors (exit code 3)
        for err in errors:
            self._errors.append(err.message)

        self._output_data = {
            "glob": self._path_glob,
            "manifests_checked": len(self._overlap_manifest_paths),
            "critical_overlaps": [e.to_dict() for e in errors],
            "warnings": [w.to_dict() for w in warnings],
            "overlap_passed": no_critical,
        }

        return True

    def _load_configuration_service_for_overlap(self):
        """Load ConfigurationService for --path overlap mode (always required)."""
        try:
            from strata.services.configuration_service import ConfigurationService
            from strata.utils.system import resolve_path

            profile, _ = self._solution_controller.get_active_profile()
            if profile is None:
                self._errors.append("No active profile. Run `strata profile activate <name>` first.")
                return None
            configfile_paths = profile.configfile_paths or []
            repo_map = self._solution_controller.get_repo_map()

            resolved_paths = []
            for entry in configfile_paths:
                try:
                    resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
                except ValueError:
                    continue
                if resolved.exists():
                    resolved_paths.append(str(resolved))

            if not resolved_paths:
                self._errors.append("--path: no configfile_paths resolved. Check active profile refs.")
                return None

            ConfigurationService.reset()
            config_svc = ConfigurationService.get_instance()
            success, load_errors = config_svc.load_from_paths(resolved_paths)
            if not success:
                self._errors.append(f"--path: failed to load configuration: {'; '.join(load_errors)}")
                return None
            return config_svc

        except Exception as exc:
            self._errors.append(f"--path: unexpected error loading configuration: {exc}")
            return None

    def _evaluate_validate_policies(self) -> bool:
        """Evaluate 'validate' phase policies. Returns False if any deny-enforcement policy fails."""
        from strata.validators.policies.base_policy import PolicyContext
        from strata.validators.policies.policy_engine import PolicyEngine

        if self._policy_configuration_service is None:
            return True

        spec = self._policy_configuration_service.model.spec if self._policy_configuration_service.model else None
        policy_models = getattr(spec, "policies", None) or []
        validate_policies = [p for p in policy_models if p.phase == "validate" and p.enabled]
        if not validate_policies:
            return True

        context = PolicyContext(
            phase="validate",
            work_path=self._work_path,
            configuration_service=self._policy_configuration_service,
        )

        engine = PolicyEngine(validate_policies)
        results = engine.evaluate("validate", context)

        denied = False
        for result in results:
            if result.passed:
                if self._is_verbose() and self._is_console_output():
                    click.echo(f"    \u2713  Policy '{result.policy_name}' passed")
            else:
                for v in result.violations:
                    if result.enforcement == "deny":
                        click.echo(f"    \u2717  Policy '{result.policy_name}' DENIED: {v}")
                        self._errors.append(f"Policy '{result.policy_name}': {v}")
                        denied = True
                    elif result.enforcement == "warn":
                        click.echo(f"    \u26a0  Policy '{result.policy_name}' warning: {v}")
                    elif result.enforcement == "audit" and self._is_verbose():
                        click.echo(f"    \u00b7  Policy '{result.policy_name}' audit: {v}")
                self._forward_policy_violation_audit_event(result)
        if denied:
            self._validate_policy_denied = True
        return not denied

    def _run_ai_validation_review(self) -> None:
        """Run AI policy review if there are schema errors or policy violations."""
        if not self.has_validation_errors():
            return

        from strata.integrations.ai import find_ai_integration

        integration = find_ai_integration(self._policy_configuration_service)
        if integration is None:
            if self._is_console_output():
                click.echo("  ⚠  --ai flag set but no ai_agent integration configured")
            return
        ok, msg = integration.ensure_available()
        if not ok:
            self._messages.append(f"AI provider unavailable: {msg}")
            return

        # Build violations list from validator errors + policy denials
        violations: list = []
        if self._validator:
            for err in self._validator.get_structured_errors():
                violations.append(err.to_dict())
        for err_msg in self._errors:
            if err_msg.startswith("Policy '"):
                violations.append({"message": err_msg, "code": "policy_violation"})

        if not violations:
            return

        context = {
            "deployment": str(self._resolved_file or self._file_path_raw or "unknown"),
            "work_path": str(self._work_path),
        }

        if self._is_console_output():
            click.echo(f"\n  🤖  AI validation review ({integration.integration_name}) …")

        try:
            response = integration.review_policy_violations(violations, context)
        except Exception as exc:
            self._messages.append(f"AI validation review failed: {exc}")
            return

        if "ai_analysis" not in self._output_data:
            self._output_data["ai_analysis"] = {}
        self._output_data["ai_analysis"]["validation_review"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
        }

        if self._is_console_output():
            self._print_ai_review(response.content)

    def _print_ai_review(self, content: str) -> None:
        import json as _json

        click.echo(f"\n  {'─' * 48}")
        click.echo("  🤖  AI Validation Review")
        click.echo(f"  {'─' * 48}")
        try:
            parsed = _json.loads(content)
            severity = str(parsed.get("severity", "?")).upper()
            severity_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(severity, "⚪")
            click.echo(f"\n  {severity_icon}  {parsed.get('summary', '')}")
            if parsed.get("violations"):
                click.echo("\n  Violations:")
                for v in parsed["violations"]:
                    if isinstance(v, dict):
                        click.echo(f"    • [{v.get('policy', '?')}] {v.get('description', '')}")
                        if v.get("fix"):
                            click.echo(f"      → Fix: {v['fix']}")
                    else:
                        click.echo(f"    • {v}")
            if parsed.get("recommendations"):
                click.echo("\n  Recommendations:")
                for r in parsed["recommendations"]:
                    click.echo(f"    → {r}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo("")

    def _after_execute(self) -> bool:
        """Emit human-readable console output."""
        if not super()._after_execute():
            return False

        if self._is_console_output():
            if self._path_glob:
                self._print_overlap_output()
            else:
                self._print_single_file_output()

        return True

    def _print_single_file_output(self) -> None:
        click.echo(f"\n📄  File  : {self._resolved_file}")
        click.echo(f"🏷️   Kind  : {self._detected_kind or 'unknown'}")
        click.echo(f"🔬  Deep  : {'yes' if self._deep else 'no'}")

        if self.has_validation_errors():
            click.echo("\n❌  Validation FAILED")
            click.echo("    Errors:")
            for err in self._validator.get_errors() if self._validator else []:
                click.secho(f"      - {err}", fg="red")
            # Show fix suggestions for Pydantic errors
            if self._validator:
                suggestions = self._generate_fix_suggestions()
                if suggestions:
                    click.echo("\n💡  Suggestions:")
                    for suggestion in suggestions:
                        click.secho(f"      → {suggestion}", fg="yellow")
        else:
            click.secho("\n✅  Validation PASSED", fg="green")
            if self._explain:
                explanation = self._generate_explanation()
                if explanation:
                    click.echo(f"\n📝  Summary: {explanation}")

        click.echo("")

    def _print_overlap_output(self) -> None:

        data = self._output_data or {}
        manifests_checked = data.get("manifests_checked", 0)
        critical: list = data.get("critical_overlaps", [])
        warnings: list = data.get("warnings", [])
        passed: bool = data.get("overlap_passed", True)

        click.echo(f"\n🔍  Overlap check  : {self._path_glob}")
        click.echo(f"📄  Manifests      : {manifests_checked}")

        if critical:
            click.echo("")
            for err in critical:
                click.secho(f"❌  OVERLAP (Check #{err['check']}): {err['message']}", fg="red")
                for f in err["files"]:
                    click.secho(f"      - {f}", fg="red")

        if warnings:
            click.echo("")
            for warn in warnings:
                click.secho(f"⚠️   WARNING (Check #{warn['check']}): {warn['message']}", fg="yellow")
                for f in warn["files"]:
                    click.secho(f"      - {f}", fg="yellow")

        click.echo("")
        if passed:
            click.secho("✅  No critical overlaps found", fg="green")
        else:
            click.secho("❌  Overlap check FAILED", fg="red")
        click.echo("")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_configuration_service(self):
        """Load ConfigurationService from active profile configfile_paths when --deep is set.

        Returns the loaded service, or None when ``--deep`` is False.
        When ``--deep`` is True and config cannot be loaded for any reason,
        appends to ``self._errors`` (system error) and returns None — the caller
        must treat that as a hard failure (exit code 1).
        """
        if not self._deep:
            return None

        try:
            from strata.services.configuration_service import ConfigurationService
            from strata.utils.system import resolve_path

            if self._solution_controller.solution is None:
                self._errors.append("--deep requires an initialized workspace. Run `strata init` or remove --deep.")
                return None

            profile, _ = self._solution_controller.get_active_profile()
            if profile is None:
                self._errors.append("No active profile. Run `strata profile activate <name>` first, or remove --deep.")
                return None

            configfile_paths = profile.configfile_paths or []
            if not configfile_paths:
                self._errors.append(
                    "--deep requires at least one configfile path on the active profile. "
                    "Add one with `strata ref configfile add` or remove --deep."
                )
                return None

            repo_map = self._solution_controller.get_repo_map()

            resolved_paths = []
            for entry in configfile_paths:
                name = str(entry.name)
                try:
                    resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
                except ValueError as exc:
                    self.logger.debug(f"Config source '{name}': {exc}")
                    continue
                if not resolved.exists():
                    self.logger.debug(f"Config source '{name}': not found at {resolved}")
                    continue
                resolved_paths.append(str(resolved))

            if not resolved_paths:
                self._errors.append(
                    "--deep: no configfile_paths resolved to existing files. Check refs or remove --deep."
                )
                return None

            ConfigurationService.reset()
            config_svc = ConfigurationService.get_instance()
            success, load_errors = config_svc.load_from_paths(resolved_paths)
            if not success:
                self._errors.append(f"--deep: failed to load configuration: {'; '.join(load_errors)}")
                return None

            self.logger.debug(
                "ConfigurationService loaded for deep validation",
                profile=str(profile.name),
                files=len(resolved_paths),
            )
            return config_svc

        except Exception as exc:
            self._errors.append(f"--deep: unexpected error loading configuration: {exc}")
            return None

    def _generate_explanation(self) -> Optional[str]:
        """Generate a plain-English summary of what the validated file describes."""
        if not self._validator or not self._validator.service:
            return None

        service = self._validator.service
        model = service.model
        if model is None:
            return None

        kind = self._detected_kind or "unknown"
        name = getattr(getattr(model, "meta", None), "name", None) or "unnamed"
        spec = getattr(model, "spec", None)

        parts: List[str] = [f"This {kind} '{name}'"]

        if kind == "deployment":
            envs = getattr(spec, "environments", None) or []
            ws = getattr(spec, "workspace", None)
            stages = getattr(spec, "stages", None) or []
            tenant = getattr(spec, "tenant", None)
            ws_file = getattr(ws, "file", None) if ws else None

            if tenant:
                parts.append(f"belongs to tenant '{tenant}'")
            if envs:
                env_names = [str(e).rsplit("/", 1)[-1].replace(".yaml", "") for e in envs]
                parts.append(f"targets environment(s) {', '.join(env_names)}")
            if stages:
                stage_desc = [s.name for s in stages]
                parts.append(f"runs {len(stages)} stage(s): {', '.join(stage_desc)}")
            if ws_file:
                parts.append(f"references workspace '{ws_file}'")

        elif kind == "environment":
            region = getattr(spec, "region", None)
            includes = getattr(spec, "includes", None) or []
            overrides = getattr(spec, "overrides", None)
            if region:
                parts.append(f"targets region '{region}'")
            if includes:
                parts.append(f"includes {len(includes)} file(s)")
            if overrides:
                override_sections = []
                if getattr(overrides, "resources", None):
                    override_sections.append("resources")
                if getattr(overrides, "modules", None):
                    override_sections.append("modules")
                if getattr(overrides, "providers", None):
                    override_sections.append("providers")
                if override_sections:
                    parts.append(f"overrides: {', '.join(override_sections)}")

        elif kind == "workspace":
            resources = getattr(spec, "resources", None) or []
            namespaces = getattr(spec, "namespaces", None) or []
            modules = getattr(spec, "modules", None) or []
            parts.append(
                f"defines {len(resources)} resource(s), {len(namespaces)} namespace(s), {len(modules)} module(s)"
            )

        elif kind == "configuration":
            provisioners = getattr(spec, "provisioners", None) or []
            providers = getattr(spec, "providers", None) or []
            remotes = getattr(spec, "remotes", None) or []
            parts.append(
                f"declares {len(provisioners)} provisioner(s), {len(providers)} provider(s), {len(remotes)} remote(s)"
            )

        elif kind == "module":
            services = getattr(spec, "services", None) or []
            parts.append(f"defines {len(services)} service(s)")

        elif kind == "resource":
            provider = getattr(spec, "provider", None)
            resource_type = getattr(spec, "type", None)
            if provider:
                parts.append(f"uses provider '{provider}'")
            if resource_type:
                parts.append(f"of type '{resource_type}'")

        elif kind == "namespace":
            resources = getattr(spec, "resources", None) or []
            modules = getattr(spec, "modules", None) or []
            parts.append(f"contains {len(resources)} resource(s), {len(modules)} module(s)")

        elif kind == "provider":
            provider_type = getattr(spec, "type", None)
            if provider_type:
                parts.append(f"of type '{provider_type}'")

        elif kind == "tenant":
            description = getattr(getattr(model, "meta", None), "annotations", None) or {}
            desc = description.get("description", "")
            if desc:
                parts.append(f"— {desc}")

        elif kind == "dns":
            zones = getattr(spec, "zones", None) or []
            parts.append(f"manages {len(zones)} DNS zone(s)")

        elif kind == "firewall":
            rules = getattr(spec, "rules", None) or []
            parts.append(f"defines {len(rules)} firewall rule(s)")

        elif kind == "network":
            subnets = getattr(spec, "subnets", None) or []
            parts.append(f"defines {len(subnets)} subnet(s)")

        return " — ".join(parts) if len(parts) > 1 else parts[0]

    def _generate_fix_suggestions(self) -> List[str]:
        """Generate actionable fix suggestions from structured validation errors."""
        import difflib

        if not self._validator:
            return []

        suggestions: List[str] = []
        seen: set = set()

        for err in self._validator.get_structured_errors():
            ctx = err.context or {}
            error_type = ctx.get("type", "")

            if error_type == "extra_forbidden" and err.field:
                # Get the offending field name (last segment of the path)
                field_parts = err.field.replace(" -> ", ".").split(".")
                bad_field = field_parts[-1]
                # Try to find valid fields from the model
                valid_fields = self._get_valid_fields_for_path(field_parts[:-1])
                if valid_fields:
                    matches = difflib.get_close_matches(bad_field, valid_fields, n=3, cutoff=0.5)
                    if matches:
                        suggestion = f"Unknown field '{bad_field}'. Did you mean: {', '.join(matches)}?"
                    else:
                        suggestion = f"Unknown field '{bad_field}'. Valid fields: {', '.join(sorted(valid_fields)[:8])}"
                    if suggestion not in seen:
                        suggestions.append(suggestion)
                        seen.add(suggestion)

            elif error_type == "missing" and err.field:
                suggestion = f"Required field '{err.field}' is missing — add it to your YAML."
                if suggestion not in seen:
                    suggestions.append(suggestion)
                    seen.add(suggestion)

        return suggestions

    def _get_valid_fields_for_path(self, path_parts: List[str]) -> List[str]:
        """Resolve the Pydantic model at a given field path and return its valid field names."""
        if not self._validator or not self._detected_kind:
            return []

        from strata.models.common_models import PlatformKind

        try:
            kind = PlatformKind(self._detected_kind)
        except ValueError:
            return []

        # Get the root model class for this kind
        from strata.models.configuration_model import ConfigurationModel
        from strata.models.deployment_model import DeploymentModel
        from strata.models.dns_model import DnsModel
        from strata.models.environment_model import EnvironmentModel
        from strata.models.firewall_model import FirewallModel
        from strata.models.module_model import ModuleModel
        from strata.models.namespace_model import NamespaceModel
        from strata.models.network_model import NetworkModel
        from strata.models.provider_model import ProviderModel
        from strata.models.resource_model import ResourceModel
        from strata.models.tenant_model import TenantModel
        from strata.models.workspace_model import WorkspaceModel

        kind_to_model = {
            PlatformKind.CONFIGURATION: ConfigurationModel,
            PlatformKind.DEPLOYMENT: DeploymentModel,
            PlatformKind.DNS: DnsModel,
            PlatformKind.ENVIRONMENT: EnvironmentModel,
            PlatformKind.FIREWALL: FirewallModel,
            PlatformKind.MODULE: ModuleModel,
            PlatformKind.NAMESPACE: NamespaceModel,
            PlatformKind.NETWORK: NetworkModel,
            PlatformKind.PROVIDER: ProviderModel,
            PlatformKind.RESOURCE: ResourceModel,
            PlatformKind.TENANT: TenantModel,
            PlatformKind.WORKSPACE: WorkspaceModel,
        }

        model_class = kind_to_model.get(kind)
        if model_class is None:
            return []

        # Walk the path to find the nested model
        current: Any = model_class
        for part in path_parts:
            if not hasattr(current, "model_fields"):
                return []
            fields = current.model_fields
            if part not in fields:
                # Try numeric index (list item) — skip to the item type
                if part.isdigit():
                    continue
                return []
            field_info = fields[part]
            # Resolve the annotation to find a nested model
            annotation = field_info.annotation
            inner = self._unwrap_annotation(annotation)
            if inner is not None and hasattr(inner, "model_fields"):
                current = inner
            else:
                return []

        if hasattr(current, "model_fields"):
            return list(current.model_fields.keys())
        return []

    @staticmethod
    def _unwrap_annotation(annotation: Any) -> Optional[type]:
        """Unwrap Optional, List, Annotated to find the inner Pydantic model type."""
        import typing

        from pydantic import BaseModel as PydanticBaseModel

        origin = getattr(annotation, "__origin__", None)

        # Handle Optional[X] = Union[X, None]
        if origin is typing.Union:
            union_args = [a for a in annotation.__args__ if a is not type(None)]
            if union_args:
                return ValidateCommand._unwrap_annotation(union_args[0])
            return None

        # Handle List[X]
        if origin is list:
            list_args = getattr(annotation, "__args__", None)
            if list_args:
                return ValidateCommand._unwrap_annotation(list_args[0])
            return None

        # Handle Annotated[X, ...]
        if origin is typing.Annotated or (
            hasattr(typing, "Annotated") and origin is getattr(typing, "Annotated", None)
        ):
            ann_args = getattr(annotation, "__args__", None)
            if ann_args:
                return ValidateCommand._unwrap_annotation(ann_args[0])
            return None

        # Direct model class
        if isinstance(annotation, type) and issubclass(annotation, PydanticBaseModel):
            return annotation

        return None
