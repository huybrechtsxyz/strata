#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_namespaces.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Tests for NamespaceModel YAML validation.
===============================================================================
"""


import os
import yaml
import pytest

from xyz_platform.models.namespace_model import NamespaceModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


NAMESPACES_FOLDER = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "namespaces"
)

# List of YAML files to test (extensible)
NAMESPACES_VALID_FILES = [os.path.join(NAMESPACES_FOLDER, "namespace-standard.yaml")]

# List of invalid YAML files to test (extensible)
NAMESPACES_INVALID_FILES = [os.path.join(NAMESPACES_FOLDER, "namespace-invalid.yaml")]


@pytest.mark.parametrize("yaml_path", NAMESPACES_VALID_FILES)
def test_namespaces_yaml_valid(yaml_path):
    """Test that a namespaces YAML file is a valid NamespaceModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = NamespaceModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", NAMESPACES_INVALID_FILES)
def test_namespaces_yaml_invalid(yaml_path):
    """Test that a namespaces YAML file is NOT a valid NamespaceModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(Exception):
        NamespaceModel.model_validate(data)
    model = None
    assert model is None
