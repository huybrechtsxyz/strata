#!/usr/bin/env python3
"""Tests for AuthenticationModel method/config consistency validation."""

import pytest
from pydantic import ValidationError

from strata.models.auth_models import AuthenticationModel


def test_auth_method_matches_populated_config_is_valid():
    """method='cli' with cli config set validates successfully."""
    model = AuthenticationModel.model_validate({"method": "cli", "cli": {"use_cli": True}})
    assert model.method == "cli"
    assert model.cli is not None


def test_auth_oauth2_is_valid():
    """method='oauth2' with matching oauth2 config validates successfully."""
    model = AuthenticationModel.model_validate(
        {
            "method": "oauth2",
            "oauth2": {"client_id": "app_client_id", "client_secret": "app_client_secret"},
        }
    )
    assert model.method == "oauth2"
    assert model.oauth2.client_id == "app_client_id"


def test_auth_missing_matching_config_is_invalid():
    """method='oauth2' without oauth2 config set raises a ValidationError."""
    with pytest.raises(ValidationError, match="oauth2.*configuration is not set"):
        AuthenticationModel.model_validate({"method": "oauth2"})


def test_auth_mismatched_config_is_invalid():
    """method='cli' with aws config set (instead of cli) raises a ValidationError."""
    with pytest.raises(ValidationError, match="configuration is not set"):
        AuthenticationModel.model_validate(
            {
                "method": "cli",
                "aws": {"access_key_id": "key", "secret_access_key": "secret"},
            }
        )


def test_auth_unrelated_config_also_set_is_invalid():
    """method='cli' with both cli and aws config set raises a ValidationError."""
    with pytest.raises(ValidationError, match="unrelated configuration is also set"):
        AuthenticationModel.model_validate(
            {
                "method": "cli",
                "cli": {"use_cli": True},
                "aws": {"access_key_id": "key", "secret_access_key": "secret"},
            }
        )


# ---------------------------------------------------------------------------
# ssh_key/token (docs/design/gitops-integration.md Phase 4) — the two real
# git-native credential shapes, added to the same closed `method` vocabulary
# rather than a separate git-only auth model.
# ---------------------------------------------------------------------------


def test_auth_ssh_key_is_valid():
    model = AuthenticationModel.model_validate(
        {"method": "ssh_key", "ssh_key": {"private_key": "github-deploy-key-private"}}
    )
    assert model.method == "ssh_key"
    assert model.ssh_key.private_key == "github-deploy-key-private"
    assert model.ssh_key.passphrase is None
    assert model.ssh_key.known_hosts is None


def test_auth_ssh_key_accepts_passphrase_and_known_hosts():
    model = AuthenticationModel.model_validate(
        {
            "method": "ssh_key",
            "ssh_key": {
                "private_key": "github-deploy-key-private",
                "passphrase": "github-deploy-key-passphrase",
                "known_hosts": "github-known-hosts",
            },
        }
    )
    assert model.ssh_key.passphrase == "github-deploy-key-passphrase"
    assert model.ssh_key.known_hosts == "github-known-hosts"


def test_auth_token_is_valid():
    model = AuthenticationModel.model_validate({"method": "token", "token": {"token": "github-pat"}})
    assert model.method == "token"
    assert model.token.token == "github-pat"
    assert model.token.username is None


def test_auth_token_accepts_username():
    model = AuthenticationModel.model_validate(
        {"method": "token", "token": {"token": "github-pat", "username": "x-access-token"}}
    )
    assert model.token.username == "x-access-token"


def test_auth_ssh_key_missing_matching_config_is_invalid():
    with pytest.raises(ValidationError, match="ssh_key.*configuration is not set"):
        AuthenticationModel.model_validate({"method": "ssh_key"})


def test_auth_token_mismatched_config_is_invalid():
    """method='token' with ssh_key config set (instead of token) raises a ValidationError."""
    with pytest.raises(ValidationError, match="configuration is not set"):
        AuthenticationModel.model_validate({"method": "token", "ssh_key": {"private_key": "github-deploy-key-private"}})
