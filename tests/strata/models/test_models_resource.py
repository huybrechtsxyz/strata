#!/usr/bin/env python3
"""Tests for ResourceModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.resource_model import ResourceModel


def _minimal_resource() -> dict:
    return {
        "meta": {"name": "web-vm"},
        "spec": {
            "properties": {
                "provider_type": "kamatera",
                "resource_type": "virtual_machine",
            }
        },
    }


def test_resource_minimal_is_valid():
    """A minimal resource (only required fields) validates successfully."""
    model = ResourceModel.model_validate(_minimal_resource())
    assert model.meta.name == "web-vm"
    assert model.spec.properties.provider_type == "kamatera"
    assert model.spec.properties.resource_type == "virtual_machine"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "resource"


def test_resource_with_storage_is_valid():
    """A resource with disks and volumes under a valid mount validates successfully."""
    data = _minimal_resource()
    data["spec"]["storage"] = {
        "disks": [{"size": 30, "label": "os-disk", "mount": "/data"}],
        "volumes": [{"name": "logs", "path": "/data/logs"}],
    }
    model = ResourceModel.model_validate(data)
    assert model.spec.storage.disks[0].label == "os-disk"
    assert model.spec.storage.volumes[0].path == "/data/logs"


def test_resource_volume_not_under_disk_mount_is_invalid():
    """A volume path outside any disk mount point raises a ValidationError."""
    data = _minimal_resource()
    data["spec"]["storage"] = {
        "disks": [{"size": 30, "label": "os-disk", "mount": "/data"}],
        "volumes": [{"name": "logs", "path": "/other/logs"}],
    }
    with pytest.raises(ValidationError, match="not under any disk mount point"):
        ResourceModel.model_validate(data)


def test_resource_disk_mount_must_be_absolute():
    """A relative disk mount path raises a ValidationError."""
    data = _minimal_resource()
    data["spec"]["storage"] = {"disks": [{"size": 30, "label": "os-disk", "mount": "data"}]}
    with pytest.raises(ValidationError, match="must be absolute"):
        ResourceModel.model_validate(data)


def test_resource_disk_mount_rejects_system_directory():
    """A disk mount pointing at a reserved system directory raises a ValidationError."""
    data = _minimal_resource()
    data["spec"]["storage"] = {"disks": [{"size": 30, "label": "os-disk", "mount": "/etc"}]}
    with pytest.raises(ValidationError, match="system directory"):
        ResourceModel.model_validate(data)


def test_resource_dependency_requires_category():
    """A dependency without a category raises a ValidationError."""
    data = _minimal_resource()
    data["spec"]["dependencies"] = [{"category": ""}]
    with pytest.raises(ValidationError):
        ResourceModel.model_validate(data)


def test_resource_dependency_is_valid():
    """A dependency with category/subcategory validates successfully."""
    data = _minimal_resource()
    data["spec"]["dependencies"] = [{"category": "networking", "subcategory": "virtual_network", "optional": True}]
    model = ResourceModel.model_validate(data)
    assert model.spec.dependencies[0].category == "networking"
    assert model.spec.dependencies[0].optional is True


def test_resource_missing_required_field_is_invalid():
    """Missing spec.properties.resource_type raises a ValidationError."""
    data = _minimal_resource()
    del data["spec"]["properties"]["resource_type"]
    with pytest.raises(ValidationError):
        ResourceModel.model_validate(data)


def test_resource_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_resource()
    data["spec"]["properties"]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        ResourceModel.model_validate(data)
