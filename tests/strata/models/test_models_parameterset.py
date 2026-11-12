#!/usr/bin/env python3
"""Tests for ParameterSetModel (`base-parameters-v2.md` (work doc), plan step 1a)."""

import pytest
from pydantic import ValidationError

from strata.models.common_models import PlatformKind
from strata.models.parameterset_model import ParameterSetModel
from strata.services.base_service import diagnostics_from_validation_error


def _set(values: object, **meta: object) -> dict:
    return {"meta": {"name": "spoke-acme-001", **meta}, "spec": {"values": values}}


def test_a_set_holds_any_yaml():
    values = {
        "name": "acme-001",
        "seq": "001",
        "count": 3,
        "enabled": True,
        "network": {"cidr": "10.1.0.0/16", "dns": ["10.1.0.4", "10.1.0.5"]},
        "customers": [{"code": "acmep01"}, {"code": "acmep02"}],
        "nothing": None,
    }
    model = ParameterSetModel.model_validate(_set(values, labels={"role": "spoke"}))
    assert model.kind is PlatformKind.PARAMETERSET
    assert model.spec.values == values
    assert model.meta.labels == {"role": "spoke"}


def test_an_empty_set_is_valid():
    assert ParameterSetModel.model_validate(_set({})).spec.values == {}


def test_values_are_required():
    with pytest.raises(ValidationError):
        ParameterSetModel.model_validate({"meta": {"name": "spoke-acme-001"}, "spec": {}})


def test_values_must_be_a_map():
    with pytest.raises(ValidationError):
        ParameterSetModel.model_validate(_set(["acme-001"]))


@pytest.mark.parametrize("field", ["extends", "partial", "params"])
def test_a_set_has_no_extends_partial_or_params(field):
    data = _set({"name": "acme-001"})
    data["spec"][field] = "x"
    with pytest.raises(ValidationError):
        ParameterSetModel.model_validate(data)


@pytest.mark.parametrize(
    ("values", "location"),
    [
        ({"tenant": "${var:tenant}"}, "values.tenant"),
        ({"name": "kv-${param:spoke.code}"}, "values.name"),
        ({"network": {"cidr": "${value:network.hub.spec.cidr}"}}, "values.network.cidr"),
        ({"customers": ["acmep01", "${secret:customer}"]}, "values.customers[1]"),
        ({"${var:key}": "x"}, "values.${var:key}"),
        ({"name": "${unknown}"}, "values.name"),
    ],
)
def test_a_token_in_a_value_or_key_is_an_error_naming_where(values, location):
    with pytest.raises(ValidationError) as caught:
        ParameterSetModel.model_validate(_set(values))
    (finding,) = diagnostics_from_validation_error(caught.value).errors
    assert finding.location == "spec.values"
    assert location in finding.message
    assert "plain values" in finding.message


def test_every_token_is_named_in_one_message():
    with pytest.raises(ValidationError) as caught:
        ParameterSetModel.model_validate(_set({"a": "${var:a}", "b": {"c": "${var:c}"}}))
    message = str(caught.value)
    assert "values.a" in message
    assert "values.b.c" in message


def test_an_escaped_token_is_literal_text():
    values = {"template": "$${var:region}", "nested": {"chart": ["$${TOKEN}"]}}
    assert ParameterSetModel.model_validate(_set(values)).spec.values == values


def test_a_dollar_without_braces_is_plain_text():
    values = {"price": "$5", "regex": "^a$", "brace": "{x}"}
    assert ParameterSetModel.model_validate(_set(values)).spec.values == values


def test_a_wrong_kind_is_rejected():
    data = _set({"name": "acme-001"})
    data["kind"] = "environment"
    with pytest.raises(ValidationError, match="Expected kind 'parameterset'"):
        ParameterSetModel.model_validate(data)


def test_the_name_must_be_a_platform_name():
    with pytest.raises(ValidationError):
        ParameterSetModel.model_validate({"meta": {"name": "Spoke ACME"}, "spec": {"values": {}}})
