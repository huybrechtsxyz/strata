#!/usr/bin/env python3
"""Tests for the shared `attributeconfig` evaluator building blocks — `reshape_block_value()` specifically
(the other functions here are already covered indirectly via `test_semantic_checks.py`'s end-to-end cases).
"""

from strata.models.store_model import FeatureStoreModel, FeatureStoreType, VariableStoreModel, VariableStoreType
from strata.services.attributeconfig_evaluator import reshape_block_value


def test_reshape_passes_through_properties_custom_configuration_unchanged():
    """These blocks are already JSON-shaped dicts — no reshape needed."""
    value = {"owner": "platform-team", "retries": 3}
    assert reshape_block_value(value, "properties") == value
    assert reshape_block_value(value, "custom") == value
    assert reshape_block_value(value, "configuration") == value


def test_reshape_treats_unset_as_empty_dict_for_every_block():
    """D7: an unset block is checked as `{}`, not skipped."""
    assert reshape_block_value(None, "properties") == {}
    assert reshape_block_value(None, "variables") == {}
    assert reshape_block_value(None, "features") == {}


def test_reshape_keys_variables_by_their_own_key():
    variables = [
        VariableStoreModel(key="REGION", store=VariableStoreType.CONSTANT, value="westeurope"),
        VariableStoreModel(key="DB_HOST", store=VariableStoreType.CONSTANT, value=""),
    ]
    reshaped = reshape_block_value(variables, "variables")
    assert set(reshaped) == {"REGION", "DB_HOST"}
    assert reshaped["REGION"]["value"] == "westeurope"
    assert reshaped["DB_HOST"]["value"] == ""


def test_reshape_dumps_the_whole_store_entry_not_just_value():
    """`value`/`type` are both typed `Any` on the real model — a schema may need `store`/`type` alongside
    `value`, so the full entry is dumped, not just `.value`."""
    variables = [VariableStoreModel(key="REGION", store=VariableStoreType.CONSTANT, value="westeurope", type="string")]
    reshaped = reshape_block_value(variables, "variables")
    assert reshaped["REGION"] == {
        "key": "REGION",
        "store": "constant",
        "value": "westeurope",
        "type": "string",
        "version": None,
        "description": None,
        "default": None,
        "field": None,
    }


def test_reshape_keys_features_by_their_own_key():
    features = [FeatureStoreModel(key="ENABLE_AKS", store=FeatureStoreType.CONSTANT, value="true")]
    reshaped = reshape_block_value(features, "features")
    assert reshaped == {
        "ENABLE_AKS": {
            "key": "ENABLE_AKS",
            "store": "constant",
            "value": "true",
            "version": None,
            "description": None,
            "default": None,
        }
    }


def test_reshape_store_type_is_json_shaped_not_an_enum_instance():
    """`model_dump(mode="json")` must serialize `VariableStoreType`/`FeatureStoreType` to plain strings, so a
    `{ const: "constant" }` schema rule can match it directly."""
    variables = [VariableStoreModel(key="REGION", store=VariableStoreType.CONSTANT, value="westeurope")]
    reshaped = reshape_block_value(variables, "variables")
    assert reshaped["REGION"]["store"] == "constant"
    assert isinstance(reshaped["REGION"]["store"], str)
