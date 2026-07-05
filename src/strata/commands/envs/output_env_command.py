"""Command to display live Terraform outputs for a deployment."""

import json
from typing import Any, Dict, List, Optional, Tuple

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.deployers.factory import DeployerFactory
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.models.common_models import ProvisionerType
from strata.models.deployment_model import DeploymentStageModel


class OutputEnvCommand(BaseDeployCommand):
    """Display live Terraform outputs for a deployment.

    Runs ``terraform output -json`` per stage and presents the results grouped
    by provisioner in a table.

    ``--name NAME``
        Print a single output value only.

    ``--provisioner NAME``
        Limit to stages that reference a specific provisioner.

    ``--raw``
        Print the bare value with no formatting — requires ``--name``.
        Suppresses the header/footer chrome; intended for shell scripting::

            IP=$(strata env output -f deploy.yaml --name hearth_ip --raw)

    ``--json``
        Emit the raw outputs dict as JSON — bypasses the strata envelope.
        Equivalent to running ``tofu output -json`` directly.
    """

    OPERATION = "env_output"
    INIT_REQUIRED = True

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        name: Optional[str] = None,
        provisioner: Optional[str] = None,
        raw: bool = False,
        json_output: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose or False,
            quiet=quiet or False,
        )
        self._name = name
        self._provisioner = provisioner
        self._raw = raw
        self._json_output = json_output

    def get_required_integrations(self) -> Dict[str, str]:
        return {"terraform": "reading live infrastructure outputs"}

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    def execute(self) -> bool:
        # In --raw / --json mode suppress all strata chrome so only the value
        # or JSON object is printed — clean output for shell scripting.
        show_chrome = not (self._raw or self._json_output)

        try:
            if not self._initialize(show_header=show_chrome):
                if show_chrome and self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False, show_footer=show_chrome)
                return False

            if not self._before_execute():
                if show_chrome and self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False, show_footer=show_chrome)
                return False

            # Prevent the standard JSON envelope in raw/json passthrough modes.
            if not show_chrome:
                self._output_format = "console"

            ok = self._run()
            self._after_execute()
            self._finalize(success=ok, show_footer=show_chrome)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute env_output: {exc}")
            self.logger.exception("env_output failed")
            self._finalize(success=False, show_footer=show_chrome)
            return False

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _run(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        all_stages: List[DeploymentStageModel] = spec.stages or []

        # Keep only terraform stages
        terraform_stages = [s for s in all_stages if self._is_terraform_stage(s)]

        # Apply --provisioner filter
        if self._provisioner:
            terraform_stages = [s for s in terraform_stages if str(s.provisioner) == self._provisioner]
            if not terraform_stages:
                avail = sorted({str(s.provisioner) for s in all_stages if self._is_terraform_stage(s)})
                self._errors.append(
                    f"No terraform stages found for provisioner '{self._provisioner}'. "
                    f"Available: {avail if avail else ['(none)']}"
                )
                return False

        if not terraform_stages:
            if self._is_console_output():
                click.echo("  (no terraform stages in this deployment)")
            self._output_data = {"file": str(self._file_path), "stages": {}}
            return True

        # Fetch outputs per stage
        stage_results: Dict[str, Dict[str, Any]] = {}
        any_failed = False

        for stage in terraform_stages:
            ok, outputs, msgs = self._fetch_stage_outputs(stage)
            self._messages.extend(msgs)
            stage_results[str(stage.name)] = {
                "provisioner": str(stage.provisioner),
                "outputs": outputs,
                "ok": ok,
                "error": msgs[-1] if not ok and msgs else None,
            }
            if not ok:
                any_failed = True

        # Apply --name filter across all stages
        if self._name:
            for data in stage_results.values():
                data["outputs"] = {k: v for k, v in data["outputs"].items() if k == self._name}

        # ── --raw: bare value, no formatting ────────────────────────────────
        if self._raw:
            if not self._name:
                self._errors.append("--raw requires --name to identify which output to return.")
                return False
            for data in stage_results.values():
                val = data["outputs"].get(self._name)
                if val is not None:
                    click.echo(str(val))
                    return not any_failed
            self._errors.append(
                f"Output '{self._name}' not found in any stage. "
                "Check the name or run without --name to list all outputs."
            )
            return False

        # ── --json: raw JSON passthrough ─────────────────────────────────────
        if self._json_output:
            combined: Dict[str, Any] = {}
            for data in stage_results.values():
                combined.update(data["outputs"])
            click.echo(json.dumps(combined, indent=2, default=str))
            return not any_failed

        # ── Structured output data (for --output json envelope) ──────────────
        self._output_data = {
            "file": str(self._file_path),
            "stages": {
                name: {
                    "provisioner": data["provisioner"],
                    "outputs": data["outputs"],
                    "ok": data["ok"],
                    "error": data["error"],
                }
                for name, data in stage_results.items()
            },
        }

        # ── Console table ────────────────────────────────────────────────────
        if self._is_console_output():
            self._print_console(stage_results)

        return not any_failed

    # -------------------------------------------------------------------------
    # Per-stage fetch
    # -------------------------------------------------------------------------

    def _fetch_stage_outputs(self, stage: DeploymentStageModel) -> Tuple[bool, Dict[str, Any], List[str]]:
        deployer = self._create_deployer(stage)
        if deployer is None:
            return False, {}, [f"Stage '{stage.name}': could not create deployer."]

        for validate_fn in (deployer.validate_workspace, deployer.validate_environment):
            ok, msgs = validate_fn()
            if not ok:
                return False, {}, msgs

        ok, msgs = deployer.setup()
        if not ok:
            return False, {}, msgs

        return deployer.output()

    # -------------------------------------------------------------------------
    # Console rendering
    # -------------------------------------------------------------------------

    def _print_console(self, stage_results: Dict[str, Dict[str, Any]]) -> None:
        """Print outputs grouped by provisioner name."""
        # Merge stages that share the same provisioner
        by_provisioner: Dict[str, Dict[str, Any]] = {}
        for data in stage_results.values():
            prov = data["provisioner"]
            if prov not in by_provisioner:
                by_provisioner[prov] = {"outputs": {}, "ok": True, "errors": []}
            by_provisioner[prov]["outputs"].update(data["outputs"])
            if not data["ok"] and data["error"]:
                by_provisioner[prov]["ok"] = False
                by_provisioner[prov]["errors"].append(data["error"])

        click.echo("")
        for prov_name, prov_data in by_provisioner.items():
            click.echo(f"  Provisioner: {prov_name}")
            if not prov_data["ok"]:
                for err in prov_data["errors"]:
                    click.echo(f"  ❌  {err}")
            elif prov_data["outputs"]:
                self._print_table(prov_data["outputs"])
            else:
                label = f"key '{self._name}' not found" if self._name else "no outputs defined"
                click.echo(f"  ({label})")
            click.echo("")

    _CELL_PAD = 1  # spaces between text and │

    def _print_table(self, outputs: Dict[str, Any]) -> None:
        """Render outputs as a box-drawing table."""
        p = self._CELL_PAD
        col1 = max(len("Output"), *(len(k) for k in outputs)) + p * 2
        col2 = max(len("Value"), *(len(str(v)) for v in outputs.values())) + p * 2

        top = f"  ┌{'─' * col1}┬{'─' * col2}┐"
        divider = f"  ├{'─' * col1}┼{'─' * col2}┤"
        bottom = f"  └{'─' * col1}┴{'─' * col2}┘"

        def row(left: str, right: str) -> str:
            return f"  │{left:^{col1}}│{right:^{col2}}│"

        click.echo(top)
        click.echo(row(" Output", " Value"))
        click.echo(divider)
        for k, v in outputs.items():
            l_cell = (" " * p) + k + (" " * (col1 - len(k) - p))
            r_cell = (" " * p) + str(v) + (" " * (col2 - len(str(v)) - p))
            click.echo(f"  │{l_cell}│{r_cell}│")
        click.echo(bottom)

    # -------------------------------------------------------------------------
    # Helpers (terraform-only; mirrored from output_deploy_command)
    # -------------------------------------------------------------------------

    def _is_terraform_stage(self, stage: DeploymentStageModel) -> bool:
        if not stage.provisioner or self._deployment_service is None:
            return False
        workspace_service = self._deployment_service.get_workspace_service()
        if not workspace_service:
            return False
        spec = workspace_service.model.spec  # type: ignore[union-attr]
        provisioners = spec.provisioners or []
        iac = next((p for p in provisioners if p.name == stage.provisioner), None)
        return iac is not None and iac.provisioner == ProvisionerType.TERRAFORM

    def _create_deployer(self, stage: DeploymentStageModel) -> Optional[TerraformDeployer]:
        if not stage.provisioner or self._deployment_service is None:
            self._errors.append(f"Stage '{stage.name}': missing provisioner reference.")
            return None
        workspace_service = self._deployment_service.get_workspace_service()
        if not workspace_service:
            self._errors.append(f"Stage '{stage.name}': workspace service not loaded.")
            return None
        spec = workspace_service.model.spec  # type: ignore[union-attr]
        provisioners = spec.provisioners or []
        iac = next((p for p in provisioners if p.name == stage.provisioner), None)
        if iac is None or iac.provisioner != ProvisionerType.TERRAFORM:
            self._errors.append(f"Stage '{stage.name}': provisioner '{stage.provisioner}' is not terraform.")
            return None
        return DeployerFactory.create(  # type: ignore[return-value]
            "terraform",
            stage=stage,
            deployment_service=self._deployment_service,  # type: ignore[arg-type]
            configuration_service=self._configuration_service,  # type: ignore[arg-type]
            build_path=self._build_path,
            work_path=self._work_path,
            verbose=self._is_verbose(),
            solution_controller=self._solution_controller,
        )
