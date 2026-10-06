#!/usr/bin/env python3
"""Tests for `strata.utils.dependency_order.topological_order()` — the
shared Kahn's-algorithm extraction, built for
docs/design/provisioner-source-dependencies.md."""

import pytest

from strata.utils.dependency_order import topological_order


def test_topological_order_respects_dependency_order():
    order = topological_order(["b", "a"], {"b": ["a"]})
    assert order == ["a", "b"]


def test_topological_order_preserves_independent_names_original_order():
    order = topological_order(["x", "y"], {})
    assert order == ["x", "y"]


def test_topological_order_handles_a_diamond():
    order = topological_order(["d", "b", "c", "a"], {"d": ["b", "c"], "b": ["a"], "c": ["a"]})
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")


def test_topological_order_rejects_a_direct_cycle():
    with pytest.raises(ValueError, match="Circular dependency"):
        topological_order(["a", "b"], {"a": ["b"], "b": ["a"]})


def test_topological_order_cycle_message_names_every_cyclic_node():
    with pytest.raises(ValueError, match=r"a -> b|b -> a"):
        topological_order(["a", "b"], {"a": ["b"], "b": ["a"]})


def test_topological_order_rejects_a_longer_cycle():
    """A 3-node cycle (a -> b -> c -> a) is rejected, not just a direct 2-node one."""
    with pytest.raises(ValueError, match="Circular dependency"):
        topological_order(["a", "b", "c"], {"a": ["b"], "b": ["c"], "c": ["a"]})


def test_topological_order_uses_the_given_label_in_the_cycle_message():
    with pytest.raises(ValueError, match="Circular dependency in provisioner depends_on"):
        topological_order(["a", "b"], {"a": ["b"], "b": ["a"]}, label="provisioner depends_on")


def test_topological_order_default_label_is_items():
    with pytest.raises(ValueError, match="Circular dependency in items"):
        topological_order(["a", "b"], {"a": ["b"], "b": ["a"]})


def test_topological_order_ignores_an_unknown_dependency_name():
    """An unknown name in depends_on is not this function's job to catch
    (existence-checking stays the caller's job) — it must not crash, and
    must not block ordering of the name that declared it."""
    order = topological_order(["a"], {"a": ["ghost"]})
    assert order == ["a"]


def test_topological_order_empty_input():
    assert topological_order([], {}) == []


def test_topological_order_no_dependencies_at_all():
    order = topological_order(["a", "b", "c"], {})
    assert order == ["a", "b", "c"]
