#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_utils_design_tokens.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : Design-system token tests for strata CLI (ADR-0034).
===============================================================================
"""

import pytest

from strata.utils.design_tokens import DESIGN_TOKENS, mermaid_escape, resolve_token
from strata.utils.templater import TemplateProcessor


class TestResolveToken:
    def test_returns_a_classdef_body(self):
        assert resolve_token("critical") == "fill:#f8d7da,stroke:#dc3545"

    def test_fill_part(self):
        assert resolve_token("high", "fill") == "#ffe5d0"

    def test_stroke_part(self):
        assert resolve_token("high", "stroke") == "#fd7e14"

    def test_unknown_token_falls_back_to_neutral(self):
        """A diagram colouring by an unanticipated field value must still render."""
        assert resolve_token("no_such_token") == resolve_token("neutral")

    def test_lookup_is_case_and_whitespace_insensitive(self):
        assert resolve_token("  CRITICAL ") == resolve_token("critical")

    def test_unknown_part_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown token part"):
            resolve_token("critical", "border")

    def test_severity_ramp_is_shared_across_domains(self):
        """'red = critical' must mean the same thing in every diagram."""
        assert resolve_token("critical") == resolve_token("deny") == resolve_token("failing")
        assert resolve_token("info") == resolve_token("unknown") == resolve_token("audit")

    def test_promotion_and_gate_outcomes_alias_the_same_ramp(self):
        """A template must not have to translate PromotionOutcome/gate values first."""
        assert resolve_token("completed") == resolve_token("success")
        assert resolve_token("rolled-back") == resolve_token("failed")
        assert resolve_token("passed") == resolve_token("success")

    def test_every_token_has_a_fill_and_a_stroke(self):
        for name, value in DESIGN_TOKENS.items():
            assert len(value) == 2, name
            assert all(hex_value.startswith("#") for hex_value in value), name


class TestMermaidEscape:
    def test_quotes_are_escaped(self):
        """Mermaid closes a quoted label on the first unescaped quote."""
        assert mermaid_escape('say "hi"') == "say #quot;hi#quot;"

    def test_angle_brackets_are_escaped(self):
        assert mermaid_escape("a <b> c") == "a #lt;b#gt; c"

    def test_newlines_become_line_breaks(self):
        assert mermaid_escape("one\ntwo") == "one<br/>two"
        assert mermaid_escape("one\r\ntwo") == "one<br/>two"

    def test_plain_text_is_unchanged(self):
        assert mermaid_escape("app_server") == "app_server"

    def test_non_strings_are_coerced(self):
        assert mermaid_escape(42) == "42"


class TestFiltersAreRegistered:
    def test_slug(self):
        assert TemplateProcessor.render("{{ '@haven/a-b.yaml' | slug }}", {}) == "at_haven_a_b"

    def test_token(self):
        assert TemplateProcessor.render("{{ 'valid' | token }}", {}) == "fill:#d4edda,stroke:#28a745"

    def test_mermaid_escape(self):
        assert TemplateProcessor.render("{{ v | mermaid_escape }}", {"v": 'a"b'}) == "a#quot;b"

    def test_templates_using_them_now_pass_syntax_check(self):
        """These used to fail validation with 'No filter named ...'."""
        template = "{{ a | slug }}{{ b | token }}{{ c | mermaid_escape }}"
        assert TemplateProcessor.check_syntax(template) is None
        assert TemplateProcessor.find_variables(template) == {"a", "b", "c"}

    def test_available_in_the_strict_environment_too(self):
        assert TemplateProcessor.render_strict("{{ v | slug }}", {"v": "a-b"}) == "a_b"
