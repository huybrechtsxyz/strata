#!/usr/bin/env python3
"""Tests for the deep_merge utility."""

from strata.utils.dict_merge import deep_merge


def test_override_wins_on_scalars():
    """A conflicting scalar takes the override's value."""
    assert deep_merge({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


def test_keys_from_either_side_are_kept():
    """Keys present on only one side carry through."""
    assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


def test_nested_dicts_merge_per_leaf_key():
    """Overriding one field of a block keeps that block's other fields.

    This is the behaviour a shallow merge gets wrong: the siblings vanish and
    fall back to schema defaults with no error.
    """
    base = {"locking": {"enabled": True, "strategy": "wrap", "wait_timeout": 600}}
    merged = deep_merge(base, {"locking": {"wait_timeout": 900}})
    assert merged["locking"] == {"enabled": True, "strategy": "wrap", "wait_timeout": 900}


def test_merging_is_recursive_to_any_depth():
    """Nesting deeper than one level still merges per leaf."""
    base = {"a": {"b": {"c": 1, "d": 2}}}
    assert deep_merge(base, {"a": {"b": {"d": 3}}}) == {"a": {"b": {"c": 1, "d": 3}}}


def test_lists_are_replaced_not_concatenated():
    """Element semantics belong to the caller who knows what the list means."""
    assert deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}


def test_type_mismatch_replaces_wholesale():
    """A dict replaced by a scalar (or vice versa) takes the override."""
    assert deep_merge({"a": {"b": 1}}, {"a": "scalar"}) == {"a": "scalar"}
    assert deep_merge({"a": "scalar"}, {"a": {"b": 1}}) == {"a": {"b": 1}}


def test_explicit_none_overrides():
    """An explicit null is a value, not an absence."""
    assert deep_merge({"a": 1}, {"a": None}) == {"a": None}


def test_inputs_are_not_mutated():
    """Merging is pure — callers reuse the base for several overrides."""
    base = {"a": {"b": 1}}
    override = {"a": {"c": 2}}
    deep_merge(base, override)
    assert base == {"a": {"b": 1}}
    assert override == {"a": {"c": 2}}


def test_nested_result_is_a_copy():
    """Mutating the result must not reach back into the inputs."""
    base = {"a": {"b": 1}}
    merged = deep_merge(base, {"a": {"c": 2}})
    merged["a"]["b"] = 999
    assert base["a"]["b"] == 1


def test_empty_operands():
    """Merging with an empty dict returns the other side's content."""
    assert deep_merge({}, {"a": 1}) == {"a": 1}
    assert deep_merge({"a": 1}, {}) == {"a": 1}
