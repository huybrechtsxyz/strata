#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_workspace.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : WorkspaceService test fixtures and utilities for strata CLI tests.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.models.workspace_model import WorkspaceModel
from strata.services.workspace_service import WorkspaceService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestWorkspaceService:
    @pytest.fixture
    def get_workspace_service(self):
        return WorkspaceService(_data("workspaces/workspace-standard.yaml"))

    def test_get_model_class(self, get_workspace_service):
        service = get_workspace_service
        model_class = service._get_model_class()
        assert model_class == WorkspaceModel

    def test_validate_standard(self, get_workspace_service):
        service = get_workspace_service
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.is_validated()

    def test_get_kind_after_validate(self, get_workspace_service):
        service = get_workspace_service
        service.validate()
        assert service.get_kind() == "workspace"
