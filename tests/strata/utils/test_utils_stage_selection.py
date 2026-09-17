"""Tests for strata.utils.stage_selection (ADR-0083 Phases 1 & 3).

Covers ``--stage``/``--scope`` narrowing and ``enabled`` gating. ``depends_on``
cascade is Phase 5 and is deliberately absent.
"""

from unittest.mock import MagicMock

from strata.utils.resolved_values import ResolvedValues
from strata.utils.stage_selection import (
    StageSelection,
    StageSkip,
    evaluate_enabled,
    order_stages,
    select_stages,
    validate_stage_dependencies,
)


def _stage(name: str, scope=None, enabled=None, depends_on=None) -> MagicMock:
    stage = MagicMock()
    stage.name = name
    stage.scope = scope
    stage.enabled = enabled
    stage.depends_on = depends_on
    return stage


def _resolved(**features) -> ResolvedValues:
    return ResolvedValues(features=dict(features))


class TestNoFilters:
    def test_returns_every_stage_in_declaration_order(self):
        stages = [_stage("a"), _stage("b"), _stage("c")]

        selection, errors = select_stages(stages)

        assert errors == []
        assert [s.name for s in selection.to_run] == ["a", "b", "c"]

    def test_empty_stage_list_is_not_an_error(self):
        selection, errors = select_stages([])

        assert errors == []
        assert selection.to_run == []

    def test_skipped_is_always_empty_without_gating(self):
        selection, _ = select_stages([_stage("a")])

        assert selection.skipped == []

    def test_does_not_mutate_or_alias_the_caller_list(self):
        stages = [_stage("a")]

        selection, _ = select_stages(stages)
        selection.to_run.append(_stage("injected"))

        assert [s.name for s in stages] == ["a"]


class TestStageFilter:
    def test_narrows_to_the_named_stage(self):
        selection, errors = select_stages([_stage("a"), _stage("b")], stage="b")

        assert errors == []
        assert [s.name for s in selection.to_run] == ["b"]

    def test_unknown_stage_errors_and_lists_available(self):
        selection, errors = select_stages([_stage("a"), _stage("b")], stage="nope")

        assert selection.to_run == []
        assert len(errors) == 1
        assert "Stage 'nope' not found in deployment definition" in errors[0]
        assert "['a', 'b']" in errors[0]

    def test_message_is_runs_wording_now_shared_with_destroy(self):
        """Both commands previously had their own wording; destroy's was terser.

        Unified on run's more informative phrasing (ADR-0083 Phase 1).
        """
        _, errors = select_stages([_stage("a")], stage="nope")

        assert errors[0].startswith("Stage 'nope' not found in deployment definition. Available: ")


class TestScopeFilter:
    def test_narrows_to_matching_scope(self):
        stages = [_stage("a", scope="infra"), _stage("b", scope="apps"), _stage("c", scope="infra")]

        selection, errors = select_stages(stages, scope="infra")

        assert errors == []
        assert [s.name for s in selection.to_run] == ["a", "c"]

    def test_no_match_errors_and_lists_available_scopes(self):
        stages = [_stage("a", scope="infra"), _stage("b", scope=None)]

        selection, errors = select_stages(stages, scope="nope")

        assert selection.to_run == []
        assert len(errors) == 1
        assert "No stages match scope 'nope'" in errors[0]
        assert "['infra']" in errors[0], "scope-less stages must not appear in the available list"


class TestCombinedFilters:
    def test_stage_then_scope_both_applied(self):
        stages = [_stage("a", scope="infra"), _stage("b", scope="apps")]

        selection, errors = select_stages(stages, stage="a", scope="infra")

        assert errors == []
        assert [s.name for s in selection.to_run] == ["a"]

    def test_stage_surviving_stage_filter_but_not_scope_errors(self):
        stages = [_stage("a", scope="infra"), _stage("b", scope="apps")]

        selection, errors = select_stages(stages, stage="a", scope="apps")

        assert selection.to_run == []
        assert "No stages match scope 'apps'" in errors[0]

    def test_available_scopes_list_spans_all_stages_not_the_filtered_subset(self):
        """The error must help the operator, so it lists every declared scope."""
        stages = [_stage("a", scope="infra"), _stage("b", scope="apps")]

        _, errors = select_stages(stages, stage="a", scope="nope")

        assert "'infra'" in errors[0] and "'apps'" in errors[0]


