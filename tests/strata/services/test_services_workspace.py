#!/usr/bin/env python3
"""Tests for WorkspaceService loading and validation."""

from strata.services.workspace_service import WorkspaceService


def test_workspace_service_validates_from_data():
    """A WorkspaceService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "myapp-workspace"},
        "spec": {
            "providers": [{"name": "azure-main", "file": "providers/azure.yaml"}],
            "provisioners": [
                {
                    "name": "terraform-main",
                    "tool": "terraform",
                    "source": {"repository": "infra-repo", "source_path": "terraform/main"},
                }
            ],
        },
    }
    service = WorkspaceService(data=data)
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.model is not None
    assert service.model.spec.providers[0].name == "azure-main"
