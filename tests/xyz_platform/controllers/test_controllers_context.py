"""Tests for ContextController — in-memory operations."""

from unittest.mock import patch

from xyz_platform.controllers.context_controller import ContextController
from xyz_platform.models.solution_model import SolutionMetaModel, SolutionModel, SolutionSpecModel


def _make_solution(context=None) -> SolutionModel:
    return SolutionModel(
        apiVersion="platform.huybrechts.xyz/v1",
        kind="solution",
        meta=SolutionMetaModel(name="test-solution"),
        spec=SolutionSpecModel(
            solution_id="00000000-0000-0000-0000-000000000001",
            context=context,
        ),
    )


class TestContextControllerSet:
    def test_set_key(self, tmp_path):
        solution = _make_solution()
        ctrl = ContextController(tmp_path)

        with (
            patch.object(ctrl._solution_controller, "load", return_value=(True, [])),
            patch.object(ctrl._solution_controller, "save", return_value=(True, [])),
        ):
            ctrl._solution_controller._solution = solution
            ok, errors = ctrl.set("owner", "myteam")

        assert ok is True
        assert errors == []
        assert solution.spec.context == {"owner": "myteam"}

    def test_set_key_initialises_context_when_none(self, tmp_path):
        solution = _make_solution(context=None)
        ctrl = ContextController(tmp_path)

        with (
            patch.object(ctrl._solution_controller, "load", return_value=(True, [])),
            patch.object(ctrl._solution_controller, "save", return_value=(True, [])),
        ):
            ctrl._solution_controller._solution = solution
            ok, errors = ctrl.set("region", "eu-west")

        assert ok is True
        assert solution.spec.context == {"region": "eu-west"}

    def test_set_multiple_keys(self, tmp_path):
        solution = _make_solution(context={"existing": "val"})
        ctrl = ContextController(tmp_path)

        with (
            patch.object(ctrl._solution_controller, "load", return_value=(True, [])),
            patch.object(ctrl._solution_controller, "save", return_value=(True, [])),
        ):
            ctrl._solution_controller._solution = solution
            ok, _ = ctrl.set("owner", "acme")

        assert ok is True
        assert solution.spec.context == {"existing": "val", "owner": "acme"}


class TestContextControllerUnset:
    def test_unset_key(self, tmp_path):
        solution = _make_solution(context={"owner": "myteam", "region": "eu"})
        ctrl = ContextController(tmp_path)

        with (
            patch.object(ctrl._solution_controller, "load", return_value=(True, [])),
            patch.object(ctrl._solution_controller, "save", return_value=(True, [])),
        ):
            ctrl._solution_controller._solution = solution
            ok, errors = ctrl.unset("owner")

        assert ok is True
        assert "owner" not in (solution.spec.context or {})

    def test_unset_nonexistent_key(self, tmp_path):
        """Removing a key that does not exist must succeed silently."""
        solution = _make_solution(context={"region": "eu"})
        ctrl = ContextController(tmp_path)

        with (
            patch.object(ctrl._solution_controller, "load", return_value=(True, [])),
            patch.object(ctrl._solution_controller, "save", return_value=(True, [])),
        ):
            ctrl._solution_controller._solution = solution
            ok, errors = ctrl.unset("nonexistent")

        assert ok is True
        assert errors == []

    def test_unset_when_context_is_none(self, tmp_path):
        """Unset on a solution with no context at all must succeed silently."""
        solution = _make_solution(context=None)
        ctrl = ContextController(tmp_path)

        with (
            patch.object(ctrl._solution_controller, "load", return_value=(True, [])),
            patch.object(ctrl._solution_controller, "save", return_value=(True, [])),
        ):
            ctrl._solution_controller._solution = solution
            ok, errors = ctrl.unset("owner")

        assert ok is True
        assert errors == []


class TestContextControllerList:
    def test_list_empty(self, tmp_path):
        solution = _make_solution(context=None)
        ctrl = ContextController(tmp_path)

        with (
            patch.object(ctrl._solution_controller, "load", return_value=(True, [])),
        ):
            ctrl._solution_controller._solution = solution
            ok, values, errors = ctrl.list()

        assert ok is True
        assert values == {}
        assert errors == []

    def test_list_with_values(self, tmp_path):
        solution = _make_solution(context={"owner": "acme", "version": "1.0.0"})
        ctrl = ContextController(tmp_path)

        with (
            patch.object(ctrl._solution_controller, "load", return_value=(True, [])),
        ):
            ctrl._solution_controller._solution = solution
            ok, values, errors = ctrl.list()

        assert ok is True
        assert values == {"owner": "acme", "version": "1.0.0"}

    def test_list_load_failure_returns_errors(self, tmp_path):
        ctrl = ContextController(tmp_path)

        with patch.object(ctrl._solution_controller, "load", return_value=(False, ["load failed"])):
            ok, values, errors = ctrl.list()

        assert ok is False
        assert "load failed" in errors
        assert values == {}
