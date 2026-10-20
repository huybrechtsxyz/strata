#!/usr/bin/env python3
"""Tests for AssetModel YAML validation (docs/design/asset-management.md)."""

import pytest
from pydantic import ValidationError

from strata.models.asset_model import AssetModel
from strata.models.common_models import PlatformKind
from strata.models.reference_fields import extract_references
from strata.services.base_service import diagnostics_from_validation_error


def _location() -> dict:
    return {"type": "git", "url": "https://example.com/c0062.git", "reference": "v1.4.0"}


def _located_asset(**location_overrides) -> dict:
    return {
        "meta": {"name": "c0062-api"},
        "spec": {"source_path": "openapi", "location": {**_location(), **location_overrides}},
    }


def test_asset_accepts_a_location():
    model = AssetModel.model_validate(_located_asset())
    location = model.spec.location
    assert (location.url, location.reference, location.fetch.value) == (
        "https://example.com/c0062.git",
        "v1.4.0",
        "strata",
    )


def test_asset_location_type_defaults_to_git():
    data = _located_asset()
    del data["spec"]["location"]["type"]
    assert AssetModel.model_validate(data).spec.location.type.value == "git"


def test_asset_rejects_remote_and_location_together():
    data = _located_asset()
    data["spec"]["remote"] = "infra"
    with pytest.raises(ValidationError, match="either 'remote' or 'location'"):
        AssetModel.model_validate(data)


@pytest.mark.parametrize("asset_type", ["oci", "helm", "local"])
def test_asset_location_supports_only_git(asset_type: str):
    with pytest.raises(ValidationError, match="only type 'git'"):
        AssetModel.model_validate(_located_asset(type=asset_type))


def test_asset_location_requires_a_reference():
    data = _located_asset()
    del data["spec"]["location"]["reference"]
    with pytest.raises(ValidationError):
        AssetModel.model_validate(data)


@pytest.mark.parametrize("reference", ["../x", "/abs", "a/../b"])
def test_asset_location_rejects_a_traversing_reference(reference: str):
    with pytest.raises(ValidationError):
        AssetModel.model_validate(_located_asset(reference=reference))


def test_asset_location_allows_a_slash_in_a_branch_reference():
    assert AssetModel.model_validate(_located_asset(reference="feature/foo")).spec.location.reference == "feature/foo"


@pytest.mark.parametrize("field", ["url", "reference"])
@pytest.mark.parametrize("value", ["--upload-pack=touch pwned", "-n"])
def test_asset_location_rejects_a_url_or_reference_git_would_read_as_an_option(field: str, value: str):
    with pytest.raises(ValidationError, match="must not start with '-'"):
        AssetModel.model_validate(_located_asset(**{field: value}))


def test_asset_location_schema_offers_the_integration_field():
    location = AssetModel.model_json_schema()["$defs"]["AssetLocationModel"]
    assert "integration" in location["properties"]
    assert location.get("additionalProperties") is False


def test_asset_location_accepts_an_integration():
    model = AssetModel.model_validate(_located_asset(integration="corp-git"))
    assert model.spec.location.integration == "corp-git"


def test_asset_location_integration_is_a_reference_to_an_integration_document():
    rules = {(rule.path, rule.kind) for rule in extract_references(AssetModel)}
    assert ("spec.location.integration", PlatformKind.INTEGRATION) in rules


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:org/repo.git",
        "ssh://git@host/org/repo.git",
        "https://host/org/repo.git",
        "https://user@host/r.git",
    ],
)
def test_asset_location_accepts_urls_without_a_password(url: str):
    assert AssetModel.model_validate(_located_asset(url=url)).spec.location.url == url


def test_asset_location_rejects_a_password_in_the_url_without_echoing_it():
    with pytest.raises(ValidationError, match="must not embed a password") as excinfo:
        AssetModel.model_validate(_located_asset(url="https://bot:s3cr3tpw@host/org/repo.git"))
    # What `strata validate` prints is built from these, which omit `input`.
    assert "s3cr3tpw" not in repr(diagnostics_from_validation_error(excinfo.value).messages())


def test_asset_location_rejects_a_token_pasted_as_the_username():
    token = "ghp_AbCd1234EfGh5678IjKl"
    with pytest.raises(ValidationError, match="must not embed a password or token") as excinfo:
        AssetModel.model_validate(_located_asset(url=f"https://{token}@github.com/org/repo.git"))
    assert token not in repr(diagnostics_from_validation_error(excinfo.value).messages())


def test_asset_location_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AssetModel.model_validate(_located_asset(branch="main"))


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
