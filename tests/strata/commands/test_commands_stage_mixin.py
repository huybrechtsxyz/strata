"""Tests for StageSelectionMixin — ADR-0083 Phase 8.

The mixin collapses four blocks that were duplicated across seven commands: the
null-service guard, the ``spec.stages`` extraction, the ``--stage``/``--scope``
filter, and the error routing.

Note how little scaffolding these tests need — a five-line stub, no command
lifecycle. That is why a `StageController` was rejected: the layer-correctness
and testability arguments for it did not survive contact with this file.
"""

from unittest.mock import MagicMock

from strata.commands.stage_mixin import StageSelectionMixin
from strata.utils.resolved_values import ResolvedValues
from strata.utils.stage_selection import StageSelectionMode


def _stage(name: str, scope=None, enabled=None, depends_on=None) -> MagicMock:
    stage = MagicMock()
    stage.name = name
    stage.scope = scope
    stage.enabled = enabled
    stage.depends_on = depends_on
    return stage


class _Cmd(StageSelectionMixin):
    """Minimal stand-in for a command that uses the mixin."""

    def __init__(self, stages=None, *, stage=None, scope=None, service=True):
        self._errors = []
        self._stage = stage
        self._scope = scope
        if service:
            svc = MagicMock()
            svc.model.spec.stages = stages
            self._deployment_service = svc
        else:
            self._deployment_service = None


class TestNullServiceGuard:
    """The guard was duplicated at all seven sites, with two message variants."""

    def test_missing_service_returns_none_and_records_the_reason(self):
        cmd = _Cmd(service=False)

        assert cmd._resolve_stages(StageSelectionMode.INSPECT) is None
        assert cmd._errors == ["Deployment service not loaded"]

    def test_missing_model_is_treated_the_same(self):
        cmd = _Cmd()
        cmd._deployment_service.model = None

        assert cmd._resolve_stages(StageSelectionMode.INSPECT) is None
        assert cmd._errors == ["Deployment service not loaded"]


class TestStageExtraction:
    def test_no_stages_declared_yields_an_empty_selection_not_an_error(self):
        selection = _Cmd(None)._resolve_stages(StageSelectionMode.INSPECT)

        assert selection is not None
        assert selection.to_run == []

    def test_returns_every_stage_by_default(self):
        cmd = _Cmd([_stage("a"), _stage("b")])

        selection = cmd._resolve_stages(StageSelectionMode.INSPECT)

        assert selection is not None
        assert [s.name for s in selection.to_run] == ["a", "b"]
        assert cmd._errors == []


class TestCliFiltersAreReadFromTheCommand:
    def test_stage_attribute_is_applied(self):
        cmd = _Cmd([_stage("a"), _stage("b")], stage="b")

        selection = cmd._resolve_stages(StageSelectionMode.INSPECT)

        assert selection is not None
        assert [s.name for s in selection.to_run] == ["b"]

    def test_scope_attribute_is_applied(self):
        cmd = _Cmd([_stage("a", scope="infra"), _stage("b", scope="apps")], scope="apps")

        selection = cmd._resolve_stages(StageSelectionMode.INSPECT)

        assert selection is not None
        assert [s.name for s in selection.to_run] == ["b"]

    def test_commands_without_scope_default_to_none(self):
        """Five of the seven sites have no --scope option at all."""

        class _NoScope(StageSelectionMixin):
            def __init__(self, stages):
                self._errors = []
                self._stage = None
                svc = MagicMock()
                svc.model.spec.stages = stages
                self._deployment_service = svc

        selection = _NoScope([_stage("a", scope="infra")])._resolve_stages(StageSelectionMode.INSPECT)

        assert selection is not None
        assert [s.name for s in selection.to_run] == ["a"]

    def test_unknown_stage_routes_the_error_and_aborts(self):
        cmd = _Cmd([_stage("a")], stage="ghost")

        assert cmd._resolve_stages(StageSelectionMode.INSPECT) is None
        assert len(cmd._errors) == 1
        assert "not found in deployment definition" in cmd._errors[0]

    def test_error_wording_is_the_unified_one(self):
        """Five commands previously used a terser variant of this message."""
        cmd = _Cmd([_stage("a")], stage="ghost")
        cmd._resolve_stages(StageSelectionMode.INSPECT)

        assert cmd._errors[0].startswith("Stage 'ghost' not found in deployment definition. Available: ")


class TestModeIsHonoured:
    def test_deploy_gates_and_orders(self):
        cmd = _Cmd([_stage("b", depends_on=["a"]), _stage("a"), _stage("off", enabled=False)])

        selection = cmd._resolve_stages(StageSelectionMode.DEPLOY)

        assert selection is not None
        assert [s.name for s in selection.to_run] == ["a", "b"]
        assert [s.stage_name for s in selection.skipped] == ["off"]

    def test_destroy_neither_gates_nor_orders(self):
        cmd = _Cmd([_stage("b", depends_on=["a"]), _stage("a"), _stage("off", enabled=False)])

        selection = cmd._resolve_stages(StageSelectionMode.DESTROY)

        assert selection is not None
        assert [s.name for s in selection.to_run] == ["b", "a", "off"]
        assert selection.skipped == []

    def test_inspect_neither_gates_nor_orders(self):
        cmd = _Cmd([_stage("b", depends_on=["a"]), _stage("a"), _stage("off", enabled=False)])

        selection = cmd._resolve_stages(StageSelectionMode.INSPECT)

        assert selection is not None
        assert [s.name for s in selection.to_run] == ["b", "a", "off"]

    def test_resolved_values_are_passed_through_for_expressions(self):
        cmd = _Cmd([_stage("api", enabled="${feature:on}")])

        selection = cmd._resolve_stages(
            StageSelectionMode.DEPLOY,
            resolved=ResolvedValues(features={"on": False}),
        )

        assert selection is not None
        assert selection.to_run == []
        assert [s.stage_name for s in selection.skipped] == ["api"]

    def test_gating_error_aborts_and_is_routed(self):
        cmd = _Cmd([_stage("api", enabled="${feature:typo}")])

        result = cmd._resolve_stages(StageSelectionMode.DEPLOY, resolved=ResolvedValues(features={}))

        assert result is None
        assert any("typo" in e for e in cmd._errors)


class TestModeProperties:
    """DESTROY and INSPECT behave identically today but for different reasons."""

    def test_only_deploy_gates(self):
        assert StageSelectionMode.DEPLOY.gates is True
        assert StageSelectionMode.DESTROY.gates is False
        assert StageSelectionMode.INSPECT.gates is False

    def test_only_deploy_orders(self):
        assert StageSelectionMode.DEPLOY.orders is True
        assert StageSelectionMode.DESTROY.orders is False
        assert StageSelectionMode.INSPECT.orders is False