class TestDataclassShape:
    """The return shape is final in Phase 1 so later phases add no churn."""

    def test_stage_selection_defaults_are_empty(self):
        selection = StageSelection()

        assert selection.to_run == []
        assert selection.skipped == []

    def test_stage_skip_carries_the_audit_fields(self):
        skip = StageSkip(
            stage_name="dispatcher_api",
            reason="disabled",
            detail="enabled resolved to 'false'",
            expression="${feature:enable_dispatcher_api}",
            resolved_value="false",
        )

        assert skip.stage_name == "dispatcher_api"
        assert skip.reason == "disabled"
        assert skip.expression == "${feature:enable_dispatcher_api}"
        assert skip.resolved_value == "false"

    def test_dependency_skipped_reason_exists_before_its_producer_does(self):
        """Declared in Phase 1, unreachable until the depends_on cascade lands."""
        skip = StageSkip(stage_name="b", reason="dependency_skipped", detail="depends on skipped stage 'a'")

        assert skip.reason == "dependency_skipped"
        assert skip.expression is None


class TestEvaluateEnabled:
    """Per-stage gate evaluation (ADR-0083 D1)."""

    def test_absent_means_enabled(self):
        ok, skip, err = evaluate_enabled(_stage("a"), None)

        assert (ok, skip, err) == (True, None, None)

    def test_literal_true_is_enabled(self):
        ok, skip, err = evaluate_enabled(_stage("a", enabled=True), None)

        assert (ok, skip, err) == (True, None, None)

    def test_literal_false_is_disabled(self):
        ok, skip, err = evaluate_enabled(_stage("a", enabled=False), None)

        assert ok is False
        assert err is None
        assert skip is not None
        assert skip.reason == "disabled"
        assert skip.resolved_value == "false"

    def test_literal_string_uses_the_shared_truthiness_rule(self):
        assert evaluate_enabled(_stage("a", enabled="no"), None)[0] is False
        assert evaluate_enabled(_stage("a", enabled="yes"), None)[0] is True

    def test_feature_expression_resolving_true_enables(self):
        ok, skip, err = evaluate_enabled(_stage("a", enabled="${feature:f}"), _resolved(f=True))

        assert (ok, skip, err) == (True, None, None)

    def test_feature_expression_resolving_false_disables_and_records_both_values(self):
        ok, skip, err = evaluate_enabled(_stage("a", enabled="${feature:f}"), _resolved(f=False))

        assert ok is False
        assert err is None
        assert skip is not None
        assert skip.expression == "${feature:f}"
        assert skip.resolved_value == "false"
        # The detail is persisted verbatim as the audit skip reason, so it must name
        # WHICH flag was responsible, not just that something resolved false.
        assert "${feature:f}" in skip.detail
        assert "false" in skip.detail

    def test_unresolvable_reference_is_an_error_not_a_silent_skip(self):
        """A typo must not quietly drop a stage from the deployment."""
        ok, skip, err = evaluate_enabled(_stage("a", enabled="${feature:typo}"), _resolved(other=True))

        assert ok is False
        assert skip is None
        assert err is not None
        assert "typo" in err and "could not be resolved" in err

    def test_expression_without_resolved_values_is_an_error(self):
        ok, skip, err = evaluate_enabled(_stage("a", enabled="${feature:f}"), None)

        assert ok is False
        assert skip is None
        assert err is not None
        assert "no resolved" in err

    def test_literal_string_needs_no_resolved_values(self):
        assert evaluate_enabled(_stage("a", enabled="true"), None)[0] is True


