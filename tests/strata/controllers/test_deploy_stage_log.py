#!/usr/bin/env python3
"""Tests for `StageLog` (docs/design/audit-trail.md, "Stages")."""

from strata.controllers.deploy_stage_log import NOT_REACHED, NOT_REACHED_AFTER_FAILURE, StageLog
from strata.utils.diagnostics import Diagnostics


def _log(*steps: str) -> tuple[StageLog, Diagnostics]:
    log, diagnostics = StageLog(), Diagnostics()
    log.expect([(step, "tf") for step in steps], diagnostics)
    return log, diagnostics


def test_steps_that_ran_to_the_end_succeed():
    log, diagnostics = _log("a", "b")
    for step in ("a", "b"):
        log.begin(step)
        log.phase("setup")
        log.phase("apply")
    log.finish()

    stages = log.stages(diagnostics)

    assert [(s.name, s.provisioner, s.status, s.skip_reason) for s in stages] == [
        ("a", "tf", "success", None),
        ("b", "tf", "success", None),
    ]
    assert [(p.step, p.success) for p in stages[0].steps] == [("setup", True), ("apply", True)]


def test_the_stage_the_run_returned_out_of_failed_and_the_rest_were_skipped():
    log, diagnostics = _log("a", "b", "c")
    log.begin("a")
    log.phase("apply")
    log.begin("b")
    log.phase("setup")
    log.phase("apply")
    diagnostics.error("Step 'b': deploy failed", location="b")

    a, b, c = log.stages(diagnostics)

    assert a.status == "success"
    assert b.status == "failed" and b.errors == ["b: Step 'b': deploy failed"]
    assert [(p.step, p.success) for p in b.steps] == [("setup", True), ("apply", False)]
    assert (c.status, c.skip_reason) == ("skipped", NOT_REACHED_AFTER_FAILURE)


def test_an_error_after_a_phase_ended_still_fails_its_stage():
    log, diagnostics = _log("a")
    log.begin("a")
    log.phase("apply")
    log.end_phase()
    diagnostics.error("could not record", location="a")

    [stage] = log.stages(diagnostics)

    assert stage.status == "failed"
    assert [(p.step, p.success) for p in stage.steps] == [("apply", True)]


def test_a_run_that_stopped_before_any_step_skips_them_and_fails_the_one_an_error_names():
    log, diagnostics = _log("a", "b")
    diagnostics.error("tool missing", location="b")
    diagnostics.error("wrong kube context")

    a, b = log.stages(diagnostics)

    assert (a.status, a.skip_reason) == ("skipped", NOT_REACHED)
    assert (b.status, b.skip_reason, b.errors) == ("failed", None, ["b: tool missing"])


def test_an_explicit_skip_keeps_its_reason():
    log, diagnostics = _log("a", "b")
    log.skip("a", "helm cannot destroy")
    log.begin("b")
    log.finish()

    a, b = log.stages(diagnostics)

    assert (a.status, a.skip_reason) == ("skipped", "helm cannot destroy")
    assert b.status == "success"


def test_findings_are_filed_under_the_step_they_name():
    log, diagnostics = _log("a")
    log.begin("a")
    diagnostics.warning("slow", location="a")
    diagnostics.info("not pushed", location="a")
    diagnostics.warning("elsewhere", location="other")
    log.finish()

    [stage] = log.stages(diagnostics)

    assert (stage.warnings, stage.messages, stage.errors) == (["a: slow"], ["a: not pushed"], None)
