#!/usr/bin/env python3
"""
===============================================================================
Script Name   : session_controller.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Controller for managing XYZ Platform sessions.
===============================================================================
"""

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from xyz_platform.logger.logger import get_logger, get_active_log_file
from xyz_platform.utils.system import generate_uuid7


class SessionController:
    """
    Controller for managing XYZ Platform sessions.

    Handles session initialization, state management, and workspace operations.
    This is a stateless controller - it does not maintain session state between calls.
    """

    @staticmethod
    def _session_folder_path(work_path: Path) -> Path:
        """Return the .xyz-platform folder path for a given work_path."""
        return work_path / ".xyz-platform"

    @staticmethod
    def _session_file_path(work_path: Path) -> Path:
        """Return the session.json file path for a given work_path."""
        return work_path / ".xyz-platform" / "session.json"

    # Initialize the controller with empty error and message lists
    def __init__(self):
        """Initialize the session controller."""
        self.logger = get_logger(self.__class__.__module__)
        self._errors: List[str] = []
        self._messages: List[str] = []
        # In-memory session state — loaded once at command start, saved once at end
        self._session_data: Optional[Dict] = None
        self._session_file: Optional[Path] = None

    # Error and message handling methods

    def has_errors(self) -> bool:
        """Check if any errors were accumulated."""
        return len(self._errors) > 0

    def get_errors(self) -> List[str]:
        """Get accumulated errors."""
        return self._errors.copy()

    def clear_errors(self) -> None:
        """Clear accumulated errors."""
        self._errors.clear()

    def get_messages(self) -> List[str]:
        """Get accumulated messages."""
        return self._messages.copy()

    def clear_messages(self) -> None:
        """Clear accumulated messages."""
        self._messages.clear()

    # Session in-memory load / save

    def load_session(self, work_path: Path) -> bool:
        """
        Load session.json into memory.

        Called once at the start of every command via BaseCommand._initialize().
        Silent no-op when the file does not exist yet (e.g. before ``session init``).

        Args:
            work_path: Working directory containing .xyz-platform/session.json

        Returns:
            bool: True if loaded successfully, False if file missing or unreadable
        """
        try:
            session_file = self._session_file_path(work_path)
            if not session_file.exists():
                return False
            with open(session_file, "r", encoding="utf-8") as f:
                self._session_data = json.load(f)
            self._session_file = session_file
            self.logger.debug(
                "Session loaded into memory",
                extra={"session_file": str(session_file)},
            )
            return True
        except Exception as e:
            self.logger.debug(f"Could not load session: {e}")
            return False

    def save_session(self) -> bool:
        """
        Write in-memory session data back to session.json.

        Called once at the end of every command via BaseCommand._finalize().
        No-op when session was never loaded (e.g. command ran before ``session init``).

        Returns:
            bool: True if saved successfully, False otherwise
        """
        if self._session_data is None or self._session_file is None:
            return False
        try:
            with open(self._session_file, "w", encoding="utf-8") as f:
                json.dump(self._session_data, f, indent=2)
            self.logger.debug(
                "Session saved to disk",
                extra={"session_file": str(self._session_file)},
            )
            return True
        except Exception as e:
            self.logger.debug(f"Could not save session: {e}")
            return False

    def get_session_id(self) -> Optional[str]:
        """Return the session_id from in-memory session data, or None if not loaded."""
        if self._session_data is None:
            return None
        return self._session_data.get("session", {}).get("session_id")

    def get_session_data(self) -> Optional[Dict]:
        """Return the in-memory session data dict, or None if not loaded."""
        return self._session_data

    def get_repositories(self) -> list:
        """Return the repositories list from in-memory session data, or [] if not loaded."""
        if self._session_data is None:
            return []
        return self._session_data.get("repositories", [])

    def get_session_status(self, work_path: Path) -> List[Dict]:
        """
        Check on-disk status of each registered repository.

        For each repository compares what is registered in session.json against
        what is actually on disk.  Git repos also report their current branch.

        Args:
            work_path: Root working directory

        Returns:
            List[Dict]: One entry per repository with keys:
                name, type, url, registered_branch, folder_exists,
                current_branch, branch_match, status
                (status is 'ok', 'missing', or 'branch_mismatch')
        """
        import subprocess

        results = []
        for repo in self.get_repositories():
            name = repo.get("name", "")
            registered_branch = repo.get("branch")
            repo_path = work_path / name
            folder_exists = repo_path.exists()
            current_branch = None
            branch_match = None

            if folder_exists and (repo_path / ".git").exists():
                try:
                    result = subprocess.run(
                        ["git", "branch", "--show-current"],
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        current_branch = result.stdout.strip() or None
                        if registered_branch and current_branch:
                            branch_match = current_branch == registered_branch
                except Exception:
                    pass

            if not folder_exists:
                status = "missing"
            elif branch_match is False:
                status = "branch_mismatch"
            else:
                status = "ok"

            results.append(
                {
                    "name": name,
                    "type": repo.get("type"),
                    "url": repo.get("url"),
                    "registered_branch": registered_branch,
                    "folder_exists": folder_exists,
                    "current_branch": current_branch,
                    "branch_match": branch_match,
                    "status": status,
                }
            )
        return results

    # Session initialization methods

    def initialize_session(
        self,
        workspace_name: str,
        work_path: Path,
        editor: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Path]]:
        """
        Initialize a new session workspace.

        Args:
            workspace_name: Name of the workspace
            work_path: Root working directory path

        Returns:
            Tuple[bool, Dict[str, Path]]: Success status and dict of created paths
                {
                    "session_folder": Path,
                    "session_file": Path,
                    "workspace_file": Path (or None if skipped)
                }
        """
        try:
            self._errors.clear()
            self._messages.clear()

            # Define paths
            session_folder = self._session_folder_path(work_path)
            session_file = self._session_file_path(work_path)
            workspace_file = work_path / f"{workspace_name}.code-workspace"

            created_paths = {
                "session_folder": None,
                "session_file": None,
                "workspace_file": None,
            }

            # Validate work path
            if not self._validate_work_path(work_path):
                return False, created_paths

            # Check existing files
            self._check_existing_files(workspace_file, session_folder)

            # Create session folder
            if not self._create_session_folder(session_folder):
                return False, created_paths
            created_paths["session_folder"] = session_folder

            # Create logs folder in workspace root
            logs_folder = work_path / "logs"
            self.logger.info(f"Creating logs folder: {logs_folder}")
            logs_folder.mkdir(parents=True, exist_ok=True)

            # Create workspace file only when VS Code editor integration is requested
            if editor and editor.lower() == "vscode":
                workspace_created = self._create_workspace_file(
                    workspace_file, workspace_name
                )
                if workspace_created:
                    created_paths["workspace_file"] = workspace_file
                else:
                    self._messages.append(
                        f"Skipped workspace file creation (already exists): {workspace_file}"
                    )

            # Create logging configuration
            logging_config_path = session_folder / "logging.yaml"
            if not self._create_logging_config(logging_config_path, work_path):
                return False, created_paths
            created_paths["logging_config"] = logging_config_path

            # Create session file
            if not self._create_session_file(
                session_file, workspace_name, work_path, logging_config_path
            ):
                return False, created_paths
            created_paths["session_file"] = session_file

            self._messages.append(
                f"Session workspace '{workspace_name}' initialized successfully"
            )

            return True, created_paths

        except Exception as e:
            error_msg = f"Failed to initialize session: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False, {}

    def add_repository(
        self,
        name: str,
        url: str,
        work_path: Path,
        repo_type: Optional[str] = None,
        branch: str = "main",
        integrations: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Dict[str, str]]:
        """
        Add a repository to the session workspace.

        Args:
            name: Repository name (folder name)
            url: Repository URL or local path
            work_path: Root working directory
            repo_type: Repository type (git, local, archive) - auto-detected if None
            branch: Git branch to clone (default: main)
            integrations: Resolved integration instances keyed by integration name

        Returns:
            Tuple[bool, Dict]: Success status and repository metadata
        """
        try:
            self._errors.clear()
            self._messages.clear()

            # Auto-detect repository type if not provided
            if not repo_type:
                repo_type = self._detect_repo_type(url, work_path)

            self.logger.info(
                f"Adding repository '{name}' from '{url}' (type: {repo_type})"
            )

            # Define repository path
            repo_path = work_path / name

            # Handle repository based on type
            if repo_type == "git":
                git_integration = integrations.get("git") if integrations else None
                if not self._clone_git_repository(
                    url, repo_path, branch, git_integration
                ):
                    return False, {}
            elif repo_type == "local":
                source_path = self._resolve_local_source_path(url, work_path)
                if not self._copy_local_repository(source_path, repo_path):
                    return False, {}
            elif repo_type == "archive":
                error_msg = "Archive repository type not yet implemented"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False, {}
            else:
                error_msg = f"Unknown repository type: {repo_type}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False, {}

            # Create repository metadata
            repo_metadata = {
                "name": name,
                "url": url,
                "path": name,
                "type": repo_type,
                "branch": branch if repo_type == "git" else None,
            }

            # Update session.json
            if not self._update_session_repositories(work_path, repo_metadata):
                return False, {}

            # Update VSCode workspace (optional)
            self._add_to_vscode_workspace(work_path, name)

            self._messages.append(f"Repository '{name}' added successfully")

            return True, repo_metadata

        except Exception as e:
            error_msg = f"Failed to add repository: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False, {}

    def get_required_integrations_for_add_repository(
        self,
        url: str,
        repo_type: Optional[str] = None,
        work_path: Optional[Path] = None,
    ) -> Dict[str, str]:
        """
        Determine required integrations for add-repository operation.

        Args:
            url: Repository URL or local path
            repo_type: Repository type (git, local, archive) - auto-detected if None
            work_path: Root working directory used to resolve relative local paths

        Returns:
            Dict[str, str]: Required integrations mapped to operation descriptions
        """
        detected_type = repo_type or self._detect_repo_type(url, work_path)

        if detected_type == "git":
            return {"git": "repository clone operations"}

        return {}

    def update_last_execution(self, execution_id: str) -> bool:
        """
        Update last_execution_id in the in-memory session data.

        The caller is responsible for persisting via save_session().

        Args:
            execution_id: Execution ID of the completed command

        Returns:
            bool: True if updated, False if session not loaded
        """
        if self._session_data is None:
            return False
        self._session_data.setdefault("session", {})["last_execution_id"] = execution_id
        return True

    def remove_repository(
        self,
        name: str,
        work_path: Path,
        delete_folder: bool = False,
        dry_run: bool = False,
    ) -> Tuple[bool, Dict[str, str]]:
        """
        Remove a repository from the session.

        Removes the entry from in-memory repositories[] (save_session() persists it).
        Optionally deletes the repository folder from disk.

        Args:
            name: Repository name to remove
            work_path: Root working directory
            delete_folder: If True, also delete the repository folder on disk
            dry_run: If True, report what would happen without making any changes

        Returns:
            Tuple[bool, Dict]: Success status and removed repository metadata
        """
        try:
            self._errors.clear()
            self._messages.clear()

            if self._session_data is None:
                error_msg = "Session data not loaded — call load_session() first"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False, {}

            repositories = self._session_data.get("repositories", [])
            repo_metadata = next((r for r in repositories if r["name"] == name), None)

            if repo_metadata is None:
                error_msg = f"Repository '{name}' not found in session"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False, {}

            if dry_run:
                self._messages.append(
                    f"[dry-run] Would remove repository '{name}' from session"
                )
                if delete_folder:
                    repo_path = work_path / name
                    if repo_path.exists():
                        self._messages.append(
                            f"[dry-run] Would delete folder: {repo_path}"
                        )
                    else:
                        self._messages.append(
                            f"[dry-run] Folder not found on disk (would skip): {repo_path}"
                        )
                return True, dict(repo_metadata)

            # Remove from in-memory list
            self._session_data["repositories"] = [
                r for r in repositories if r["name"] != name
            ]
            self._messages.append(f"Removed repository '{name}' from session")

            # Optionally delete the folder
            if delete_folder:
                repo_path = work_path / name
                if repo_path.exists():
                    shutil.rmtree(repo_path)
                    self.logger.info(f"Deleted repository folder: {repo_path}")
                    self._messages.append(f"Deleted folder: {repo_path}")
                else:
                    self._messages.append(
                        f"Folder not found on disk (skipped): {repo_path}"
                    )

            return True, repo_metadata

        except Exception as e:
            error_msg = f"Failed to remove repository: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False, {}

    def clean_session(
        self,
        work_path: Path,
        logs: bool = True,
        dry_run: bool = False,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Clean workspace artifacts without modifying session state.

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

            if logs:
                logs_folder = work_path / "logs"
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
                                    self._messages.append(
                                        f"Skipped locked file: {log_file.name}"
                                    )
                    stats["logs_deleted"] = deleted
                    stats["logs_skipped"] = skipped
                    prefix = "[dry-run] Would delete" if dry_run else "Deleted"
                    self.logger.info(
                        f"{'[dry-run] ' if dry_run else ''}Cleaned logs folder: {logs_folder} ({deleted} files{',' if not dry_run else ''}{'' if dry_run else f' deleted, {skipped} skipped'})"
                    )
                    self._messages.append(
                        f"{prefix} {deleted} log file(s) from {logs_folder}"
                        + (
                            f" ({skipped} skipped — in use)"
                            if skipped and not dry_run
                            else ""
                        )
                    )
                else:
                    self._messages.append(
                        f"Logs folder not found (skipped): {logs_folder}"
                    )

            return True, stats

        except Exception as e:
            error_msg = f"Failed to clean session: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False, {}

    def _validate_work_path(self, work_path: Path) -> bool:
        """
        Validate that work path exists and is a directory.

        Args:
            work_path: Path to validate

        Returns:
            bool: True if valid, False otherwise
        """
        if not work_path.exists():
            error_msg = f"Work path does not exist: {work_path}"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        if not work_path.is_dir():
            error_msg = f"Work path is not a directory: {work_path}"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        return True

    def _check_existing_files(self, workspace_file: Path, session_folder: Path) -> None:
        """
        Check for existing workspace files and log warnings.

        Args:
            workspace_file: Path to workspace file
            session_folder: Path to session folder
        """
        # Check for any existing .code-workspace files
        existing_workspaces = list(workspace_file.parent.glob("*.code-workspace"))
        if existing_workspaces:
            for ws_file in existing_workspaces:
                warning_msg = f"Found existing workspace file: {ws_file.name}"
                self.logger.info(warning_msg)
                self._messages.append(warning_msg)

        # Check if .xyz-platform folder already exists
        if session_folder.exists():
            warning_msg = f".xyz-platform folder already exists: {session_folder}"
            self.logger.info(warning_msg)
            self._messages.append(warning_msg)

    def _create_session_folder(self, session_folder: Path) -> bool:
        """
        Create .xyz-platform folder.

        Args:
            session_folder: Path to session folder

        Returns:
            bool: Success status
        """
        try:
            self.logger.info(f"Creating .xyz-platform folder: {session_folder}")
            session_folder.mkdir(parents=True, exist_ok=True)
            self._messages.append(f"Created .xyz-platform folder: {session_folder}")
            return True

        except Exception as e:
            error_msg = f"Failed to create session folder: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _create_workspace_file(self, workspace_file: Path, workspace_name: str) -> bool:
        """
        Create VSCode workspace file from template.
        Skips creation if file already exists.

        Args:
            workspace_file: Path to workspace file
            workspace_name: Name of the workspace

        Returns:
            bool: True if created, False if skipped or error
        """
        try:
            # Skip if already exists
            if workspace_file.exists():
                self.logger.info(
                    f"Workspace file already exists, skipping: {workspace_file}"
                )
                return False

            # Load template
            template_path = self._get_template_path("workspace.template.json")
            if not template_path.exists():
                error_msg = f"Workspace template not found: {template_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Read and process template
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()

            # Replace placeholders
            workspace_content = template_content.replace(
                "{{workspace_name}}", workspace_name
            )

            # Parse as JSON to validate
            workspace_data = json.loads(workspace_content)

            # Write workspace file
            self.logger.info(f"Creating VSCode workspace file: {workspace_file}")
            with open(workspace_file, "w", encoding="utf-8") as f:
                json.dump(workspace_data, f, indent=2)

            self._messages.append(f"Created VSCode workspace file: {workspace_file}")
            return True

        except Exception as e:
            error_msg = f"Failed to create workspace file: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _create_logging_config(
        self, logging_config_path: Path, work_path: Path
    ) -> bool:
        """
        Create logging.yaml configuration file from template.
        Updates paths to be workspace-specific.

        Args:
            logging_config_path: Path to logging config file (.xyz-platform/logging.yaml)
            work_path: Root working directory

        Returns:
            bool: Success status
        """
        try:
            # Load template
            template_path = self._get_template_path("logging.yaml")
            if not template_path.exists():
                error_msg = f"Logging template not found: {template_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Read template as YAML
            with open(template_path, "r", encoding="utf-8") as f:
                logging_config = yaml.safe_load(f)

            # Update file handler path to be workspace-specific
            if "handlers" in logging_config and "file" in logging_config["handlers"]:
                # Update to absolute path in workspace root
                log_file_path = work_path / "logs" / "platform.json"
                logging_config["handlers"]["file"]["filename"] = str(log_file_path)

            # Write updated logging config
            self.logger.info(f"Creating logging configuration: {logging_config_path}")
            with open(logging_config_path, "w", encoding="utf-8") as f:
                yaml.dump(logging_config, f, default_flow_style=False, sort_keys=False)

            self._messages.append(
                f"Created logging configuration: {logging_config_path}"
            )
            return True

        except Exception as e:
            error_msg = f"Failed to create logging configuration: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _create_session_file(
        self,
        session_file: Path,
        workspace_name: str,
        work_path: Path,
        logging_config_path: Path,
    ) -> bool:
        """
        Create session.json state file from template.

        Args:
            session_file: Path to session file
            workspace_name: Name of the workspace
            work_path: Root working directory
            logging_config_path: Path to logging configuration file

        Returns:
            bool: Success status
        """
        try:
            # Load template
            template_path = self._get_template_path("session.template.json")
            if not template_path.exists():
                error_msg = f"Session template not found: {template_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Read and process template
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()

            # Define log path
            log_path = work_path / "logs"

            # Replace placeholders (use as_posix() to avoid backslash issues in JSON on Windows)
            session_content = (
                template_content.replace("{{session_id}}", generate_uuid7())
                .replace("{{workspace_name}}", workspace_name)
                .replace("{{created_timestamp}}", datetime.now().isoformat())
                .replace("{{work_path}}", work_path.absolute().as_posix())
                .replace(
                    "{{logging_config_path}}", logging_config_path.absolute().as_posix()
                )
                .replace("{{log_path}}", log_path.absolute().as_posix())
            )

            # Parse as JSON to validate
            session_data = json.loads(session_content)

            # Write session file
            self.logger.info(f"Creating session state file: {session_file}")
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2)

            # Load into memory so subsequent calls in this run use in-memory data
            self._session_data = session_data
            self._session_file = session_file

            self._messages.append(f"Created session state file: {session_file}")
            return True

        except Exception as e:
            error_msg = f"Failed to create session file: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _detect_repo_type(self, url: str, work_path: Optional[Path] = None) -> str:
        """
        Auto-detect repository type from URL.

        Args:
            url: Repository URL or path
            work_path: Root working directory for resolving relative local paths

        Returns:
            str: Repository type (git, local, or archive)
        """
        url_lower = url.lower()

        # Check for git patterns
        git_patterns = [
            "https://github.com",
            "https://gitlab.com",
            "https://bitbucket.org",
            "git@",
            ".git",
        ]
        if any(pattern in url_lower for pattern in git_patterns):
            return "git"

        # Check for archive patterns
        archive_patterns = [".zip", ".tar.gz", ".tar.bz2", ".tgz"]
        if any(url_lower.endswith(pattern) for pattern in archive_patterns):
            return "archive"

        # Check if local path exists
        local_path = self._resolve_local_source_path(url, work_path)
        if local_path.exists():
            return "local"

        # Default to git if no pattern matches
        self.logger.warning(
            f"Could not detect repository type for '{url}', defaulting to 'git'"
        )
        return "git"

    def _resolve_local_source_path(
        self, url: str, work_path: Optional[Path] = None
    ) -> Path:
        """
        Resolve local source path using work_path for relative URLs.

        Args:
            url: Local repository URL/path provided by user
            work_path: Root working directory for relative path resolution

        Returns:
            Path: Resolved source path
        """
        source_path = Path(url)
        if source_path.is_absolute():
            return source_path

        if work_path is not None:
            return work_path / source_path

        return source_path

    def _clone_git_repository(
        self, url: str, repo_path: Path, branch: str, git_integration
    ) -> bool:
        """
        Clone a git repository using GitIntegration.

        Args:
            url: Git repository URL
            repo_path: Destination path for repository
            branch: Branch to clone
            git_integration: GitIntegration instance from command

        Returns:
            bool: Success status
        """
        try:
            if repo_path.exists():
                error_msg = f"Repository path already exists: {repo_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            if not git_integration:
                error_msg = "Git integration not provided"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            self.logger.info(f"Cloning git repository from '{url}' (branch: {branch})")

            # Execute git clone via integration
            result = git_integration.clone(
                repo_url=url,
                target_dir=str(repo_path),
                branch=branch,
                depth=0,  # Full clone (not shallow)
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                error_msg = (
                    f"Git clone failed: {result.stderr.strip() or 'Unknown error'}"
                )
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            self._messages.append(f"Cloned git repository to {repo_path}")
            return True

        except RuntimeError as e:
            # GitIntegration raises RuntimeError on failure
            error_msg = f"Git clone failed: {str(e)}"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False
        except Exception as e:
            error_msg = f"Failed to clone git repository: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _copy_local_repository(self, source_path: Path, repo_path: Path) -> bool:
        """
        Copy a local repository to the workspace.

        Args:
            source_path: Local repository source path
            repo_path: Destination path for repository

        Returns:
            bool: Success status
        """
        try:
            if not source_path.exists():
                error_msg = f"Local repository not found: {source_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            if repo_path.exists():
                error_msg = f"Repository path already exists: {repo_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            if not source_path.is_dir():
                error_msg = f"Local repository is not a directory: {source_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            self.logger.info(f"Copying local repository from '{source_path}'")

            # Copy directory tree
            shutil.copytree(source_path, repo_path)

            self._messages.append(f"Copied local repository to {repo_path}")
            return True

        except Exception as e:
            error_msg = f"Failed to copy local repository: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _update_session_repositories(
        self, work_path: Path, repo_metadata: Dict[str, str]
    ) -> bool:
        """
        Update session.json with new repository metadata.

        Args:
            work_path: Root working directory
            repo_metadata: Repository metadata dictionary

        Returns:
            bool: Success status
        """
        try:
            if self._session_data is None:
                error_msg = "Session data not loaded — call load_session() first"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Check if repository already exists
            existing_repos = self._session_data.get("repositories", [])
            if any(repo["name"] == repo_metadata["name"] for repo in existing_repos):
                error_msg = (
                    f"Repository '{repo_metadata['name']}' already exists in session"
                )
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Add repository to in-memory list (save_session() will persist it)
            existing_repos.append(repo_metadata)
            self._session_data["repositories"] = existing_repos

            self._messages.append(
                f"Updated session with repository '{repo_metadata['name']}'"
            )
            return True

        except Exception as e:
            error_msg = f"Failed to update session repositories: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _add_to_vscode_workspace(self, work_path: Path, repo_name: str) -> bool:
        """
        Add repository folder to VSCode workspace (optional).

        Args:
            work_path: Root working directory
            repo_name: Repository name (folder name)

        Returns:
            bool: Success status (True if workspace updated or doesn't exist)
        """
        try:
            workspace_file = work_path / f"{work_path.name}.code-workspace"

            if not workspace_file.exists():
                self.logger.debug(
                    f"Workspace file not found, skipping: {workspace_file}"
                )
                return True

            # Read existing workspace data
            with open(workspace_file, "r", encoding="utf-8") as f:
                workspace_data = json.load(f)

            # Check if repository already in workspace
            folders = workspace_data.get("folders", [])
            if any(folder.get("path") == repo_name for folder in folders):
                self.logger.debug(f"Repository '{repo_name}' already in workspace")
                return True

            # Add repository folder
            folders.append({"path": repo_name})
            workspace_data["folders"] = folders

            # Write updated workspace data
            self.logger.info(f"Updating workspace file: {workspace_file}")
            with open(workspace_file, "w", encoding="utf-8") as f:
                json.dump(workspace_data, f, indent=2)

            self._messages.append(f"Added '{repo_name}' to VSCode workspace")
            return True

        except Exception as e:
            # Workspace update is optional, log warning but don't fail
            self.logger.warning(f"Failed to update VSCode workspace: {str(e)}")
            return True

    def _get_template_path(self, template_name: str) -> Path:
        """
        Get path to template file in data directory.

        Args:
            template_name: Name of template file

        Returns:
            Path: Absolute path to template file
        """
        # Get the package data directory
        package_dir = Path(__file__).parent.parent
        template_path = package_dir / "data" / template_name
        return template_path

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
        if log_file and log_file.exists():
            return log_file

        # 2. Fall back to session.json log_path
        try:
            # Use in-memory data if already loaded, otherwise read from file
            if self._session_data is not None:
                session_data = self._session_data
            else:
                session_file = self._session_file_path(work_path)
                if not session_file.exists():
                    return log_file
                with open(session_file, "r", encoding="utf-8") as f:
                    session_data = json.load(f)
            log_path_str = session_data.get("logging", {}).get("log_path")
            if log_path_str:
                log_path = Path(log_path_str)
                if log_path.exists():
                    candidates = sorted(log_path.glob("*.log"))
                    if candidates:
                        self.logger.debug(
                            "Resolved log file from session.json",
                            extra={"log_file": str(candidates[0])},
                        )
                        return candidates[0]
        except Exception as e:
            self.logger.debug(f"Could not resolve log path from session.json: {e}")

        # 3. Return original result (may be None or a non-existent path)
        return log_file

    # Get logs with filtering options

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
                self.logger.debug(
                    "No log file found (no active handler and no session.json log_path)"
                )
                return True, [], []

            if not log_file.exists():
                self.logger.debug(
                    "Log file not found", extra={"log_file": str(log_file)}
                )
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
            filtered = [
                entry
                for entry in filtered
                if self._parse_timestamp(entry) >= cutoff_time
            ]

        # Filter by level
        if level:
            level_upper = level.upper()
            filtered = [
                entry
                for entry in filtered
                if entry.get("level", "").upper() == level_upper
            ]

        # Filter by session_id
        if session_id:
            filtered = [
                entry for entry in filtered if entry.get("session_id") == session_id
            ]

        # Filter by execution_id
        if execution_id:
            filtered = [
                entry for entry in filtered if entry.get("execution_id") == execution_id
            ]

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
        except:
            try:
                # Try common log format
                return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")
            except:
                # Return very old date if parsing fails
                return datetime.min
