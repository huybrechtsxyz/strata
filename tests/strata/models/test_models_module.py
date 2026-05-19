#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_module.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Module model using Pydantic for data validation and YAML parsing.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from strata.models.module_model import ModuleModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


MODULE_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "modules")

# List of YAML files to test (extensible)
MODULE_VALID_FILES = [
    os.path.join(MODULE_FOLDER, "module-standard.yaml"),
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "xyz-configuration", "stack", "xyz-md-traefik.yaml"
    ),
]

# List of invalid YAML files to test (extensible)
MODULE_INVALID_FILES = [os.path.join(MODULE_FOLDER, "module-invalid.yaml")]


@pytest.mark.parametrize("yaml_path", MODULE_VALID_FILES)
def test_module_yaml_valid(yaml_path):
    """Test that a module YAML file is a valid ModuleModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = ModuleModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", MODULE_INVALID_FILES)
def test_module_yaml_invalid(yaml_path):
    """Test that a module YAML file is NOT a valid ModuleModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)
    model = None
    assert model is None
