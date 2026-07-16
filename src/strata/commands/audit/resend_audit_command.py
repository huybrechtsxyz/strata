"""Command to re-forward deploy-log entries to configured audit sinks."""

from __future__ import annotations

from typing import Dict, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand
from strata.controllers.audit_controller import AuditController
from strata.utils.config import SOLUTION_DEPLOY_LOG_DIR, SOLUTION_DIR


class ResendAuditCommand(SchemaBaseCommand):
    """Re-forward local deploy-log records to configured audit sinks."""

    OPERATION = "audit_resend"

    def __init__(
        self,
        last: Optional[int] = None,
        since: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._last = last
        self._since = since
        self._sent: int = 0
        self._failed: int = 0

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    @classmethod
    def show_console_header(cls, work_path: Optional[str] = None) -> None:
        """Suppress the standard base-command chrome."""

    @classmethod
    def show_console_footer(cls) -> None:
        """Suppress the standard base-command chrome."""

    def _execute(self) -> bool:
        from strata.models.audit_config_model import AuditConfigModel
        from strata.services.configuration_service import ConfigurationService

        base_path = self._work_path / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR

        audit_config: Optional[AuditConfigModel] = None
        try:
            config_service = ConfigurationService.load(
                str(self._work_path / SOLUTION_DIR / "configuration.yaml"), validate=False
            )
            if config_service.model and config_service.model.spec and config_service.model.spec.audit:
                audit_config = config_service.model.spec.audit
        except Exception:
            pass

        if not audit_config or not audit_config.sinks:
            self._errors.append("No audit sinks configured. Add spec.audit.sinks to configuration.yaml.")
            return False

        controller = AuditController(work_path=self._work_path)
        self._sent, self._failed = controller.resend(
            base_path=base_path,
            audit_config=audit_config,
            since=self._since,
            last=self._last,
        )

        self._output_data = {"sent": self._sent, "failed": self._failed}
        return self._failed == 0

    def _after_execute(self) -> bool:
        if self._is_console_output():
            click.echo(f"Resend complete: {self._sent} sent, {self._failed} failed.")
        return super()._after_execute()
