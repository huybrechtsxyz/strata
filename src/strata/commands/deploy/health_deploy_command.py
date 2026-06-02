"""Command to run health checks against provisioned infrastructure stages."""

import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.models.common_models import ProvisionerType
from strata.models.deployment_model import DeploymentStageModel, HealthCheckModel


class HealthDeployCommand(BaseDeployCommand):
    """Run health checks against provisioned deployment stages.

    For each stage that has ``health_checks`` defined:
    1. Fetch live Terraform outputs for the stage (``terraform output -json``).
    2. For each check, resolve the target (URL / host:port) from either a static
       field or a Terraform output value.
    3. Execute the check (HTTP GET or TCP connect).
    4. Report pass / fail per check and an overall stage result.

    Exit codes:
      0  — all checks passed
      3  — one or more checks failed (validation failure)
      1  — execution error (connectivity, missing outputs, etc.)
    """

    OPERATION = "deploy_health"

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
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._stage = stage

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

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

            ok = self._run_health_checks()

            if not self._after_execute():
                self._finalize(success=False)
                return False

            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_health: {exc}")
            self.logger.exception("deploy_health failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Core
    # -------------------------------------------------------------------------

    def _run_health_checks(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        all_stages: List[DeploymentStageModel] = spec.stages or []

        stages = [s for s in all_stages if s.name == self._stage] if self._stage else all_stages
        if self._stage and not stages:
            self._errors.append(f"Stage '{self._stage}' not found. Available: {[s.name for s in all_stages]}")
            return False

        # Filter to stages that actually have health checks defined
        checkable = [s for s in stages if s.health_checks]
        skipped = [s for s in stages if not s.health_checks]

        if skipped and self._is_console_output():
            names = ", ".join(str(s.name) for s in skipped)
            click.echo(f"\n  ℹ  No health checks defined for: {names}")

        if not checkable:
            if self._is_console_output():
                click.echo(
                    "\n  ℹ  No stages have health checks configured.\n"
                    "     Add 'health_checks:' to a stage in your deployment YAML to use this command."
                )
            self._output_data = {"mode": "health", "stages": {}, "summary": "no_checks_defined"}
            self._finalize(success=True)
            return True

        if self._is_console_output():
            click.echo(f"\n🏥  Running health checks for {len(checkable)} stage(s)…\n")

        results: Dict[str, Any] = {}
        all_passed = True

        for stage in checkable:
            ok, stage_results = self._check_stage(stage)
            results[str(stage.name)] = stage_results
            if not ok:
                all_passed = False
            if self._is_console_output():
                self._print_stage_results(str(stage.name), ok, stage_results)

        passed = sum(1 for r in results.values() if r.get("passed"))
        failed = len(results) - passed

        self._output_data = {
            "mode": "health",
            "stages": results,
            "summary": {
                "total_stages": len(results),
                "passed": passed,
                "failed": failed,
            },
        }

        if self._is_console_output():
            icon = "✅" if all_passed else "❌"
            click.echo(f"  {icon}  {passed}/{len(results)} stages healthy\n")

        return all_passed

    # -------------------------------------------------------------------------
    # Per-stage
    # -------------------------------------------------------------------------

    def _check_stage(self, stage: DeploymentStageModel) -> Tuple[bool, Dict[str, Any]]:
        """Fetch outputs then run every health check for the stage."""
        checks = stage.health_checks or []

        # Fetch live outputs once per stage
        outputs: Dict[str, Any] = {}
        deployer = self._create_deployer(stage)
        if deployer is not None:
            ok_ws, _ = deployer.validate_workspace()
            ok_env, _ = deployer.validate_environment()
            if ok_ws and ok_env:
                ok_setup, _ = deployer.setup()
                if ok_setup:
                    ok_out, outputs, _ = deployer.output()
                    if not ok_out:
                        outputs = {}

        check_results: List[Dict[str, Any]] = []
        all_passed = True

        for check in checks:
            ok, detail = self._run_single_check(check, outputs)
            if not ok:
                all_passed = False
            check_results.append({"name": check.name, "type": check.type, "passed": ok, **detail})

        return all_passed, {"passed": all_passed, "checks": check_results}

    def _run_single_check(self, check: HealthCheckModel, outputs: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """Execute one health check; return (passed, detail_dict)."""
        if check.type == "http":
            return self._http_check(check, outputs)
        return self._tcp_check(check, outputs)

    # -------------------------------------------------------------------------
    # HTTP check
    # -------------------------------------------------------------------------

    def _http_check(self, check: HealthCheckModel, outputs: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        url = self._resolve_url(check, outputs)
        if url is None:
            return False, {
                "error": (
                    f"Cannot resolve URL for check '{check.name}': "
                    f"output_key='{check.output_key}' not found in stage outputs."
                )
            }

        try:
            req = urllib.request.Request(url, method="GET")  # noqa: S310
            with urllib.request.urlopen(req, timeout=check.timeout) as resp:  # noqa: S310
                status = resp.status
                passed = status == check.expect_status
                return passed, {
                    "url": url,
                    "status_code": status,
                    "expected": check.expect_status,
                }
        except urllib.error.HTTPError as exc:
            status = exc.code
            passed = status == check.expect_status
            return passed, {
                "url": url,
                "status_code": status,
                "expected": check.expect_status,
            }
        except Exception as exc:
            return False, {"url": url, "error": str(exc)}

    def _resolve_url(self, check: HealthCheckModel, outputs: Dict[str, Any]) -> Optional[str]:
        if check.output_key:
            val = outputs.get(check.output_key)
            if val is None:
                return None
            return str(val)
        return check.url

    # -------------------------------------------------------------------------
    # TCP check
    # -------------------------------------------------------------------------

    def _tcp_check(self, check: HealthCheckModel, outputs: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        host, port = self._resolve_host_port(check, outputs)
        if host is None or port is None:
            return False, {
                "error": (
                    f"Cannot resolve host:port for check '{check.name}': "
                    f"output_key='{check.output_key}' not found in stage outputs."
                )
            }

        try:
            with socket.create_connection((host, port), timeout=check.timeout):
                return True, {"host": host, "port": port}
        except Exception as exc:
            return False, {"host": host, "port": port, "error": str(exc)}

    def _resolve_host_port(
        self, check: HealthCheckModel, outputs: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[int]]:
        if check.output_key:
            val = outputs.get(check.output_key)
            if val is None:
                return None, None
            # Expect "host:port" format
            raw = str(val)
            if ":" in raw:
                parts = raw.rsplit(":", 1)
                try:
                    return parts[0], int(parts[1])
                except ValueError:
                    return None, None
            return None, None
        return check.host, check.port

    # -------------------------------------------------------------------------
    # Console output
    # -------------------------------------------------------------------------

    def _print_stage_results(self, stage_name: str, passed: bool, results: Dict[str, Any]) -> None:
        icon = "✅" if passed else "❌"
        click.echo(f"  {icon}  Stage: {stage_name}")
        for chk in results.get("checks", []):
            c_icon = "✅" if chk.get("passed") else "❌"
            name = chk.get("name", "?")
            detail_parts = []
            if "url" in chk:
                detail_parts.append(chk["url"])
            if "host" in chk:
                detail_parts.append(f"{chk['host']}:{chk.get('port', '?')}")
            if "status_code" in chk:
                detail_parts.append(f"HTTP {chk['status_code']}")
            if "error" in chk:
                detail_parts.append(f"error: {chk['error']}")
            detail = "  ".join(detail_parts)
            click.echo(f"       {c_icon}  {name}  {detail}")
        click.echo()

    # -------------------------------------------------------------------------
    # Deployer factory (same as RunDeployCommand / StatusDeployCommand)
    # -------------------------------------------------------------------------

    def _create_deployer(self, stage: DeploymentStageModel):
        """Instantiate and return the deployer for *stage*, or None.

        Resolution: stage.provisioner → workspace provisioners list → deployer type.
        Returns None silently when no provisioner is configured (health checks
        run without live deployer output in that case).
        """
        resolved_type: Optional[str] = None

        if stage.provisioner and self._deployment_service is not None:
            workspace_service = self._deployment_service.get_workspace_service()
            if workspace_service:
                spec = workspace_service.model.spec  # type: ignore[union-attr]
                _provisioners = spec.provisioners or []
                _iac = next((p for p in _provisioners if p.name == stage.provisioner), None)
                if _iac and _iac.provisioner == ProvisionerType.TERRAFORM:
                    resolved_type = "terraform"
                elif _iac and _iac.provisioner == ProvisionerType.ANSIBLE:
                    resolved_type = "ansible"
                elif _iac and _iac.provisioner == ProvisionerType.COMPOSE:
                    resolved_type = "compose"
                elif _iac and _iac.provisioner == ProvisionerType.HELM:
                    resolved_type = "helm"

        if resolved_type is None:
            return None

        if resolved_type == "terraform":
            return TerraformDeployer(
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
            )

        if resolved_type == "ansible":
            from strata.deployers.ansible_deployer import AnsibleDeployer

            return AnsibleDeployer(
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
            )

        if resolved_type == "compose":
            from strata.deployers.compose_deployer import ComposeDeployer

            return ComposeDeployer(
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
            )

        if resolved_type == "helm":
            from strata.deployers.helm_deployer import HelmDeployer

            return HelmDeployer(
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
            )

        return None
