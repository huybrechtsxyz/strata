#!/usr/bin/env python3
"""Tests for the security gate policy models (docs/design/security-gates.md D3, section 6.1)."""

import pytest
from pydantic import ValidationError

from strata.models.security_gate_model import ImageGatePolicyModel, ReleaseGatePolicyModel


def _release(**overrides) -> dict:
    return {"integration": "aikido-ci", "repository": "huybrechtsxyz/strata-infra", **overrides}


def test_release_gate_defaults():
    policy = ReleaseGatePolicyModel.model_validate(_release())
    assert policy.enforcement == "deny"
    assert policy.on_missing_data == "block"
    assert policy.timeout == 900
    assert policy.poll_interval == 10
    assert policy.commit is None


def test_release_gate_accepts_every_field():
    policy = ReleaseGatePolicyModel.model_validate(
        _release(enforcement="warn", on_missing_data="skip", timeout=60, poll_interval=5, commit="A" * 40)
    )
    assert policy.enforcement == "warn"
    assert policy.commit == "A" * 40


@pytest.mark.parametrize(
    "overrides",
    [
        {"timeout": 29},
        {"timeout": 7201},
        {"poll_interval": 1},
        {"poll_interval": 301},
        {"timeout": 30, "poll_interval": 30},
        {"enforcement": "block"},
        {"on_missing_data": "ignore"},
        {"commit": "abc1234"},
        {"commit": "g" * 40},
        {"repository": ""},
        {"fail_on": ["iac"]},
    ],
)
def test_release_gate_rejects(overrides):
    with pytest.raises(ValidationError):
        ReleaseGatePolicyModel.model_validate(_release(**overrides))


@pytest.mark.parametrize("missing", ["integration", "repository"])
def test_release_gate_requires(missing):
    data = _release()
    del data[missing]
    with pytest.raises(ValidationError):
        ReleaseGatePolicyModel.model_validate(data)


def test_image_gate_defaults():
    policy = ImageGatePolicyModel.model_validate({"integration": "aikido-api"})
    assert policy.enforcement == "deny"
    assert policy.on_missing_data == "block"
    assert policy.max_severity == "HIGH"
    assert policy.max_count == 0
    assert policy.on_unknown_image == "warn"


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_severity": "UNKNOWN"},
        {"max_severity": "high"},
        {"max_count": -1},
        {"on_unknown_image": "fail"},
        {"name_strategy": "full"},
    ],
)
def test_image_gate_rejects(overrides):
    with pytest.raises(ValidationError):
        ImageGatePolicyModel.model_validate({"integration": "aikido-api", **overrides})


def test_image_gate_requires_integration():
    with pytest.raises(ValidationError):
        ImageGatePolicyModel.model_validate({})
