#!/usr/bin/env python3
"""Tests for the deployment metrics model — `_metrics.json` (docs/design/audit-trail.md)."""

import pytest
from pydantic import ValidationError

from strata.models.audit_metrics_model import (
    DeploymentMetricsModel,
    ErrorCategory,
)


def _minimal_metrics(**overrides) -> dict:
    data = {
        "execution_id": "11111111-1111-1111-1111-111111111111",
        "deployment": "fabrikam-prod",
        "workspace": "main",
        "timestamp": "2026-09-30T14:30:00Z",
        "dimensions": {"outcome": "success", "action": "deploy"},
        "measures": {"duration_seconds": 123.0, "stages_total": 3, "stages_failed": 0},
    }
    data.update(overrides)
    return data


def test_minimal_metrics_is_valid():
    model = DeploymentMetricsModel.model_validate(_minimal_metrics())
    assert model.dimensions.outcome == "success"
    assert model.measures.duration_seconds == 123.0


def test_metrics_default_label_safe_excludes_unbounded_fields():
    model = DeploymentMetricsModel.model_validate(_minimal_metrics())
    assert "actor" not in model.label_safe
    assert "commit_sha" not in model.label_safe
    assert "execution_id" not in model.label_safe
    assert "outcome" in model.label_safe


def test_metrics_drop_the_sections_nothing_measured():
    # docs/design/audit-trail.md, History: no cost, CVE or drift section was ever written.
    with pytest.raises(ValidationError, match="sections"):
        DeploymentMetricsModel.model_validate(_minimal_metrics(sections={"cost": {}}))


def test_metrics_measures_drop_resource_changes():
    measures = {"duration_seconds": 1.0, "stages_total": 1, "stages_failed": 0, "resource_changes": 3}
    with pytest.raises(ValidationError, match="resource_changes"):
        DeploymentMetricsModel.model_validate(_minimal_metrics(measures=measures))


def test_metrics_error_category_requires_failed_outcome():
    data = _minimal_metrics(error_category="timeout")  # outcome still 'success'
    with pytest.raises(ValidationError, match="error_category may only be set"):
        DeploymentMetricsModel.model_validate(data)


def test_metrics_error_category_allowed_on_failed_outcome():
    data = _minimal_metrics(error_category="timeout")
    data["dimensions"]["outcome"] = "failed"
    model = DeploymentMetricsModel.model_validate(data)
    assert model.error_category is ErrorCategory.TIMEOUT


def test_metrics_rejects_unknown_error_category():
    data = _minimal_metrics(error_category="something-else")
    data["dimensions"]["outcome"] = "failed"
    with pytest.raises(ValidationError):
        DeploymentMetricsModel.model_validate(data)


def test_metrics_rejects_unknown_field():
    data = _minimal_metrics(unexpected_field="nope")
    with pytest.raises(ValidationError):
        DeploymentMetricsModel.model_validate(data)
