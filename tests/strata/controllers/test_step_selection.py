#!/usr/bin/env python3
"""Tests for `step_selection.select_steps()` (docs/design/deploy-steps-command.md)."""

from strata.controllers.step_selection import select_steps
from strata.models.provisioning_model import ProvisioningStepModel


def _step(name: str, scope: str | None = None) -> ProvisioningStepModel:
    return ProvisioningStepModel(name=name, provisioner="p", resources=["r"], scope=scope)


STEPS = [_step("network", "infra"), _step("dns", "infra"), _step("apps", "apps"), _step("smoke")]


def _names(selection) -> list[str]:
    return [step.name for step in selection.steps]


def test_no_filter_keeps_every_step_in_order():
    selection = select_steps(STEPS)
    assert _names(selection) == ["network", "dns", "apps", "smoke"]
    assert selection.unmatched_stages == []
    assert selection.unmatched_scopes == []


def test_one_stage():
    assert _names(select_steps(STEPS, stages=["dns"])) == ["dns"]


def test_two_stages_keep_input_order():
    assert _names(select_steps(STEPS, stages=["apps", "network"])) == ["network", "apps"]


def test_one_scope():
    assert _names(select_steps(STEPS, scopes=["infra"])) == ["network", "dns"]


def test_two_scopes():
    assert _names(select_steps(STEPS, scopes=["apps", "infra"])) == ["network", "dns", "apps"]


def test_stage_and_scope_must_both_match():
    assert _names(select_steps(STEPS, stages=["network", "apps"], scopes=["infra"])) == ["network"]


def test_an_empty_result_is_not_an_error():
    selection = select_steps(STEPS, stages=["network"], scopes=["apps"])
    assert selection.steps == []
    assert selection.unmatched_stages == []
    assert selection.unmatched_scopes == []


def test_unmatched_values_are_reported_once():
    selection = select_steps(STEPS, stages=["ghost", "dns", "ghost"], scopes=["db"])
    assert _names(selection) == []
    assert selection.unmatched_stages == ["ghost"]
    assert selection.unmatched_scopes == ["db"]


def test_a_step_without_scope_never_matches_a_scope_filter():
    assert "smoke" not in _names(select_steps(STEPS, scopes=["infra", "apps"]))