class TestGating:
    def test_disabled_stage_is_excluded_and_recorded(self):
        stages = [_stage("a"), _stage("b", enabled=False)]

        selection, errors = select_stages(stages)

        assert errors == []
        assert [s.name for s in selection.to_run] == ["a"]
        assert [s.stage_name for s in selection.skipped] == ["b"]

    def test_gating_can_be_switched_off_for_destroy(self):
        """D3: destroy must still see a disabled stage."""
        stages = [_stage("a"), _stage("b", enabled=False)]

        selection, errors = select_stages(stages, apply_gating=False)

        assert errors == []
        assert [s.name for s in selection.to_run] == ["a", "b"]
        assert selection.skipped == []

    def test_expression_gating_end_to_end(self):
        stages = [
            _stage("core"),
            _stage("api", enabled="${feature:enable_api}"),
        ]

        selection, errors = select_stages(stages, resolved=_resolved(enable_api=False))

        assert errors == []
        assert [s.name for s in selection.to_run] == ["core"]
        assert selection.skipped[0].expression == "${feature:enable_api}"

    def test_all_stages_disabled_is_a_no_op_not_an_error(self):
        stages = [_stage("a", enabled=False), _stage("b", enabled=False)]

        selection, errors = select_stages(stages)

        assert errors == []
        assert selection.to_run == []
        assert len(selection.skipped) == 2

    def test_resolution_error_aborts_and_returns_no_selection(self):
        stages = [_stage("a"), _stage("b", enabled="${feature:missing}")]

        selection, errors = select_stages(stages, resolved=_resolved(other=True))

        assert len(errors) == 1
        assert selection.to_run == []

    def test_every_broken_expression_is_reported_not_just_the_first(self):
        stages = [
            _stage("a", enabled="${feature:missing_one}"),
            _stage("b", enabled="${feature:missing_two}"),
        ]

        _, errors = select_stages(stages, resolved=_resolved())

        assert len(errors) == 2


class TestGatingWithCliFilters:
    def test_stage_filter_naming_a_disabled_stage_errors(self):
        """D4: CLI does not override `enabled` — it is a correctness condition."""
        stages = [_stage("a"), _stage("b", enabled=False)]

        selection, errors = select_stages(stages, stage="b")

        assert selection.to_run == []
        assert len(errors) == 1
        assert "'b' is disabled in this environment" in errors[0]
        assert "--stage" in errors[0]

    def test_disabled_stage_error_explains_the_cause(self):
        stages = [_stage("b", enabled="${feature:off}")]

        _, errors = select_stages(stages, stage="b", resolved=_resolved(off=False))

        assert "resolved to 'false'" in errors[0]

    def test_unknown_stage_still_reports_not_found_not_disabled(self):
        stages = [_stage("a", enabled=False)]

        _, errors = select_stages(stages, stage="ghost")

        assert "not found in deployment definition" in errors[0]

    def test_stage_filter_on_an_enabled_stage_works_normally(self):
        stages = [_stage("a"), _stage("b", enabled=False)]

        selection, errors = select_stages(stages, stage="a")

        assert errors == []
        assert [s.name for s in selection.to_run] == ["a"]

    def test_scope_whose_stages_are_all_disabled_is_a_no_op(self):
        stages = [_stage("a", scope="infra", enabled=False), _stage("b", scope="apps")]

        selection, errors = select_stages(stages, scope="infra")

        assert errors == [], "stages matched the scope; the environment just disabled them"
        assert selection.to_run == []
        assert [s.stage_name for s in selection.skipped] == ["a"]

    def test_skipped_excludes_stages_outside_the_cli_selection(self):
        """Recording an out-of-scope stage as skipped would pollute the audit trail."""
        stages = [_stage("a", scope="infra"), _stage("b", scope="apps", enabled=False)]

        selection, errors = select_stages(stages, scope="infra")

        assert errors == []
        assert [s.name for s in selection.to_run] == ["a"]
        assert selection.skipped == [], "'b' was never part of this run"


