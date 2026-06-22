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

import pytest
import yaml
from pydantic import ValidationError

from strata.models.common_models import SourceModel
from strata.models.workspace_model import WorkspaceModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


WORKSPACE_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "workspaces")

# List of YAML files to test (extensible)
WORKSPACE_VALID_FILES = [
    os.path.join(WORKSPACE_FOLDER, "workspace-standard.yaml"),
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "xyz-configuration", "stack", "xyz-ws-platform.yaml"
    ),
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
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)
    model = None
    assert model is None


class TestSourceModelSingleRepo:
    def test_source_path_only_is_valid(self):
        """Phase 1: source_path without repository must pass SourceModel validation."""
        model = SourceModel(source_path="terraform")
        assert model.source_path == "terraform"
        assert model.repository is None

    def test_repository_plus_source_path_is_valid(self):
        """Phase 1: explicit repository + source_path must still pass."""
        model = SourceModel(repository="my_repo", source_path="terraform")
        assert str(model.repository) == "my_repo"
        assert model.source_path == "terraform"

    def test_neither_git_nor_chart_raises(self):
        """Phase 1: completely empty SourceModel must fail validation."""
        with pytest.raises(ValidationError):
            SourceModel()

    def test_repository_without_source_path_raises(self):
        """Phase 1: repository alone (no source_path) must fail — source_path is required."""
        with pytest.raises(ValidationError):
            SourceModel(repository="my_repo")
