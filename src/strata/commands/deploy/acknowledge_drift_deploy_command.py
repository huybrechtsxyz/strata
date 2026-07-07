"""Command to acknowledge (or un-acknowledge) a drifted resource address."""

from typing import Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.utils.drift_history import DriftHistoryStore


class AcknowledgeDriftDeployCommand(BaseDeployCommand):
    """Acknowledge expected drift for a specific Terraform resource address.

    Acknowledged entries are excluded from ``strata deploy drift run`` output and
    do not contribute to exit code 3.  They remain suppressed until you explicitly
    remove the acknowledgement or reset the baseline.

    Use ``--remove`` to un-acknowledge a previously suppressed address.

    Examples::

        strata deploy drift acknowledge -f deploy/prod.yaml \\
            --address "azurerm_autoscale_setting.web" \\
            --reason "auto-scaler managed — expected drift"

        strata deploy drift acknowledge -f deploy/prod.yaml \\
            --address "azurerm_autoscale_setting.web" \\
            --remove
    """

    OPERATION = "deploy_drift_acknowledge"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        address: Optional[str] = None,
        reason: str = "",
        remove: bool = False,
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
        self._address = address or ""
        self._reason = reason
        self._remove = remove

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

            ok = self._run_acknowledge()
            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_drift_acknowledge: {exc}")
            self.logger.exception("deploy_drift_acknowledge failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Core
    # -------------------------------------------------------------------------

    def _run_acknowledge(self) -> bool:
        if not self._address:
            self._errors.append("--address is required")
            return False

        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        deployment_name = str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
        history = DriftHistoryStore(self._work_path, deployment_name)
        history.load()

        if self._remove:
            ok = history.remove_acknowledgement(self._address)
            if not ok:
                self._errors.append(
                    f"Address '{self._address}' was not acknowledged (or not found in history for '{deployment_name}')."
                )
                return False

            history.save()

            if self._is_console_output():
                click.echo(
                    f"\n  ✅  Acknowledgement removed for: {self._address}\n"
                    f"      Future drift checks will report this address again.\n"
                )

            self._output_data = {
                "deployment": deployment_name,
                "address": self._address,
                "action": "unacknowledged",
            }

        else:
            history.acknowledge(self._address, reason=self._reason)
            history.save()

            if self._is_console_output():
                reason_note = f" ({self._reason})" if self._reason else ""
                click.echo(
                    f"\n  📌  Acknowledged: {self._address}{reason_note}\n"
                    f"      This address will be suppressed in future drift checks.\n"
                )

            self._output_data = {
                "deployment": deployment_name,
                "address": self._address,
                "action": "acknowledged",
                "reason": self._reason,
            }

        return True
