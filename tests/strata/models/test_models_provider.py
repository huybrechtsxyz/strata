#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_provider.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Security tests for ProviderModel YAML validation.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from strata.models.provider_model import ProviderModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


PROVIDER_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "providers")

# List of YAML files to test (extensible)
PROVIDER_VALID_FILES = [
    os.path.join(PROVIDER_FOLDER, "provider-standard.yaml"),
]

# List of invalid YAML files to test (extensible)
PROVIDER_INVALID_FILES = [os.path.join(PROVIDER_FOLDER, "provider-invalid.yaml")]


@pytest.mark.parametrize("yaml_path", PROVIDER_VALID_FILES)
def test_provider_yaml_valid(yaml_path):
    """Test that a provider YAML file is a valid ProviderModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = ProviderModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", PROVIDER_INVALID_FILES)
def test_provider_yaml_invalid(yaml_path):
    """Test that a provider YAML file is NOT a valid ProviderModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        ProviderModel.model_validate(data)
    model = None
    assert model is None
