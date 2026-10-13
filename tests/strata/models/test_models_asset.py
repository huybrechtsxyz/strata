#!/usr/bin/env python3
"""Tests for AssetModel YAML validation (docs/design/asset-management.md)."""

import pytest
from pydantic import ValidationError

from strata.models.asset_model import AssetModel


def _minimal_asset() -> dict:
    return {
        "meta": {"name": "openapi"},
        "spec": {
            "source_path": "assets/contoso/{deploy_tier}/openapi",
        },
    }


@pytest.mark.parametrize(
    "source_path",
    [
        pytest.param("../../etc/passwd", id="parent-traversal"),
        pytest.param("assets/../../../etc", id="embedded-traversal"),
        pytest.param("/etc/passwd", id="absolute-posix"),
        pytest.param("C:/Windows/System32", id="absolute-windows-drive"),
        pytest.param("\\\\server\\share", id="absolute-unc"),
    ],
)
def test_asset_rejects_traversing_or_absolute_source_path(source_path: str):
    """Review finding 1 — `source_path` must be relative, the same guard
    `SourceModel.source_path` already applies. An absolute path is worse than
    it looks: `repo_root / '/etc/passwd'` discards `repo_root` entirely in
    pathlib, so it would read straight from the filesystem root."""
    data = _minimal_asset()
    data["spec"]["source_path"] = source_path
    with pytest.raises(ValidationError):
        AssetModel.model_validate(data)


@pytest.mark.parametrize(
    "source_path",
    [
        pytest.param("assets/openapi/*.json", id="star"),
        pytest.param("assets/openapi/**/*.json", id="globstar"),
        pytest.param("assets/openapi/api?.json", id="question-mark"),
        pytest.param("assets/openapi/[ab].json", id="character-class"),
        pytest.param("assets/{deploy_tier}/openapi/*.json", id="token-plus-glob"),
    ],
)
def test_asset_accepts_glob_source_path(source_path: str):
    """Review finding 3 — wildcards are legitimate `source_path` syntax and
    must survive the traversal validator untouched, including alongside a
    `{name}` token."""
    data = _minimal_asset()
    data["spec"]["source_path"] = source_path
    assert AssetModel.model_validate(data).spec.source_path == source_path


def test_asset_minimal_is_valid():
    """A minimal asset (identity + source_path only) validates."""
    model = AssetModel.model_validate(_minimal_asset())
    assert model.meta.name == "openapi"
    assert model.spec.source_path == "assets/contoso/{deploy_tier}/openapi"
    assert model.spec.remote is None
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "asset"


def test_asset_rejects_mismatched_kind():
    """A document declaring another kind is rejected (ADR-0016)."""
    data = _minimal_asset()
    data["kind"] = "workspace"
    with pytest.raises(ValidationError, match="Expected kind 'asset'"):
        AssetModel.model_validate(data)


def test_asset_requires_source_path():
    """`source_path` is mandatory — an empty string is rejected."""
    data = _minimal_asset()
    data["spec"]["source_path"] = ""
    with pytest.raises(ValidationError):
        AssetModel.model_validate(data)


def test_asset_accepts_full_spec():
    """Every optional field round-trips, including a named remote."""
    data = _minimal_asset()
    data["spec"].update(
        {
            "remote": "assets-repo",
            "description": "OpenAPI contract files for the Contoso API Gateway",
        }
    )
    model = AssetModel.model_validate(data)
    assert model.spec.remote == "assets-repo"
    assert model.spec.description == "OpenAPI contract files for the Contoso API Gateway"


def test_asset_rejects_unknown_fields():
    """`extra='forbid'` (PlatformBaseModel) rejects a typo'd field."""
    data = _minimal_asset()
    data["spec"]["soruce_path"] = "typo"
    with pytest.raises(ValidationError):
        AssetModel.model_validate(data)
