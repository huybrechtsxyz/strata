#!/usr/bin/env python3
"""Tests for ArtifactModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.artifact_model import ArtifactModel


def _minimal_artifact() -> dict:
    return {
        "meta": {"name": "dspapi_container"},
        "spec": {
            "image_name": "int-docker-test/src/acme.dispatcher.api",
        },
    }


def test_artifact_minimal_is_valid():
    """A minimal artifact (identity + image_name only) validates."""
    model = ArtifactModel.model_validate(_minimal_artifact())
    assert model.meta.name == "dspapi_container"
    assert model.spec.image_name == "int-docker-test/src/acme.dispatcher.api"
    assert model.spec.image_tag is None
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "artifact"


def test_artifact_rejects_mismatched_kind():
    """A document declaring another kind is rejected (ADR-0016)."""
    data = _minimal_artifact()
    data["kind"] = "workspace"
    with pytest.raises(ValidationError, match="Expected kind 'artifact'"):
        ArtifactModel.model_validate(data)


def test_artifact_requires_image_name():
    """`image_name` is mandatory — an empty string is rejected."""
    data = _minimal_artifact()
    data["spec"]["image_name"] = ""
    with pytest.raises(ValidationError):
        ArtifactModel.model_validate(data)


def test_artifact_accepts_blank_image_tag():
    """A blank image_tag is valid — same 'not yet known' convention as
    today's real config-deploy variables."""
    data = _minimal_artifact()
    data["spec"]["image_tag"] = ""
    model = ArtifactModel.model_validate(data)
    assert model.spec.image_tag == ""


def test_artifact_accepts_full_spec():
    """Every optional field round-trips."""
    data = _minimal_artifact()
    data["spec"].update(
        {
            "image_tag": "env_sbx11006400201_20260907.10",
            "registry": "cdn-feeds.acme.com",
            "description": "Dispatcher API container image (ADR-0013)",
            "properties": {"team": "dispatcher"},
            "configuration": {"pull_policy": "always"},
            "custom": {"note": "internal"},
        }
    )
    model = ArtifactModel.model_validate(data)
    assert model.spec.image_tag == "env_sbx11006400201_20260907.10"
    assert model.spec.registry == "cdn-feeds.acme.com"
    assert model.spec.properties == {"team": "dispatcher"}
    assert model.spec.configuration == {"pull_policy": "always"}
    assert model.spec.custom == {"note": "internal"}


def test_artifact_accepts_integration_reference():
    """`spec.integration` names an Integration document by identity."""
    data = _minimal_artifact()
    data["spec"]["integration"] = "corp_registry_auth"
    model = ArtifactModel.model_validate(data)
    assert model.spec.integration == "corp_registry_auth"
