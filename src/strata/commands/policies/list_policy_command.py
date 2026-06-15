#!/usr/bin/env python3
"""Command to list declared policies from the active configuration."""

from pathlib import Path
from typing import Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.policy_controller import PolicyController
from strata.models.policy_model import PolicyModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService


class ListPolicyCommand(BaseCommand):
    """List policies declared in ``configuration.spec.policies``.

    Without ``--file``: loads the active configuration from the workspace's
    active profile and lists all declared policies.

    With ``--file <deploy.yaml>``: additionally loads the deployment model
    to annotate which lifecycle phases that deployment can trigger, giving
    the operator context for which policies are relevant.
    """

    OPERATION = "policy_list"
    INIT_REQUIRED = True

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose or False,
            quiet=quiet or False,
        )
        self._raw_file: Optional[str] = file
        self._file_path: Optional[Path] = Path(file) if file else None
        self._configuration_service: Optional[ConfigurationService] = None
        self._deployment_service: Optional[DeploymentService] = None
        self._policies: List[PolicyModel] = []
        self._deployment_phases: List[str] = []
        self._source_label: str = ""

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
            error_msg = f"Failed to list policies: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Lifecycle overrides
    # ------------------------------------------------------------------

    def _run_execution(self) -> bool:
        """Load services and extract policies."""
        controller = PolicyController()

        # --- Load configuration from the active profile ---
        self._configuration_service = self._load_configuration_service()
        if self._configuration_service is None:
            return False

        # Build source label (shown in console header)
        profile, _ = self._solution_controller.get_active_profile()
        self._source_label = f"profile:{profile.name}" if profile else "active profile"

        # --- Optionally load deployment when -f is given ---
        if self._raw_file:
            ok = self._load_deployment_service()
            if not ok:
                return False
            if self._deployment_service and self._deployment_service.model:
                deploy_name = str(self._deployment_service.model.meta.name)
                self._source_label += f"  ·  deployment: {deploy_name}"

        # --- Extract policies via controller ---
        self._policies = controller.get_declared_policies(self._configuration_service)

        # --- Annotate phases triggered by this deployment ---
        if self._deployment_service:
            self._deployment_phases = controller.get_deployment_phases(self._deployment_service)

        # Populate output data (json / text formats use this)
        self._output_data = {
            "source": self._source_label,
            "deployment": str(self._file_path) if self._file_path else None,
            "policy_count": len(self._policies),
            "enabled_count": sum(1 for p in self._policies if p.enabled),
            "phases_triggered": self._deployment_phases if self._deployment_phases else None,
            "policies": [
                {
                    "name": str(p.name),
                    "type": p.type,
                    "phase": p.phase,
                    "enforcement": p.enforcement,
                    "enabled": p.enabled,
                    "description": p.description,
                    "configuration": p.configuration,
                }
                for p in self._policies
            ],
        }

        return True

    def _after_execute(self) -> bool:
        if self._is_console_output():
            self._render_console()
        return super()._after_execute()

    # ------------------------------------------------------------------
    # Console rendering
    # ------------------------------------------------------------------

    def _render_console(self) -> None:
        phases_note = ""
        if self._deployment_phases:
            phases_note = f"  ·  phases triggered: {', '.join(self._deployment_phases)}"

        click.echo(f"\n📋  Policies  —  {self._source_label}{phases_note}")
        click.echo("─" * 70)

        if not self._policies:
            click.echo("    (no policies declared)\n")
            return

        # Column header
        click.echo(f"  {'Name':<24}{'Type':<20}{'Phase':<12}{'Enforcement':<14}Enabled")
        click.echo(f"  {'─' * 23} {'─' * 18} {'─' * 10} {'─' * 12} {'─' * 7}")

        for p in self._policies:
            name = str(p.name)[:23]
            ptype = p.type[:18]
            phase = p.phase[:10]

            if p.enforcement == "deny":
                enf_str = click.style(f"{p.enforcement:<14}", fg="red")
            elif p.enforcement == "warn":
                enf_str = click.style(f"{p.enforcement:<14}", fg="yellow")
            else:
                enf_str = click.style(f"{p.enforcement:<14}", fg="bright_black")

            if not p.enabled:
                enabled_str = click.style("✗ (disabled)", dim=True)
                click.echo(click.style(f"  {name:<24}{ptype:<20}{phase:<12}", dim=True) + enf_str + enabled_str)
            else:
                click.echo(f"  {name:<24}{ptype:<20}{phase:<12}{enf_str}✓")

        click.echo("")
        total = len(self._policies)
        enabled = sum(1 for p in self._policies if p.enabled)
        noun = "policy" if total == 1 else "policies"
        if enabled == total:
            click.echo(f"  {total} {noun} declared")
        else:
            click.echo(f"  {total} declared · {enabled} enabled · {total - enabled} disabled")
        click.echo("")

    # ------------------------------------------------------------------
    # Service loaders
    # ------------------------------------------------------------------

    def _load_configuration_service(self) -> Optional[ConfigurationService]:
        """Load ConfigurationService from the active profile's configfile_paths."""
        from strata.utils.system import resolve_path

        if self._solution_controller.solution is None:
            self._errors.append("policy list requires an initialized workspace. Run `strata sln init` first.")
            return None

        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            self._errors.append("policy list requires an active profile. Run `strata profile activate <name>`.")
            return None

        configfile_paths = profile.configfile_paths or []
        if not configfile_paths:
            self._errors.append(
                "policy list requires at least one configfile path on the active profile. "
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
            self.logger.debug(
                "ConfigurationService loaded",
                profile=str(profile.name),
                files=len(resolved_paths),
            )
            return config_svc
        except Exception as exc:
            self._errors.append(f"Failed to load configuration service: {exc}")
            return None

    def _load_deployment_service(self) -> bool:
        """Resolve and load the deployment file given via ``--file``.

        Only Phase-1 (Pydantic structural) validation is performed — no
        cross-reference resolution is needed just to read stage names.
        Returns True on success, False on failure (errors in self._errors).
        """
        from strata.utils.system import resolve_path

        assert self._raw_file is not None

        repo_map = self._solution_controller.get_repo_map() if self._solution_controller else {}
        try:
            resolved = resolve_path(str(self._work_path), self._raw_file, repo_map=repo_map)
        except ValueError as exc:
            self._errors.append(f"Deployment file reference error: {exc}")
            return False

        if not resolved.exists():
            self._errors.append(f"Deployment file not found: {resolved}")
            return False

        self._file_path = resolved

        try:
            svc = DeploymentService.load(str(self._file_path), validate=True)
            if not svc.is_validated():
                self._errors.extend(svc.get_errors())
                return False
            self._deployment_service = svc
            return True
        except Exception as exc:
            self._errors.append(f"Failed to load deployment file: {exc}")
            return False
