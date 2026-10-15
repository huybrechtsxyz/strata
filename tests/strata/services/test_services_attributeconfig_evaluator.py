#!/usr/bin/env python3
"""Tests for the shared attributeconfig JSON Schema evaluator (Phase 2/4).

Evaluator unit tests only — no document-index wiring (that's Phase 3/4's
own controller-level tests in test_semantic_checks.py).
"""

from unittest.mock import patch

import pytest
from jsonschema.exceptions import SchemaError
from referencing.exceptions import Unresolvable

from strata.models.common_models import PlatformBaseModel
from strata.services.attributeconfig_evaluator import (
    build_validator,
    evaluate_selector,
    selectors_compatible,
    translate_validation_error,
)

_REGION_SCHEMA = {
    "type": "object",
    "required": ["region"],
    "properties": {
        "region": {"type": "string", "pattern": "^[a-z-]+$"},
        "count": {"type": "integer"},
    },
    "additionalProperties": False,
}


# -- build_validator ---------------------------------------------------------


def test_build_validator_compiles_a_valid_schema():
    """A well-formed schema compiles and evaluates cleanly against a matching value."""
    validator = build_validator(_REGION_SCHEMA)
    assert list(validator.iter_errors({"region": "eu-west"})) == []


def test_build_validator_rejects_a_malformed_schema():
    """A schema that isn't valid JSON Schema itself raises SchemaError at compile time."""
    with pytest.raises(SchemaError):
        build_validator({"type": "not-a-real-type"})


def test_build_validator_does_not_resolve_external_refs_at_compile_time():
    """check_schema() doesn't inspect $ref targets — only evaluation does (see module docstring)."""
    schema = {"type": "object", "properties": {"a": {"$ref": "https://example.com/evil.json"}}}
    validator = build_validator(schema)  # must not raise
    assert validator is not None


def test_evaluating_against_an_external_ref_raises_unresolvable_without_any_network_call():
    """SSRF regression: an unresolvable $ref fails closed, and never attempts a network fetch."""
    schema = {"type": "object", "properties": {"a": {"$ref": "https://example.com/evil.json"}}}
    validator = build_validator(schema)
    with patch("urllib.request.urlopen") as mock_urlopen, patch("requests.get") as mock_requests_get:
        with pytest.raises(Unresolvable):
            list(validator.iter_errors({"a": "x"}))
        mock_urlopen.assert_not_called()
        mock_requests_get.assert_not_called()


# -- translate_validation_error -----------------------------------------------


def test_translate_validation_error_maps_known_validator_keywords_to_stable_codes():
    """Each of the four named keywords maps to its own attributeconfig_* code."""
    validator = build_validator(_REGION_SCHEMA)

    missing_region = next(iter(validator.iter_errors({"count": 1})))
    code, _message, location = translate_validation_error(missing_region)
    assert code == "attributeconfig_field_missing"
    assert location == ""  # required fires at the object level, not one field

    bad_pattern = next(iter(validator.iter_errors({"region": "EU-WEST"})))
    code, _message, location = translate_validation_error(bad_pattern)
    assert code == "attributeconfig_pattern_mismatch"
    assert location == "region"

    bad_type = next(iter(validator.iter_errors({"region": "eu-west", "count": "x"})))
    code, _message, location = translate_validation_error(bad_type)
    assert code == "attributeconfig_type_mismatch"
    assert location == "count"

    extra_field = next(iter(validator.iter_errors({"region": "eu-west", "extra": True})))
    code, _message, location = translate_validation_error(extra_field)
    assert code == "attributeconfig_field_not_allowed"
    assert location == ""


def test_translate_validation_error_falls_back_for_unmapped_keywords():
    """A keyword with no dedicated code (e.g. `enum`) falls back to attributeconfig_validation_error."""
    validator = build_validator({"type": "string", "enum": ["a", "b"]})
    error = next(iter(validator.iter_errors("c")))
    code, _message, _location = translate_validation_error(error)
    assert code == "attributeconfig_validation_error"


