#!/usr/bin/env python3
"""Tests for `extends` chain resolution."""

from pathlib import Path

import pytest

from strata.controllers.deployment_resolution import resolve_deployment_chains
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel


def _deployment(name: str, spec: dict) -> DeploymentModel:
    return DeploymentModel.model_validate({"meta": {"name": name}, "spec": spec})


def _index(*deployments: DeploymentModel) -> DocumentIndex:
    index = DocumentIndex()
    for model in deployments:
        ref = DocumentRef(kind=PlatformKind.DEPLOYMENT, name=model.meta.name)
        index.add(IndexEntry(ref=ref, model=model, source=Path(f"{model.meta.name}.yaml")))
    return index


# ---------------------------------------------------------------------------
# No extends
# ---------------------------------------------------------------------------


def test_plain_deployment_resolves_to_itself():
    """A deployment with no `extends` needs no merge — it is already complete."""
    index = _index(_deployment("solo", {"workspace": "main", "environments": ["prd"]}))
    resolved, diagnostics = resolve_deployment_chains(index)
    assert diagnostics.ok
    assert resolved["solo"].spec.workspace == "main"


def test_partial_base_with_no_consumers_is_silently_excluded():
    """Not deployable, so the other checks correctly have nothing to say about it."""
    index = _index(_deployment("base", {"partial": True, "workspace": "main"}))
    resolved, diagnostics = resolve_deployment_chains(index)
    assert diagnostics.ok
    assert "base" not in resolved


# ---------------------------------------------------------------------------
# Single-level merge
# ---------------------------------------------------------------------------


def test_single_level_extends_merges_in_the_base():
    """The exact gap found in the shipped example: workspace only via extends."""
    index = _index(
        _deployment("base", {"partial": True, "workspace": "main"}),
        _deployment("leaf", {"extends": "base", "environments": ["prd"]}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert diagnostics.ok
    assert resolved["leaf"].spec.workspace == "main"
    assert resolved["leaf"].spec.environments == ["prd"]


def test_child_field_wins_over_the_base():
    index = _index(
        _deployment("base", {"partial": True, "workspace": "base-ws"}),
        _deployment("leaf", {"extends": "base", "workspace": "leaf-ws", "environments": ["prd"]}),
    )
    resolved, _ = resolve_deployment_chains(index)
    assert resolved["leaf"].spec.workspace == "leaf-ws"


# ---------------------------------------------------------------------------
# Multi-level chains and shared ancestors
# ---------------------------------------------------------------------------


def test_three_level_chain_folds_root_to_leaf():
    index = _index(
        _deployment("root", {"partial": True, "workspace": "main"}),
        _deployment("middle", {"partial": True, "extends": "root", "locking": {"enabled": True}}),
        _deployment("leaf", {"extends": "middle", "environments": ["prd"]}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert diagnostics.ok
    leaf = resolved["leaf"]
    assert leaf.spec.workspace == "main"  # from root
    assert leaf.spec.locking.enabled is True  # from middle
    assert leaf.spec.environments == ["prd"]  # from leaf itself


def test_two_leaves_sharing_one_base_both_resolve_independently():
    """A diamond: the shared base is folded once (memoised) but each leaf
    keeps its own overrides.
    """
    index = _index(
        _deployment("base", {"partial": True, "workspace": "main"}),
        _deployment("leaf-a", {"extends": "base", "environments": ["dev"]}),
        _deployment("leaf-b", {"extends": "base", "environments": ["prd"]}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert diagnostics.ok
    assert resolved["leaf-a"].spec.environments == ["dev"]
    assert resolved["leaf-b"].spec.environments == ["prd"]
    assert resolved["leaf-a"].spec.workspace == resolved["leaf-b"].spec.workspace == "main"


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------


def test_two_node_cycle_is_detected():
    index = _index(
        _deployment("a", {"partial": True, "extends": "b"}),
        _deployment("b", {"partial": True, "extends": "a"}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert not diagnostics.ok
    assert "a" not in resolved and "b" not in resolved
    assert any("circular" in m.lower() for m in diagnostics.messages())


def test_three_node_cycle_names_the_full_path():
    index = _index(
        _deployment("a", {"partial": True, "extends": "b"}),
        _deployment("b", {"partial": True, "extends": "c"}),
        _deployment("c", {"partial": True, "extends": "a"}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert not diagnostics.ok
    assert not resolved
    # Whichever node the cycle detector reaches first gets the full path;
    # the others get a shorter "reported elsewhere" pointer. Both are present.
    messages = diagnostics.messages()
    assert any(" -> " in m for m in messages)
    assert len(diagnostics.errors) == 3


def test_cycle_does_not_prevent_other_deployments_from_resolving():
    """One bad chain must not take down unrelated documents."""
    index = _index(
        _deployment("a", {"partial": True, "extends": "b"}),
        _deployment("b", {"partial": True, "extends": "a"}),
        _deployment("fine", {"workspace": "main", "environments": ["prd"]}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert not diagnostics.ok
    assert "fine" in resolved
    assert resolved["fine"].spec.workspace == "main"


# ---------------------------------------------------------------------------
# Missing target / never-terminating chain
# ---------------------------------------------------------------------------


def test_extends_target_not_in_the_index_does_not_crash():
    """Already reported by validate_references; this must degrade gracefully."""
    index = _index(_deployment("leaf", {"extends": "ghost", "environments": ["prd"]}))
    resolved, diagnostics = resolve_deployment_chains(index)
    assert "leaf" not in resolved
    # No duplicate finding manufactured here — validate_references owns this.
    assert diagnostics.ok


def test_chain_that_never_supplies_required_fields_is_a_real_error():
    """`partial` is stripped by every merge, so an incomplete result cannot
    hide behind it — the model's own completeness check fires for free.
    """
    index = _index(
        _deployment("root", {"partial": True, "locking": {"enabled": True}}),
        _deployment("leaf", {"extends": "root", "tenant": "acme"}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert not diagnostics.ok
    assert "leaf" not in resolved
    assert any("workspace" in m for m in diagnostics.messages())


# ---------------------------------------------------------------------------
# Findings are attributed correctly
# ---------------------------------------------------------------------------


def test_missing_target_produces_no_duplicate_finding():
    """Already reported by validate_references; the resolver must stay silent."""
    index = _index(_deployment("leaf", {"extends": "ghost", "environments": ["prd"]}))
    _, diagnostics = resolve_deployment_chains(index)
    assert diagnostics.ok  # nothing to report; already validate_references' job


def test_trivial_self_extend_is_rejected_before_it_can_reach_the_index():
    """The one cycle shape the model itself catches (ADR: validate_not_self_extending)."""
    with pytest.raises(Exception, match="cannot extend itself"):
        _deployment("x", {"extends": "x"})
