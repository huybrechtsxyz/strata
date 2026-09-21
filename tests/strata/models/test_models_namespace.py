#!/usr/bin/env python3
"""Tests for NamespaceModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.namespace_model import NamespaceModel


def _minimal_namespace() -> dict:
    return {
        "meta": {"name": "myapp"},
        "spec": {
            "modules": [{"name": "template_module", "file": "config/myapp/modules/template-module.yaml"}],
            "default_labels": {"environment": "test"},
        },
    }


def test_namespace_minimal_is_valid():
    """A minimal namespace document (only required fields) validates successfully."""
    model = NamespaceModel.model_validate(_minimal_namespace())
    assert model.meta.name == "myapp"
    assert model.spec.modules[0].name == "template_module"
    assert model.spec.modules[0].slot_type == "main"
    assert model.spec.modules[0].enabled is True
    assert model.spec.type.value == "dedicated"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "namespace"


def test_namespace_module_accepts_slot_type_enabled_and_configuration():
    """Namespace modules accept the shared ModuleReferenceModel's enriched fields."""
    data = _minimal_namespace()
    data["spec"]["modules"][0]["slot_type"] = "canary"
    data["spec"]["modules"][0]["enabled"] = False
    data["spec"]["modules"][0]["configuration"] = {"replicas": 2}
    model = NamespaceModel.model_validate(data)
    assert model.spec.modules[0].slot_type == "canary"
    assert model.spec.modules[0].enabled is False
    assert model.spec.modules[0].configuration == {"replicas": 2}


def test_namespace_accepts_shared_type():
    """type: shared is accepted."""
    data = _minimal_namespace()
    data["spec"]["type"] = "shared"
    model = NamespaceModel.model_validate(data)
    assert model.spec.type.value == "shared"


def test_namespace_accepts_lifecycle_only():
    """A namespace with only lifecycle (no modules) is accepted, with a warning."""
    data = {
        "meta": {"name": "infra-hooks"},
        "spec": {
            "lifecycle": {"deploy_check": {"scripts": ["scripts/check.sh"]}},
            "default_labels": {"environment": "test"},
        },
    }
    with pytest.warns(UserWarning):
        model = NamespaceModel.model_validate(data)
    assert model.spec.modules is None


def test_namespace_rejects_empty_spec():
    """A namespace with neither lifecycle nor modules is rejected."""
    data = {"meta": {"name": "empty"}, "spec": {}}
    with pytest.raises(ValidationError):
        NamespaceModel.model_validate(data)


def test_namespace_rejects_duplicate_module_names():
    """Duplicate module names within a namespace are rejected."""
    data = _minimal_namespace()
    data["spec"]["modules"].append(dict(data["spec"]["modules"][0]))
    with pytest.raises(ValidationError):
        NamespaceModel.model_validate(data)


def test_namespace_module_rejects_path_traversal():
    """A module file reference escaping via '..' is rejected."""
    data = _minimal_namespace()
    data["spec"]["modules"][0]["file"] = "../../etc/passwd"
    with pytest.raises(ValidationError):
        NamespaceModel.model_validate(data)


def test_namespace_module_rejects_absolute_path():
    """A module file reference that is absolute is rejected."""
    data = _minimal_namespace()
    data["spec"]["modules"][0]["file"] = "/etc/passwd"
    with pytest.raises(ValidationError):
        NamespaceModel.model_validate(data)


def test_namespace_module_rejects_path_traversal_in_cross_repo_reference():
    """A '@repo/' module file reference escaping the repo root via '..' is rejected."""
    data = _minimal_namespace()
    data["spec"]["modules"][0]["file"] = "@infra/../../../etc/passwd"
    with pytest.raises(ValidationError):
        NamespaceModel.model_validate(data)


def test_namespace_module_accepts_cross_repo_reference():
    """A well-formed '@repo/' module file reference is accepted."""
    data = _minimal_namespace()
    data["spec"]["modules"][0]["file"] = "@infra/modules/template-module.yaml"
    model = NamespaceModel.model_validate(data)
    assert model.spec.modules[0].file == "@infra/modules/template-module.yaml"


def test_namespace_rejects_references_field():
    """spec.references is rejected (Requirement was removed as a schema concept — ADR-0002)."""
    data = _minimal_namespace()
    data["spec"]["references"] = {"variables": ["some_key"]}
    with pytest.raises(ValidationError):
        NamespaceModel.model_validate(data)


def test_namespace_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_namespace()
    data["spec"]["modules"][0]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        NamespaceModel.model_validate(data)
