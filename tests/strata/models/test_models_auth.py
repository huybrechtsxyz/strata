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
