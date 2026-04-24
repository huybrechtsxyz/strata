"""Controller for solution lifecycle operations."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from xyz_platform.controllers.base_controller import BaseController
from xyz_platform.logger.logger import get_active_log_file
from xyz_platform.models.solution_model import SolutionModel, SolutionSpecRepositoryModel
from xyz_platform.services.solution_service import SolutionService
from xyz_platform.utils.config import (
    SOLUTION_CONFIGURATION_FILE,
    SOLUTION_DIR,
    SOLUTION_FILE,
    SOLUTION_LOGGING_FILE,
    SOLUTION_WORKSPACE_SUFFIX,
)
from xyz_platform.utils.system import generate_uuid


class SolutionController(BaseController):
    """
    Controller for solution-level operations.

    Responsibilities:
    - Initialise a new solution (``xyz solution init <name>``)
    - Load and validate an existing solution from disk
    - Add / remove repositories from a solution
    - Generate the VS Code ``.code-workspace`` file from solution state
    - Persist solution changes back to ``solution.json``
    """

    def __init__(self, work_path: Path) -> None:
        super().__init__()
        self._work_path = work_path
        self._service = SolutionService.get_instance()
        self._solution: Optional[SolutionModel] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def solution(self) -> Optional[SolutionModel]:
        """Currently loaded solution model, or None if not yet loaded."""
        return self._solution

    def get_solution_id(self) -> str:
        """Return the solution_id of the loaded solution, or '' if not loaded."""
        if self._solution is None:
            return ""
        return self._solution.spec.solution_id

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def load(self) -> Tuple[bool, List[str]]:
        """Load the solution model from ``<work_path>/.platform/solution.json``.

        Returns:
            (success, errors)
        """
        path = self._solution_path()
        try:
            self._solution = self._service.load_from_json(path)
            self.logger.info("Solution loaded", name=self._solution.meta.name, path=str(path))
            return True, []
        except Exception as e:
            msg = f"Failed to load solution from {path}: {e}"
            self._add_error(msg)
            return False, self.get_errors()

    def save(self) -> Tuple[bool, List[str]]:
        """Persist the current solution model to disk.

        Returns:
            (success, errors)
        """
        if self._solution is None:
            self._add_error("No solution loaded — cannot save.")
            return False, self.get_errors()

        path = self._solution_path()
        try:
            self._service.save_to_json(self._solution, path)
            self.logger.info("Solution saved", name=self._solution.meta.name, path=str(path))
            return True, []
        except Exception as e:
            msg = f"Failed to save solution to {path}: {e}"
            self._add_error(msg)
            return False, self.get_errors()

    def update_last_execution(self, execution_id: str) -> Tuple[bool, List[str]]:
        """Record the most recent execution ID and timestamp, then persist.

        Args:
            execution_id: Execution UUID to record as the last run.

        Returns:
            (success, errors)
        """
        if self._solution is None:
            self._add_error("No solution loaded — cannot update last execution.")
            return False, self.get_errors()

        self._solution.spec.last_execution_id = execution_id
        self._solution.spec.last_execution_on = datetime.now(timezone.utc).isoformat()
        self.logger.info(
            "Last execution updated",
            execution_id=execution_id,
            last_execution_on=self._solution.spec.last_execution_on,
        )
        return self.save()

    # ------------------------------------------------------------------
    # Initialise
    # ------------------------------------------------------------------

    def init(self, name: str) -> Tuple[bool, List[str]]:
        """Initialise a new solution workspace.

        Creates the ``.platform/`` state directory, an empty
        ``solution.json``, and a ``<name>.code-workspace`` file in
        *work_path*.

        Args:
            name: Solution name (used as the workspace file stem).

        Returns:
            (success, errors)
        """
        SolutionController.get_state_dir(self._work_path).mkdir(parents=True, exist_ok=True)

        self._solution = SolutionModel(
            apiVersion="platform.huybrechts.xyz/v1",
            kind="solution",
            meta={"name": name},  # type: ignore[arg-type]
            spec={"solution_id": generate_uuid()},  # type: ignore[arg-type]
        )

        ok, errors = self.save()
        if not ok:
            return ok, errors

        ok, errors = self.generate_workspace()
        if not ok:
            return ok, errors

        self._add_message(f"Solution '{name}' initialised at {self._work_path}")
        return True, []

    # ------------------------------------------------------------------
    # Repository management
    # ------------------------------------------------------------------

    def add_repository(self, repo: SolutionSpecRepositoryModel) -> Tuple[bool, List[str]]:
        """Add a repository to the solution.

        Args:
            repo: Repository model to add.

        Returns:
            (success, errors)
        """
        if self._solution is None:
            self._add_error("No solution loaded.")
            return False, self.get_errors()

        if self._solution.spec.repositories is None:
            self._solution.spec.repositories = []

        existing = [r.name for r in self._solution.spec.repositories]
        if repo.name in existing:
            self._add_error(f"Repository '{repo.name}' already exists in solution.")
            return False, self.get_errors()

        self._solution.spec.repositories.append(repo)
        self.logger.info("Repository added to solution", repo=repo.name)
        return True, []

    def remove_repository(self, name: str) -> Tuple[bool, List[str]]:
        """Remove a repository from the solution by name.

        Args:
            name: Repository name to remove.

        Returns:
            (success, errors)
        """
        if self._solution is None:
            self._add_error("No solution loaded.")
            return False, self.get_errors()

        repos = self._solution.spec.repositories or []
        updated = [r for r in repos if r.name != name]

        if len(updated) == len(repos):
            self._add_error(f"Repository '{name}' not found in solution.")
            return False, self.get_errors()

        self._solution.spec.repositories = updated
        self.logger.info("Repository removed from solution", repo=name)
        return True, []

    # ------------------------------------------------------------------
    # VS Code workspace generation
    # ------------------------------------------------------------------

    def generate_workspace(self) -> Tuple[bool, List[str]]:
        """Write (or overwrite) the VS Code ``.code-workspace`` file.

        The workspace file is written to ``<work_path>/<solution-name>.code-workspace``
        and includes a folder entry for each repository defined in the solution spec.

        Returns:
            (success, errors)
        """
        if self._solution is None:
            self._add_error("No solution loaded — cannot generate workspace.")
            return False, self.get_errors()

        name = self._solution.meta.name
        repos = self._solution.spec.repositories or []

        folders: List[dict] = [{"path": "."}]  # always include the solution root
        for repo in repos:
            folders.append({"path": repo.path, "name": repo.name})

        workspace_data = {
            "folders": folders,
            "settings": {},
        }

        workspace_path = self._work_path / f"{name}{SOLUTION_WORKSPACE_SUFFIX}"
        try:
            workspace_path.write_text(
                json.dumps(workspace_data, indent=2),
                encoding="utf-8",
            )
            self.logger.info("VS Code workspace written", path=str(workspace_path))
            self._add_message(f"Workspace file written: {workspace_path.name}")
            return True, []
        except Exception as e:
            msg = f"Failed to write workspace file: {e}"
            self._add_error(msg)
            return False, self.get_errors()

    # ------------------------------------------------------------------
    # Others (e.g. validation) can be added here as needed
    # ------------------------------------------------------------------

    # Get logging
    def get_logs(
        self,
        work_path: Path,
        lines: int = 50,
        minutes: Optional[int] = None,
        level: Optional[str] = None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> tuple[bool, List[Dict[str, Any]], List[str]]:
        """
        Read and filter execution logs.

        Args:
            work_path: Working directory path
            lines: Number of log lines to return (default: 50)
            minutes: Filter logs from last N minutes
            level: Filter by log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            session_id: Filter by session ID
            execution_id: Filter by execution ID

        Returns:
            Tuple of (success: bool, log_entries: List[dict], errors: List[str])
        """
        errors = []

        try:
            # Resolve log file: active handler → session.json log_path fallback
            log_file = self._resolve_log_file(work_path)

            if not log_file:
                self.logger.debug("No log file found (no active handler and no session.json log_path)")
                return True, [], []

            if not log_file.exists():
                self.logger.debug("Log file not found", extra={"log_file": str(log_file)})
                return True, [], []

            # Read and parse log entries
            log_entries = self._read_log_entries(log_file)

            if not log_entries:
                return True, [], []

            # Apply filters
            log_entries = self._apply_filters(
                log_entries,
                minutes=minutes,
                level=level,
                session_id=session_id,
                execution_id=execution_id,
            )

            # Limit to last N lines (unless filtering by session_id or execution_id)
            if not session_id and not execution_id and len(log_entries) > lines:
                log_entries = log_entries[-lines:]

            self.logger.debug(
                "Retrieved logs",
                extra={
                    "total_entries": len(log_entries),
                    "filters": {
                        "minutes": minutes,
                        "level": level,
                        "session_id": session_id,
                        "execution_id": execution_id,
                    },
                },
            )

            return True, log_entries, []

        except Exception as e:
            error_msg = f"Failed to read logs: {str(e)}"
            self.logger.exception(error_msg)
            errors.append(error_msg)
            return False, [], errors

    # ------------------------------------------------------------------
    # Static path helpers  (no instance needed — safe to call from anywhere)
    # ------------------------------------------------------------------

    @staticmethod
    def get_logging_config_path(work_path: Path) -> Path:
        """Return the path to the solution logging config file."""
        return work_path / SOLUTION_DIR / SOLUTION_LOGGING_FILE

    @staticmethod
    def get_configuration_path(work_path: Path) -> Path:
        """Return the path to the solution configuration file."""
        return work_path / SOLUTION_DIR / SOLUTION_CONFIGURATION_FILE

    @staticmethod
    def get_solution_json_path(work_path: Path) -> Path:
        """Return the path to solution.json."""
        return work_path / SOLUTION_DIR / SOLUTION_FILE

    @staticmethod
    def get_state_dir(work_path: Path) -> Path:
        """Return the path to the solution state directory."""
        return work_path / SOLUTION_DIR

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _solution_path(self) -> Path:
        return SolutionController.get_solution_json_path(self._work_path)

    def _add_error(self, message: str) -> None:
        self.logger.error(message)
        self._errors.append(message)

    def _add_message(self, message: str) -> None:
        self.logger.info(message)
        self._messages.append(message)

    def _resolve_log_file(self, work_path: Path) -> Optional[Path]:
        """
        Resolve the active log file for a session.

        Resolution order:
        1. Active Python logging file handler (fast path)
        2. ``logging.log_path`` from ``.xyz-platform/session.json`` — scans for
           the first ``*.log`` file in that directory
        3. Returns ``None`` if no log file can be found

        Args:
            work_path: Working directory path used to locate session.json

        Returns:
            Path to log file, or None if unavailable
        """
        # 1. Try global logging handler introspection first
        log_file = get_active_log_file()
        if not log_file or log_file == "":
            self.logger.debug("No active log file from logging handlers")
        else:
            log_path = Path(log_file)
            if log_path.exists():
                self.logger.debug("Resolved log file from active logging handler", extra={"log_file": log_file})
                return log_path

        # 2. No further fallback available
        return None

    def _read_log_entries(self, log_file: Path) -> List[Dict[str, Any]]:
        """
        Read and parse JSON log entries from file.

        Args:
            log_file: Path to log file

        Returns:
            List of parsed log entry dictionaries
        """
        entries = []

        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError:
                        # Skip malformed lines
                        self.logger.debug(f"Skipping malformed log line: {line[:100]}")
                        continue
        except Exception as e:
            self.logger.warning(f"Error reading log file: {e}")

        return entries

    def _apply_filters(
        self,
        log_entries: List[Dict[str, Any]],
        minutes: Optional[int] = None,
        level: Optional[str] = None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Apply filters to log entries.

        Args:
            log_entries: List of log entry dictionaries
            minutes: Filter logs from last N minutes
            level: Filter by log level
            session_id: Filter by session ID
            execution_id: Filter by execution ID

        Returns:
            Filtered list of log entries
        """
        filtered = log_entries

        # Filter by time
        if minutes:
            cutoff_time = datetime.now() - timedelta(minutes=minutes)
            filtered = [entry for entry in filtered if self._parse_timestamp(entry) >= cutoff_time]

        # Filter by level
        if level:
            level_upper = level.upper()
            filtered = [entry for entry in filtered if entry.get("level", "").upper() == level_upper]

        # Filter by session_id
        if session_id:
            filtered = [entry for entry in filtered if entry.get("session_id") == session_id]

        # Filter by execution_id
        if execution_id:
            filtered = [entry for entry in filtered if entry.get("execution_id") == execution_id]

        return filtered

    def _parse_timestamp(self, entry: Dict[str, Any]) -> datetime:
        """
        Parse timestamp from log entry.

        Args:
            entry: Log entry dictionary

        Returns:
            Parsed datetime object (returns datetime.min if parsing fails)
        """
        timestamp_str = entry.get("timestamp", "")
        try:
            # Try ISO format first
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            # Make timezone-naive for comparison
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            try:
                # Try common log format
                return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")
            except ValueError:
                # Return very old date if parsing fails
                return datetime.min
