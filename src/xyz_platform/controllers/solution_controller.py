"""Controller for solution lifecycle operations."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from xyz_platform.controllers.base_controller import BaseController
from xyz_platform.logger.logger import get_active_log_file
from xyz_platform.models.solution_model import SolutionModel, SolutionSpecRepositoryModel
from xyz_platform.services.solution_service import SolutionService
from xyz_platform.utils.config import (
    SOLUTION_CONFIG_FILE,
    SOLUTION_CONFIGURATION_FILE,
    SOLUTION_DIR,
    SOLUTION_FILE,
    SOLUTION_GITIGNORE_FILE,
    SOLUTION_LOGGING_FILE,
    SOLUTION_WORKSPACE_SUFFIX,
)
from xyz_platform.utils.system import generate_uuid, get_pkg_templates_path
from xyz_platform.utils.templater import TemplateProcessor


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
        # Store last-execution metadata as CLI-level state (in `cli.yaml`),
        # not inside the solution model. This ensures the values are scoped
        # to the user's CLI preferences rather than the solution resource.
        ts = datetime.now(timezone.utc).isoformat()

        try:
            # Import here to avoid potential circular imports at module import time
            from xyz_platform.controllers.configuration_controller import ConfigurationController

            cfg = ConfigurationController(self._work_path)
            ok1, errs1 = cfg.set_cli_value("last_execution_id", execution_id)
            ok2, errs2 = cfg.set_cli_value("last_execution_on", ts)
            if not ok1 or not ok2:
                errors: List[str] = []
                errors.extend(errs1 or [])
                errors.extend(errs2 or [])
                if not errors:
                    errors = self.get_errors()
                self._add_error("Failed to update cli.yaml with last execution metadata")
                return False, errors

            # Do NOT persist these values into solution.json — they belong to cli.yaml
            self.logger.info(
                "Last execution updated (stored in cli.yaml)",
                execution_id=execution_id,
                last_execution_on=ts,
            )
            return True, []
        except Exception as e:
            self._add_error(f"Failed to persist last execution metadata: {e}")
            return False, self.get_errors()

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

        existing_path = self._solution_path()
        if existing_path.exists():
            ok, errors = self.load()
            if not ok:
                return ok, errors
            if self._solution is not None:
                self._solution.meta.name = name  # type: ignore[assignment]
        else:
            self._solution = SolutionModel(
                apiVersion="platform.huybrechts.xyz/v1",
                kind="solution",
                meta={"name": name},  # type: ignore[arg-type]
                spec={"solution_id": generate_uuid()},  # type: ignore[arg-type]
            )

        ok, errors = self.save()
        if not ok:
            return ok, errors

        ok, errors = self._scaffold_platform_dir()
        if not ok:
            return ok, errors

        ok, errors = self.generate_workspace()
        if not ok:
            return ok, errors

        self._add_message(f"Solution '{name}' initialised at {self._work_path}")
        return True, []

    def clean_solution(
        self,
        work_path: Path,
        dry_run: bool = False,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Clean workspace artifacts without modifying project state.

        Args:
            work_path: Root working directory
            logs: If True (default), delete files in the logs/ folder
            dry_run: If True, report what would be deleted without removing anything

        Returns:
            Tuple[bool, Dict]: Success status and stats dict
        """
        try:
            self._errors.clear()
            self._messages.clear()

            stats: Dict[str, Any] = {
                "logs_deleted": 0,
                "logs_folder": None,
                "dry_run": dry_run,
            }

            # Logs are stored in the solution state directory (`.platform/logs`)
            logs_folder = SolutionController.get_state_dir(work_path) / "logs"
            stats["logs_folder"] = str(logs_folder)
            if logs_folder.exists():
                deleted = 0
                skipped = 0
                for log_file in logs_folder.iterdir():
                    if log_file.is_file():
                        if dry_run:
                            deleted += 1
                        else:
                            try:
                                log_file.unlink()
                                deleted += 1
                            except PermissionError:
                                # File is held open by the logging system; skip it
                                skipped += 1
                                self._messages.append(f"Skipped locked file: {log_file.name}")
                stats["logs_deleted"] = deleted
                stats["logs_skipped"] = skipped
                prefix = "[dry-run] Would delete" if dry_run else "Deleted"
                self.logger.info(
                    f"{'[dry-run] ' if dry_run else ''}Cleaned logs folder: {logs_folder} ({deleted} files{',' if not dry_run else ''}{'' if dry_run else f' deleted, {skipped} skipped'})"
                )
                self._messages.append(
                    f"{prefix} {deleted} log file(s) from {logs_folder}"
                    + (f" ({skipped} skipped — in use)" if skipped and not dry_run else "")
                )
            else:
                self._messages.append(f"Logs folder not found (skipped): {logs_folder}")

            return True, stats

        except Exception as e:
            error_msg = f"Failed to clean project: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False, {}

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

    def get_repositories(self, name: Optional[str] = None) -> Tuple[List[SolutionSpecRepositoryModel], List[str]]:
        """Return repositories from the loaded solution, optionally filtered by name.

        Args:
            name: If given, return only the repository with this name.
                  Returns an error if no match is found.

        Returns:
            ``(repos, errors)`` — *repos* is an empty list on error.
        """
        if self._solution is None:
            return [], ["No solution loaded."]

        repos = self._solution.spec.repositories or []

        if name:
            repos = [r for r in repos if str(r.name) == name]
            if not repos:
                return [], [f"Repository '{name}' not found in solution."]

        return list(repos), []

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

            # Create a small `.vscode/` scaffold. Prefer rendering templates from
            # package templates/vscode/*.template.* when available; fall
            # back to the inline scaffold if templates are missing.
            try:
                vscode_dir = self._work_path / ".vscode"
                vscode_dir.mkdir(parents=True, exist_ok=True)

                templates_vscode = get_pkg_templates_path() / "vscode"
                if templates_vscode.exists() and templates_vscode.is_dir():
                    # Render template files into the workspace .vscode directory.
                    processor = TemplateProcessor(templates_vscode, cleanup_templates=False)
                    # Temporarily expose solution name to the template processor
                    prev = os.environ.get("SOLUTION_NAME")
                    os.environ["SOLUTION_NAME"] = name
                    try:
                        for tpl in templates_vscode.iterdir():
                            if not tpl.is_file() or ".template." not in tpl.name:
                                continue
                            try:
                                content = tpl.read_text(encoding="utf-8")
                                processed = processor._substitute_environment_variables(content)
                                out_name = tpl.name.replace(".template.", ".")
                                out_path = vscode_dir / out_name
                                if not out_path.exists():
                                    out_path.write_text(processed, encoding="utf-8")
                                    self._add_message(f"Created: {out_path.relative_to(self._work_path)}")
                                else:
                                    self.logger.debug(".vscode template output exists — skipping", path=str(out_path))
                            except Exception as e:
                                self.logger.warning(
                                    "Failed to render vscode template", extra={"template": str(tpl), "error": str(e)}
                                )
                    finally:
                        # restore environment
                        if prev is None:
                            del os.environ["SOLUTION_NAME"]
                        else:
                            os.environ["SOLUTION_NAME"] = prev
                else:
                    # No templates available; fall back to inline scaffolding (non-destructive)
                    extensions = {
                        "recommendations": [
                            "ms-python.python",
                            "ms-python.vscode-pylance",
                            "charliermarsh.ruff",
                            "ms-vscode.powershell",
                            "ms-vscode-remote.remote-containers",
                            "redhat.vscode-yaml",
                            "hashicorp.terraform",
                            "streetsidesoftware.code-spell-checker",
                            "eamodio.gitlens",
                            "vscode-icons-team.vscode-icons",
                            "EditorConfig.EditorConfig",
                        ],
                        "unwantedRecommendations": [
                            "ms-python.black-formatter",
                            "ms-python.isort",
                        ],
                    }
                    ext_file = vscode_dir / "extensions.json"
                    if not ext_file.exists():
                        ext_file.write_text(json.dumps(extensions, indent=2), encoding="utf-8")
                        self._add_message(f"Created: {ext_file.relative_to(self._work_path)}")
                    else:
                        self.logger.debug(".vscode/extensions.json already exists — skipping", path=str(ext_file))

                    settings = {
                        "[python]": {
                            "editor.insertSpaces": True,
                            "editor.tabSize": 4,
                            "editor.defaultFormatter": "charliermarsh.ruff",
                            "editor.formatOnSave": True,
                            "editor.codeActionsOnSave": {
                                "source.fixAll.ruff": "explicit",
                                "source.organizeImports.ruff": "explicit",
                            },
                        },
                        "[yaml]": {
                            "editor.insertSpaces": True,
                            "editor.tabSize": 2,
                            "editor.defaultFormatter": "redhat.vscode-yaml",
                            "editor.formatOnSave": True,
                        },
                        "python.testing.pytestEnabled": True,
                        "python.testing.pytestArgs": ["tests"],
                        "python.envFile": "${workspaceFolder}/.env",
                        "python.testing.cwd": "${workspaceFolder}",
                        "editor.formatOnSave": True,
                        "files.eol": "\n",
                        "files.exclude": {
                            "**/.git": True,
                            ".ruff_cache": True,
                            ".mypy_cache": True,
                            ".nox": True,
                            ".pytest_cache": True,
                            ".coverage": True,
                        },
                    }
                    settings_file = vscode_dir / "settings.json"
                    if not settings_file.exists():
                        settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
                        self._add_message(f"Created: {settings_file.relative_to(self._work_path)}")
                    else:
                        self.logger.debug(".vscode/settings.json already exists — skipping", path=str(settings_file))

                    launch = {
                        "version": "0.2.0",
                        "configurations": [
                            {
                                "name": f"Run: {name}",
                                "type": "debugpy",
                                "request": "launch",
                                "program": "${workspaceFolder}/src/xyz_platform/__main__.py",
                                "args": "${input:cliArgs}",
                                "cwd": "${workspaceFolder}",
                                "env": {"PYTHONPATH": "${workspaceFolder}/src"},
                                "console": "integratedTerminal",
                                "justMyCode": True,
                            },
                            {
                                "name": f"Debug: {name}",
                                "type": "debugpy",
                                "request": "launch",
                                "program": "${workspaceFolder}/src/xyz_platform/__main__.py",
                                "args": "${input:cliArgs}",
                                "cwd": "${workspaceFolder}",
                                "env": {"PYTHONPATH": "${workspaceFolder}/src"},
                                "console": "integratedTerminal",
                                "justMyCode": False,
                            },
                        ],
                        "inputs": [
                            {
                                "id": "cliArgs",
                                "type": "promptString",
                                "description": "CLI arguments (e.g. --help, version, deploy platform.yaml)",
                                "default": "--help",
                            }
                        ],
                    }
                    launch_file = vscode_dir / "launch.json"
                    if not launch_file.exists():
                        launch_file.write_text(json.dumps(launch, indent=2), encoding="utf-8")
                        self._add_message(f"Created: {launch_file.relative_to(self._work_path)}")
                    else:
                        self.logger.debug(".vscode/launch.json already exists — skipping", path=str(launch_file))

                    tasks = {
                        "version": "2.0.0",
                        "tasks": [
                            {
                                "label": f"Run: {name}",
                                "type": "shell",
                                "command": "uv run xyz-platform ${input:cliArgs}",
                                "group": "build",
                                "presentation": {
                                    "echo": True,
                                    "reveal": "always",
                                    "focus": True,
                                    "panel": "shared",
                                    "clear": True,
                                },
                                "problemMatcher": [],
                            },
                            {
                                "label": "Check: lint + format + types",
                                "type": "shell",
                                "command": "scripts/Check.ps1",
                                "options": {"shell": {"executable": "pwsh", "args": ["-NoProfile", "-File"]}},
                                "group": "build",
                                "presentation": {
                                    "echo": True,
                                    "reveal": "always",
                                    "focus": True,
                                    "panel": "shared",
                                    "clear": True,
                                },
                                "problemMatcher": [],
                            },
                        ],
                        "inputs": [
                            {
                                "id": "cliArgs",
                                "type": "promptString",
                                "description": "CLI arguments (e.g. version, deploy platform.yaml)",
                                "default": "version",
                            }
                        ],
                    }
                    tasks_file = vscode_dir / "tasks.json"
                    if not tasks_file.exists():
                        tasks_file.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
                        self._add_message(f"Created: {tasks_file.relative_to(self._work_path)}")
                    else:
                        self.logger.debug(".vscode/tasks.json already exists — skipping", path=str(tasks_file))

                    readme_file = vscode_dir / "README.md"
                    if not readme_file.exists():
                        readme_file.write_text(
                            "# VS Code workspace settings\n\nThis folder contains recommended extensions, settings, tasks and launch configurations for this workspace.\n",
                            encoding="utf-8",
                        )
                        self._add_message(f"Created: {readme_file.relative_to(self._work_path)}")
                    else:
                        self.logger.debug(".vscode/README.md already exists — skipping", path=str(readme_file))
            except Exception as e:
                self.logger.warning("Failed to write .vscode scaffolding", extra={"error": str(e)})

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
    # Scaffold
    # ------------------------------------------------------------------

    def _scaffold_platform_dir(self) -> Tuple[bool, List[str]]:
        """Write scaffold files into ``.platform/`` that don't exist yet.

        Files written (skipped if already present — idempotent):
        - logging.yaml  — dev logging profile (console + rotating file)
        - cli.yaml      — CLI preferences stub
        - .gitignore    — what to commit vs. ignore
        - logs/         — log output directory (empty)
        """
        state_dir = SolutionController.get_state_dir(self._work_path)
        templates_dir = get_pkg_templates_path() / "solution"

        scaffold = [
            (SOLUTION_LOGGING_FILE, "logging.yaml"),
            (SOLUTION_CONFIG_FILE, SOLUTION_CONFIG_FILE),
            (SOLUTION_GITIGNORE_FILE, ".gitignore"),
        ]

        for dest_name, src_name in scaffold:
            dest = state_dir / dest_name
            if dest.exists():
                self.logger.debug("Scaffold file already exists — skipping", path=str(dest))
                continue
            src = templates_dir / src_name
            if not src.exists():
                self.logger.warning("Scaffold template not found", template=str(src))
                continue
            try:
                content = src.read_text(encoding="utf-8")
                if dest_name == SOLUTION_LOGGING_FILE:
                    log_file = (state_dir / "logs" / "application.json").as_posix()
                    content = content.replace(".platform/logs/application.json", log_file)
                dest.write_text(content, encoding="utf-8")
                self.logger.info("Scaffold file written", path=str(dest))
                self._add_message(f"Created: {dest.relative_to(self._work_path)}")
            except Exception as e:
                msg = f"Failed to write scaffold file {dest_name}: {e}"
                self._add_error(msg)
                return False, self.get_errors()

        # Ensure logs directory exists
        logs_dir = state_dir / "logs"
        logs_dir.mkdir(exist_ok=True)

        # Copy integration help templates from templates/integrations/
        integrations_src = get_pkg_templates_path() / "integrations"
        if integrations_src.exists() and integrations_src.is_dir():
            integrations_dest = state_dir / "integrations"
            integrations_dest.mkdir(exist_ok=True)
            for src_file in integrations_src.iterdir():
                if not src_file.is_file():
                    continue
                dest_file = integrations_dest / src_file.name
                if dest_file.exists():
                    self.logger.debug("Integration template already exists — skipping", path=str(dest_file))
                    continue
                try:
                    dest_file.write_text(src_file.read_text(encoding="utf-8"), encoding="utf-8")
                    self.logger.info("Integration template written", path=str(dest_file))
                    self._add_message(f"Created: {dest_file.relative_to(self._work_path)}")
                except Exception as e:
                    msg = f"Failed to write integration template {src_file.name}: {e}"
                    self._add_error(msg)
                    return False, self.get_errors()

        # Copy workspace README from templates/solution/README.md
        readme_src = templates_dir / "README.md"
        dest_readme = state_dir / "README.md"
        if readme_src.exists() and not dest_readme.exists():
            try:
                dest_readme.write_text(readme_src.read_text(encoding="utf-8"), encoding="utf-8")
                self.logger.info("Workspace README written", path=str(dest_readme))
                self._add_message(f"Created: {dest_readme.relative_to(self._work_path)}")
            except Exception as e:
                msg = f"Failed to write workspace README: {e}"
                self._add_error(msg)
                return False, self.get_errors()

        return True, []

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
