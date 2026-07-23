"""Base command variant for schema commands that do not require a solution."""

from datetime import datetime, timezone
from typing import Dict

from strata.commands.base_command import BaseCommand
from strata.logger import configure_audit_log, is_audit_configured
from strata.logger.context import set_context
from strata.utils.config import get_audit_log_path


class SchemaBaseCommand(BaseCommand):
    """Base command for schema operations that should not read solution.json."""

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        """Initialize without loading workspace solution state.

        Schema commands are workspace-optional and should keep output clean even
        when .strata/solution.json is missing or invalid.
        """
        try:
            self._start_time = datetime.now(timezone.utc)
            self._configure_session_logging()

            if not self._work_path.exists():
                self._errors.append(f"Work path does not exist: {self._work_path}")
                return False

            set_context({"solution_id": "unknown", "execution_id": self._execution_id})

            if not is_audit_configured():
                audit_path = get_audit_log_path(self._work_path)
                configure_audit_log(log_path=str(audit_path))

            if show_header and self._is_console_output():
                self.show_console_header()

            return True
        except Exception as exc:
            self._errors.append(f"Failed to initialize command: {exc}")
            return False
