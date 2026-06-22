"""Command to validate a single platform YAML file or run cross-manifest overlap checks."""

import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import yaml

from strata.commands.base_command import BaseCommand
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
    INIT_REQUIRED = False

    def __init__(
        self,
        file: Optional[str] = None,
        path: Optional[str] = None,
        deep: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file_path_raw: Optional[str] = file
        self._path_glob: Optional[str] = path
        self._deep: bool = deep
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
            # Evaluate validate-phase policies — failures are validation errors
            # (exit code 3), not system failures (exit code 1).
            self._evaluate_validate_policies()
            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution processing failed")
                self._finalize(success=False)
                return False

            # Finalize as success even when there are validation errors —
            # system-level execution succeeded; exit code difference is handled
            # by handle_command_exit() via has_validation_errors().
            self._finalize(success=True)
            return True

        except Exception as e:
            error_msg = f"Failed to validate file: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Lifecycle overrides
    # ------------------------------------------------------------------

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
            raise click.UsageError(
                "Overlap check requires an active profile. Run `strata profile activate <name>` first."
            )

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
        if denied:
            self._validate_policy_denied = True
        return not denied

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
        else:
            click.secho("\n✅  Validation PASSED", fg="green")

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
                self._errors.append(
                    "--deep requires an active profile. Run `strata profile activate <name>` or remove --deep."
                )
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
