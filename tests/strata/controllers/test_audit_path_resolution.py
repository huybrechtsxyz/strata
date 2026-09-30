#!/usr/bin/env python3
"""Tests for resolve_audit_relative_path() — docs/design/audit-trail.md's
"Path resolution — shared by the local write and every push destination".

Phase 3 of that doc's Layer 2 Implementation Plan.
"""

from pathlib import Path

from strata.controllers.audit_path_resolution import resolve_audit_relative_path
from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_model import DeploymentModel

_STARTED_AT = "2026-09-30T14:30:00Z"


def _deployment(**spec_overrides) -> DeploymentModel:
    spec = {"partial": True, "workspace": "main", **spec_overrides}
    return DeploymentModel.model_validate({"meta": {"name": "c0224-prod"}, "spec": spec})


def _configuration_with_hub_path_convention() -> ConfigurationModel:
    data = {
        "meta": {"name": "cfg"},
        "spec": {
            "paths": [
                {
                    "name": "hub-path",
                    "scope": "deploy/hubs/**",
                    "pattern": "deploy/hubs/{hub}/{spoke}/{customer}/{ring}",
                    "resolves": "layers",
                    "segments": [
                        {"name": "hub", "pattern": "^[a-z0-9]+$"},
                        {"name": "spoke", "pattern": "^[a-z0-9]+$"},
                        {"name": "customer", "pattern": "^[a-z0-9]+$"},
                        {"name": "ring", "pattern": "^[a-z]+$"},
                    ],
                }
            ]
        },
    }
    return ConfigurationModel.model_validate(data)


def test_derives_path_from_layers_segments_in_convention_order():
    deployment = _deployment(
        layers={
            "follows": "hub-path",
            "segments": {"ring": "prod", "hub": "z00", "customer": "c0224", "spoke": "s01"},
        }
    )
    configuration = _configuration_with_hub_path_convention()
    relative = resolve_audit_relative_path(deployment, configuration, _STARTED_AT)
    assert relative == Path("z00", "s01", "c0224", "prod", "20260930-143000")


def test_falls_back_to_workspace_deployment_when_no_layers_block():
    deployment = _deployment()
    relative = resolve_audit_relative_path(deployment, None, _STARTED_AT)
    assert relative == Path("main", "c0224-prod", "20260930-143000")


def test_falls_back_when_layers_follows_but_no_configuration_given():
    deployment = _deployment(layers={"follows": "hub-path", "segments": {"hub": "z00"}})
    relative = resolve_audit_relative_path(deployment, None, _STARTED_AT)
    assert relative == Path("main", "c0224-prod", "20260930-143000")


def test_falls_back_when_convention_name_unknown():
    deployment = _deployment(layers={"follows": "no-such-convention", "segments": {"hub": "z00"}})
    configuration = _configuration_with_hub_path_convention()
    relative = resolve_audit_relative_path(deployment, configuration, _STARTED_AT)
    assert relative == Path("main", "c0224-prod", "20260930-143000")


def test_falls_back_when_segments_incomplete():
    """A convention segment with no matching value in layers.segments — never raises,
    falls back instead (semantic_checks.py's job to report the mismatch, not this one's)."""
    deployment = _deployment(layers={"follows": "hub-path", "segments": {"hub": "z00"}})
    configuration = _configuration_with_hub_path_convention()
    relative = resolve_audit_relative_path(deployment, configuration, _STARTED_AT)
    assert relative == Path("main", "c0224-prod", "20260930-143000")


def test_falls_back_when_layers_segments_absent():
    deployment = _deployment(layers={"follows": "hub-path"})
    configuration = _configuration_with_hub_path_convention()
    relative = resolve_audit_relative_path(deployment, configuration, _STARTED_AT)
    assert relative == Path("main", "c0224-prod", "20260930-143000")


def test_timestamp_leaf_is_windows_safe_no_colons():
    deployment = _deployment()
    relative = resolve_audit_relative_path(deployment, None, _STARTED_AT)
    assert ":" not in relative.parts[-1]


def test_timestamp_leaf_always_appended_even_in_fallback_case():
    deployment = _deployment()
    relative = resolve_audit_relative_path(deployment, None, _STARTED_AT)
    assert relative.parts[-1] == "20260930-143000"
