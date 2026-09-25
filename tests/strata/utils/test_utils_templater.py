#!/usr/bin/env python3
"""Tests for `templater.validate_template_references()` (docs/design/
value-token-resolution.md, Value Supply Mechanisms option C — static
reference validation only, no rendering)."""

from strata.utils.templater import validate_template_references

KNOWN_NAMES = {
    "graph": None,
    "variables": {"REGION"},
    "flags": {"NEW_UI"},
    "secrets": {"DB_PASSWORD"},
    "properties": None,
    "custom": None,
    "provisioner": None,
}


def test_valid_references_produce_no_errors():
    source = '{"region": "{{ variables.REGION }}", "flag": {{ flags.NEW_UI }}}'
    assert validate_template_references(source, KNOWN_NAMES) == []


def test_unknown_root_name_is_reported():
    source = "{{ varaibles.REGION }}"
    errors = validate_template_references(source, KNOWN_NAMES)
    assert len(errors) == 1
    assert "'varaibles'" in errors[0]
    assert "not a known template variable" in errors[0]


def test_unknown_nested_key_is_reported():
    source = "{{ variables.REGOIN }}"
    errors = validate_template_references(source, KNOWN_NAMES)
    assert len(errors) == 1
    assert "'variables.REGOIN' is not declared" in errors[0]


def test_unknown_secret_key_is_reported():
    source = "{{ secrets.GHOST_SECRET }}"
    errors = validate_template_references(source, KNOWN_NAMES)
    assert "'secrets.GHOST_SECRET' is not declared" in errors[0]


def test_unchecked_root_nested_keys_are_never_flagged():
    """properties/custom have no known nested-key set (arbitrary shape) — only
    the root name is checked, never its attributes."""
    source = "{{ properties.anything_at_all }}"
    assert validate_template_references(source, KNOWN_NAMES) == []


def test_dynamic_key_access_is_skipped_not_flagged():
    """variables[some_var] can't be statically checked — not an error, just unchecked."""
    source = "{% set key = 'REGION' %}{{ variables[key] }}"
    assert validate_template_references(source, KNOWN_NAMES) == []


def test_item_access_with_a_literal_string_is_checked_like_attribute_access():
    source = "{{ variables['REGOIN'] }}"
    errors = validate_template_references(source, KNOWN_NAMES)
    assert "'variables.REGOIN' is not declared" in errors[0]


def test_template_syntax_error_is_reported_not_raised():
    errors = validate_template_references("{{ variables.REGION ", KNOWN_NAMES)
    assert len(errors) == 1
    assert "template syntax error" in errors[0]


def test_multiple_problems_are_all_reported():
    source = "{{ varaibles.REGION }} {{ variables.REGOIN }}"
    errors = validate_template_references(source, KNOWN_NAMES)
    assert len(errors) == 2
