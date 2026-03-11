#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_unknown.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Tests for unknown models in XYZ Platform.
===============================================================================
"""

import os
import yaml
import pytest

from src.xyz_platform.models.unknown_model import UnknownModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


UNKNOWN_FOLDER = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "data",
    "unknown",
)

# List of YAML files to test (extensible)
UNKNOWN_VALID_FILES = [os.path.join(UNKNOWN_FOLDER, "unknown-standard.yaml")]

# List of invalid YAML files to test (extensible)
UNKNOWN_INVALID_FILES = [os.path.join(UNKNOWN_FOLDER, "unknown-invalid.yaml")]


@pytest.mark.parametrize("yaml_path", UNKNOWN_VALID_FILES)
def test_unknown_yaml_valid(yaml_path):
    """Test that a unknown YAML file is a valid unknownModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = UnknownModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", UNKNOWN_INVALID_FILES)
def test_unknown_yaml_invalid(yaml_path):
    """Test that a unknown YAML file is NOT a valid unknownModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(Exception):
        UnknownModel.model_validate(data)
    model = None
    assert model is None