class TestOrderStagesStability:
    """The safety property for giving `depends_on` runtime meaning (Design §4).

    If these fail, existing deployments would be silently reordered.
    """

    def test_already_valid_order_is_returned_unchanged(self):
        stages = [
            _stage("a"),
            _stage("b", depends_on=["a"]),
            _stage("c", depends_on=["b"]),
        ]

        ordered, errors = order_stages(stages)

        assert errors == []
        assert [s.name for s in ordered] == ["a", "b", "c"]

    def test_independent_stages_keep_declaration_order(self):
        stages = [_stage(n) for n in ("z", "m", "a")]

        ordered, errors = order_stages(stages)

        assert errors == []
        assert [s.name for s in ordered] == ["z", "m", "a"], "must not sort alphabetically"

    def test_partially_constrained_graph_keeps_declaration_order_elsewhere(self):
        stages = [
            _stage("first"),
            _stage("second"),
            _stage("third", depends_on=["first"]),
            _stage("fourth"),
        ]

        ordered, _ = order_stages(stages)

        assert [s.name for s in ordered] == ["first", "second", "third", "fourth"]

    def test_diamond_already_ordered_is_unchanged(self):
        stages = [
            _stage("root"),
            _stage("left", depends_on=["root"]),
            _stage("right", depends_on=["root"]),
            _stage("join", depends_on=["left", "right"]),
        ]

        ordered, _ = order_stages(stages)

        assert [s.name for s in ordered] == ["root", "left", "right", "join"]

    def test_empty_input(self):
        assert order_stages([]) == ([], [])


class TestOrderStagesReordering:
    def test_out_of_order_file_is_reordered(self):
        stages = [_stage("b", depends_on=["a"]), _stage("a")]

        ordered, errors = order_stages(stages)

        assert errors == []
        assert [s.name for s in ordered] == ["a", "b"]

    def test_dependency_outside_the_set_is_ignored(self):
        """--stage/--scope may legitimately narrow the set; that is not an error."""
        ordered, errors = order_stages([_stage("b", depends_on=["a"])])

        assert errors == []
        assert [s.name for s in ordered] == ["b"]

    def test_cycle_is_reported_and_yields_no_order(self):
        stages = [_stage("a", depends_on=["b"]), _stage("b", depends_on=["a"])]

        ordered, errors = order_stages(stages)

        assert ordered == []
        assert len(errors) == 1
        assert "cycle" in errors[0]
        assert "'a'" in errors[0] and "'b'" in errors[0]

    def test_cycle_does_not_hide_the_orderable_remainder_from_the_message(self):
        stages = [_stage("ok"), _stage("a", depends_on=["b"]), _stage("b", depends_on=["a"])]

        _, errors = select_stages(stages)

        assert len(errors) == 1
        assert "ok" not in errors[0]


class TestValidateStageDependencies:
    def test_sound_graph_produces_no_errors(self):
        stages = [_stage("a"), _stage("b", depends_on=["a"])]

        assert validate_stage_dependencies(stages) == []

    def test_no_dependencies_at_all_is_sound(self):
        assert validate_stage_dependencies([_stage("a"), _stage("b")]) == []

    def test_unknown_dependency_is_reported_with_available_names(self):
        stages = [_stage("a"), _stage("b", depends_on=["ghost"])]

        errors = validate_stage_dependencies(stages)

        assert len(errors) == 1
        assert "unknown stage 'ghost'" in errors[0]
        assert "'a'" in errors[0]

    def test_self_dependency_is_reported(self):
        errors = validate_stage_dependencies([_stage("a", depends_on=["a"])])

        assert len(errors) == 1
        assert "lists the stage itself" in errors[0]

    def test_cycle_is_reported(self):
        stages = [_stage("a", depends_on=["b"]), _stage("b", depends_on=["a"])]

        errors = validate_stage_dependencies(stages)

        assert len(errors) == 1
        assert "cycle" in errors[0]

    def test_dangling_reference_is_reported_without_a_derived_cycle_complaint(self):
        """A concrete typo must not be buried under a consequence of itself."""
        stages = [_stage("a", depends_on=["ghost"]), _stage("b", depends_on=["a"])]

        errors = validate_stage_dependencies(stages)

        assert len(errors) == 1
        assert "ghost" in errors[0]


