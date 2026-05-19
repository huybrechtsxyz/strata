#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_resource.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.9+
Description   : Resource model using Pydantic for data validation and YAML parsing.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from strata.models.resource_model import ResourceModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


RESOURCE_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "resources")

# List of YAML files to test (extensible)
RESOURCE_VALID_FILES = [
    os.path.join(RESOURCE_FOLDER, "resource-standard.yaml"),
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "xyz-configuration", "stack", "xyz-rx-vm-infra.yaml"
    ),
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "xyz-configuration", "stack", "xyz-rx-vm-manager.yaml"
    ),
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "xyz-configuration", "stack", "xyz-rx-vm-worker.yaml"
    ),
]

# List of invalid YAML files to test (extensible)
RESOURCE_INVALID_FILES = [
    os.path.join(RESOURCE_FOLDER, "resource-invalid.yaml"),
]


@pytest.mark.parametrize("yaml_path", RESOURCE_VALID_FILES)
def test_resource_yaml_valid(yaml_path):
    """Test that a resource YAML file is a valid ResourceModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = ResourceModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", RESOURCE_INVALID_FILES)
def test_resource_yaml_invalid(yaml_path):
    """Test that a resource YAML file is NOT a valid ResourceModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        model = ResourceModel.model_validate(data)
