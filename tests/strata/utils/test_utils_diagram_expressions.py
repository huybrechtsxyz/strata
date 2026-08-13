#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_utils_diagram_expressions.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : Highlight condition grammar tests for strata CLI (ADR-0034).
===============================================================================
"""

import pytest

from strata.utils.diagram_expressions import ConditionError, parse_condition


class TestParseCondition:
    def test_equality(self):
        assert parse_condition("status == disabled") == "n.status == 'disabled'"

    def test_inequality(self):
        assert parse_condition("status != valid") == "n.status != 'valid'"

    def test_membership(self):
        assert parse_condition("severity in [critical, high]") == "n.severity in ['critical', 'high']"

    def test_dotted_field(self):
        assert parse_condition("metadata.role == web") == "n.metadata.role == 'web'"

    def test_node_var_is_configurable(self):
        assert parse_condition("status == valid", node_var="item") == "item.status == 'valid'"

    def test_quoted_values_are_unwrapped_once(self):
        assert parse_condition("status == 'disabled'") == "n.status == 'disabled'"
        assert parse_condition('status == "disabled"') == "n.status == 'disabled'"

    def test_surrounding_whitespace_is_tolerated(self):
        assert parse_condition("  status   ==   disabled  ") == "n.status == 'disabled'"

    def test_values_are_quoted_not_interpolated(self):
        """An authored value must never become part of the expression itself."""
        parsed = parse_condition("status == 1 == 1")
        assert parsed == "n.status == '1 == 1'"

    def test_list_quoting_survives_injection_attempt(self):
        parsed = parse_condition("kind in [a' or 'x, b]")
        assert parsed == "n.kind in [\"a' or 'x\", 'b']"


class TestConditionErrors:
    @pytest.mark.parametrize(
        "condition",
        ["", "status", "status ~ disabled", "== disabled", "1status == x", "status.. == x"],
    )
    def test_malformed_conditions_are_rejected(self, condition):
        with pytest.raises(ConditionError):
            parse_condition(condition)

    def test_error_names_the_supported_operators(self):
        with pytest.raises(ConditionError, match=r"'=='.*'!='.*'in'"):
            parse_condition("status")

    def test_in_without_a_list_is_rejected(self):
        with pytest.raises(ConditionError, match="bracketed list"):
            parse_condition("severity in critical")

    def test_empty_list_is_rejected(self):
        """A condition that can never match is a mistake, not a valid rule."""
        with pytest.raises(ConditionError, match="can never match"):
            parse_condition("severity in []")

    def test_list_with_equality_suggests_in(self):
        with pytest.raises(ConditionError, match="Use 'in' to test membership"):
            parse_condition("severity == [critical, high]")
