"""Controller for managing spec.context in solution.json — team-shared template defaults."""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from strata.controllers.base_controller import BaseController
from strata.controllers.solution_controller import SolutionController
from strata.models.solution_model import SolutionModel


class ContextController(BaseController):
    """Manage spec.context in solution.json — team-shared template defaults."""

    def __init__(self, work_path: Path) -> None:
        super().__init__()
        self._work_path = work_path
        self._solution_controller = SolutionController(work_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> Tuple[bool, Optional[SolutionModel]]:
        """Load the solution. Returns (ok, solution_or_None)."""
        ok, errors = self._solution_controller.load()
        if not ok:
            self._errors.extend(errors)
            return False, None
        solution = self._solution_controller.solution
        if solution is None:
            self._errors.append("No solution loaded.")
            return False, None
        return True, solution

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def set(self, key: str, value: str) -> Tuple[bool, List[str]]:
        """Set *key* to *value* in spec.context and persist.

        Returns:
            (success, errors)
        """
        ok, solution = self._load()
        if not ok:
            return False, self.get_errors()
        assert solution is not None
        if solution.spec.context is None:
            solution.spec.context = {}
        solution.spec.context[key] = value
        ok, errors = self._solution_controller.save()
        self._errors.extend(errors)
        return ok, self.get_errors()

    def unset(self, key: str) -> Tuple[bool, List[str]]:
        """Remove *key* from spec.context and persist.

        Silently succeeds when the key does not exist.

        Returns:
            (success, errors)
        """
        ok, solution = self._load()
        if not ok:
            return False, self.get_errors()
        assert solution is not None
        if solution.spec.context and key in solution.spec.context:
            del solution.spec.context[key]
        ok, errors = self._solution_controller.save()
        self._errors.extend(errors)
        return ok, self.get_errors()

    def list(self) -> Tuple[bool, Dict[str, str], List[str]]:
        """Return all key/value pairs from spec.context.

        Returns:
            (success, context_dict, errors)
        """
        ok, solution = self._load()
        if not ok:
            return False, {}, self.get_errors()
        assert solution is not None
        return True, dict(solution.spec.context or {}), []