# -- evaluate_selector ---------------------------------------------------------


class _Address(PlatformBaseModel):
    city: str


class _Resource(PlatformBaseModel):
    name: str


class _Document(PlatformBaseModel):
    provider_type: str
    address: _Address | None = None
    resources: list[_Resource] = []


def test_evaluate_selector_matches_a_simple_field():
    """A single-segment path compares the field directly."""
    doc = _Document(provider_type="kamatera")
    assert evaluate_selector(doc, {"provider_type": "kamatera"}) is True
    assert evaluate_selector(doc, {"provider_type": "azure"}) is False


def test_evaluate_selector_matches_a_nested_field():
    """A multi-segment path walks nested models via getattr."""
    doc = _Document(provider_type="kamatera", address=_Address(city="ghent"))
    assert evaluate_selector(doc, {"address.city": "ghent"}) is True
    assert evaluate_selector(doc, {"address.city": "antwerp"}) is False


def test_evaluate_selector_is_vacantly_false_for_a_missing_segment():
    """A path through a None/absent attribute never satisfies its constraint."""
    doc = _Document(provider_type="kamatera", address=None)
    assert evaluate_selector(doc, {"address.city": "ghent"}) is False


def test_evaluate_selector_list_suffix_matches_if_any_element_matches():
    """A `[]`-suffixed segment iterates the list; existential match, not positional."""
    doc = _Document(provider_type="kamatera", resources=[_Resource(name="vm"), _Resource(name="disk")])
    assert evaluate_selector(doc, {"resources[].name": "disk"}) is True
    assert evaluate_selector(doc, {"resources[].name": "network"}) is False


def test_evaluate_selector_ands_multiple_constraints():
    """Every key in the selector must be satisfied, not just one."""
    doc = _Document(provider_type="kamatera", address=_Address(city="ghent"))
    assert evaluate_selector(doc, {"provider_type": "kamatera", "address.city": "ghent"}) is True
    assert evaluate_selector(doc, {"provider_type": "kamatera", "address.city": "antwerp"}) is False


def test_evaluate_selector_is_vacantly_true_for_an_empty_selector():
    """No constraints means every document matches (the selector-less case, per Q16)."""
    doc = _Document(provider_type="kamatera")
    assert evaluate_selector(doc, {}) is True


# -- selectors_compatible (Q16) ------------------------------------------------


def test_selectors_compatible_when_no_shared_keys():
    """No field path is constrained by both selectors — vacantly compatible."""
    assert selectors_compatible({"spec.provider_type": "azure"}, {"spec.resource_type": "vm"}) is True


def test_selectors_compatible_when_shared_key_agrees():
    assert selectors_compatible({"spec.provider_type": "azure"}, {"spec.provider_type": "azure"}) is True


def test_selectors_incompatible_when_shared_key_disagrees():
    """A shared path with differing required values makes the two mutually
    exclusive — no document could ever satisfy both."""
    assert selectors_compatible({"spec.provider_type": "azure"}, {"spec.provider_type": "hetzner"}) is False


def test_selectors_compatible_when_either_is_empty():
    """A selector-less dict has no shared keys with anything — vacantly compatible."""
    assert selectors_compatible({}, {"spec.provider_type": "azure"}) is True
    assert selectors_compatible({}, {}) is True


def test_selectors_compatible_with_partial_overlap():
    """Only the shared key must agree; an unshared key on either side is unconstrained."""
    assert (
        selectors_compatible(
            {"spec.provider_type": "azure", "spec.region": "westeurope"},
            {"spec.provider_type": "azure", "spec.resource_type": "vm"},
        )
        is True
    )
    assert (
        selectors_compatible(
            {"spec.provider_type": "azure", "spec.region": "westeurope"},
            {"spec.provider_type": "hetzner", "spec.resource_type": "vm"},
        )
        is False
    )
