"""Controller for solution lifecycle operations."""

import json
from pathlib import Path
from typing import List, Optional, Tuple

from xyz_platform.controllers.base_controller import BaseController
from xyz_platform.models.solution_model import SolutionModel, SolutionSpecRepositoryModel
from xyz_platform.services.solution_service import SolutionService
from xyz_platform.utils.config import (
    SOLUTION_CONFIGURATION_FILE,
    SOLUTION_DIR,
    SOLUTION_FILE,
    SOLUTION_LOGGING_FILE,
    SOLUTION_WORKSPACE_SUFFIX,
)


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
            spec={},  # type: ignore[arg-type]
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
