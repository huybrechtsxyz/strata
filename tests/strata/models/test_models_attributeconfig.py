#!/usr/bin/env python3
"""Tests for AttributeConfigModel (standalone attribute schema kind) validation.

Phase 1 only — these tests cover the Pydantic/structural shape (target
binding, `schema` passthrough, kind matching). Schema-compiles-as-real-
JSON-Schema and schema-applied-to-matching-documents checks land with the
Phase 2 evaluator; see docs/design/dynamic-configuration-validation.md.
"""

import pytest
from pydantic import ValidationError

from strata.models.attributeconfig_model import AttributeConfigModel


def _minimal_attributeconfig() -> dict:
    return {
        "meta": {"name": "environment-custom"},
        "spec": {
            "target": {"kind": "environment", "block": "custom"},
            "schema": {"type": "object", "properties": {"region": {"type": "string"}}},
        },
    }


def test_attributeconfig_minimal_is_valid():
    """A minimal attribute config document validates successfully."""
    model = AttributeConfigModel.model_validate(_minimal_attributeconfig())
    assert model.meta.name == "environment-custom"
    assert model.spec.target.kind.value == "environment"
    assert model.spec.target.block == "custom"
    assert model.spec.target.selector is None
    assert model.spec.schema_ == {"type": "object", "properties": {"region": {"type": "string"}}}
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "attributeconfig"


def test_attributeconfig_rejects_mismatched_kind():
    """A document declaring another kind is rejected (see validate_kind_matches)."""
    data = _minimal_attributeconfig()
    data["kind"] = "providerconfig"
    with pytest.raises(ValidationError, match="Expected kind 'attributeconfig'"):
        AttributeConfigModel.model_validate(data)


def test_attributeconfig_rejects_invalid_block():
    """target.block only accepts 'properties', 'custom', or 'configuration'."""
    data = _minimal_attributeconfig()
    data["spec"]["target"]["block"] = "spec"
    with pytest.raises(ValidationError):
        AttributeConfigModel.model_validate(data)


def test_attributeconfig_accepts_selector():
    """target.selector narrows which documents of `kind` this schema applies to."""
    data = _minimal_attributeconfig()
    data["spec"]["target"]["selector"] = {"spec.provider": "kamatera"}
    model = AttributeConfigModel.model_validate(data)
    assert model.spec.target.selector == {"spec.provider": "kamatera"}


def test_attributeconfig_schema_accepts_arbitrary_json_schema_content():
    """spec.schema is a verbatim passthrough — any JSON-serializable dict is accepted here."""
    data = _minimal_attributeconfig()
    data["spec"]["schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["region"],
        "properties": {"region": {"type": "string", "pattern": "^[a-z-]+$"}},
        "additionalProperties": False,
    }
    model = AttributeConfigModel.model_validate(data)
    assert model.spec.schema_["required"] == ["region"]


def test_attributeconfig_round_trips_schema_alias():
    """model_dump(by_alias=True) serializes spec.schema_ back under the real 'schema' key."""
    model = AttributeConfigModel.model_validate(_minimal_attributeconfig())
    dumped = model.spec.model_dump(by_alias=True)
    assert "schema" in dumped
    assert "schema_" not in dumped