class TestDependencySkipCascade:
    def test_dependent_of_a_disabled_stage_is_skipped(self):
        stages = [_stage("a", enabled=False), _stage("b", depends_on=["a"])]

        selection, errors = select_stages(stages)

        assert errors == []
        assert selection.to_run == []
        reasons = {s.stage_name: s for s in selection.skipped}
        assert reasons["a"].reason == "disabled"
        assert reasons["b"].reason == "dependency_skipped"
        assert "depends on skipped stage 'a'" in reasons["b"].detail

    def test_cascade_is_transitive(self):
        stages = [
            _stage("a", enabled=False),
            _stage("b", depends_on=["a"]),
            _stage("c", depends_on=["b"]),
        ]

        selection, _ = select_stages(stages)

        assert selection.to_run == []
        assert [s.stage_name for s in selection.skipped] == ["a", "b", "c"]

    def test_transitive_reason_names_the_direct_dependency(self):
        """One hop at a time, so the chain can be walked back."""
        stages = [
            _stage("a", enabled=False),
            _stage("b", depends_on=["a"]),
            _stage("c", depends_on=["b"]),
        ]

        selection, _ = select_stages(stages)
        reasons = {s.stage_name: s.detail for s in selection.skipped}

        assert "'a'" in reasons["b"]
        assert "'b'" in reasons["c"], "should name b, not the distant root a"

    def test_dependent_of_an_enabled_stage_still_runs(self):
        stages = [_stage("a"), _stage("b", depends_on=["a"])]

        selection, errors = select_stages(stages)

        assert errors == []
        assert [s.name for s in selection.to_run] == ["a", "b"]

    def test_unrelated_stages_are_unaffected(self):
        stages = [
            _stage("a", enabled=False),
            _stage("b", depends_on=["a"]),
            _stage("independent"),
        ]

        selection, _ = select_stages(stages)

        assert [s.name for s in selection.to_run] == ["independent"]

    def test_diamond_cascade_skips_the_join(self):
        stages = [
            _stage("root", enabled=False),
            _stage("left", depends_on=["root"]),
            _stage("right", depends_on=["root"]),
            _stage("join", depends_on=["left", "right"]),
        ]

        selection, _ = select_stages(stages)

        assert selection.to_run == []
        assert len(selection.skipped) == 4

    def test_cascade_is_reported_in_declaration_order(self):
        stages = [
            _stage("first", depends_on=["trigger"]),
            _stage("trigger", enabled=False),
        ]

        selection, _ = select_stages(stages)

        assert [s.stage_name for s in selection.skipped] == ["first", "trigger"]

    def test_no_cascade_when_gating_is_off(self):
        stages = [_stage("a", enabled=False), _stage("b", depends_on=["a"])]

        selection, _ = select_stages(stages, apply_gating=False)

        assert [s.name for s in selection.to_run] == ["a", "b"]


class TestCascadeVersusCliFiltering:
    """§3.1 — `enabled` cascades; --stage/--scope deliberately do not."""

    def test_stage_filter_does_not_cascade_to_its_dependency(self):
        """Otherwise --stage B would be impossible whenever B declares a dependency."""
        stages = [_stage("a"), _stage("b", depends_on=["a"])]

        selection, errors = select_stages(stages, stage="b")

        assert errors == []
        assert [s.name for s in selection.to_run] == ["b"]

    def test_scope_filter_does_not_cascade(self):
        stages = [_stage("a", scope="infra"), _stage("b", scope="apps", depends_on=["a"])]

        selection, errors = select_stages(stages, scope="apps")

        assert errors == []
        assert [s.name for s in selection.to_run] == ["b"]

    def test_stage_filter_naming_a_cascade_skipped_stage_errors(self):
        stages = [_stage("a", enabled=False), _stage("b", depends_on=["a"])]

        selection, errors = select_stages(stages, stage="b")

        assert selection.to_run == []
        assert "depends on skipped stage 'a'" in errors[0]
        assert "--stage" in errors[0]


class TestOrderingInSelection:
    def test_to_run_is_dependency_ordered(self):
        stages = [_stage("b", depends_on=["a"]), _stage("a")]

        selection, errors = select_stages(stages)

        assert errors == []
        assert [s.name for s in selection.to_run] == ["a", "b"]

    def test_ordering_can_be_switched_off_for_destroy(self):
        """Teardown needs the reverse order, which this ADR does not decide."""
        stages = [_stage("b", depends_on=["a"]), _stage("a")]

        selection, errors = select_stages(stages, apply_gating=False, apply_ordering=False)

        assert errors == []
        assert [s.name for s in selection.to_run] == ["b", "a"]
