#!/usr/bin/env python3
"""Tests for strata.utils.dict_merge.deep_merge()."""

from strata.utils.dict_merge import deep_merge


class TestDeepMerge:
    """Recursive dict merge semantics."""

    def test_flat_scalar_override_wins(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        assert deep_merge(base, override) == {"a": 1, "b": 3, "c": 4}

    def test_nested_dict_merges_recursively(self):
        base = {"a": 1, "b": {"x": 1, "y": 2}}
        override = {"b": {"y": 3, "z": 4}, "c": 5}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": {"x": 1, "y": 3, "z": 4}, "c": 5}

    def test_nested_key_only_in_base_survives(self):
        """Regression: partially overriding a nested object must not drop base-only keys."""
        base = {"integration_config": {"flag1": True, "flag2": False}}
        override = {"integration_config": {"flag2": True}}
        result = deep_merge(base, override)
        assert result == {"integration_config": {"flag1": True, "flag2": True}}

    def test_mismatched_types_override_wholesale(self):
        base = {"a": {"x": 1}}
        override = {"a": "not-a-dict"}
        assert deep_merge(base, override) == {"a": "not-a-dict"}

    def test_list_values_replaced_not_concatenated(self):
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        assert deep_merge(base, override) == {"items": [4, 5]}

    def test_does_not_mutate_inputs(self):
        base = {"a": 1, "b": {"x": 1}}
        override = {"b": {"y": 2}}
        base_copy = {"a": 1, "b": {"x": 1}}
        override_copy = {"b": {"y": 2}}

        result = deep_merge(base, override)

        assert base == base_copy
        assert override == override_copy
        assert result is not base
        assert result is not override

    def test_empty_override_returns_equivalent_base(self):
        base = {"a": 1, "b": {"x": 1}}
        assert deep_merge(base, {}) == base

    def test_empty_base_returns_equivalent_override(self):
        override = {"a": 1, "b": {"x": 1}}
        assert deep_merge({}, override) == override
