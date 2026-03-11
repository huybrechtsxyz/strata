#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_workspace.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Workspace model using Pydantic for data validation and YAML parsing.
===============================================================================
"""


import os
import yaml
import pytest

from xyz_platform.models.workspace_model import WorkspaceModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


WORKSPACE_FOLDER = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "workspaces"
)

# List of YAML files to test (extensible)
WORKSPACE_VALID_FILES = [
    os.path.join(WORKSPACE_FOLDER, "workspace-standard.yaml"),
]

# List of invalid YAML files to test (extensible)
WORKSPACE_INVALID_FILES = [os.path.join(WORKSPACE_FOLDER, "workspace-invalid.yaml")]


@pytest.mark.parametrize("yaml_path", WORKSPACE_VALID_FILES)
def test_workspace_yaml_valid(yaml_path):
    """Test that a workspace YAML file is a valid WorkspaceModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = WorkspaceModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", WORKSPACE_INVALID_FILES)
def test_workspace_yaml_invalid(yaml_path):
    """Test that a workspace YAML file is NOT a valid WorkspaceModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(Exception):
        WorkspaceModel.model_validate(data)
    model = None
    assert model is None
