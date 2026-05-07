#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_firewall.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Tests for firewall models in XYZ Platform.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from xyz_platform.models.firewall_model import FirewallModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


FIREWALL_FOLDER = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "data",
    "firewalls",
)

# List of YAML files to test (extensible)
FIREWALL_VALID_FILES = [
    os.path.join(FIREWALL_FOLDER, "firewall-standard.yaml"),
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "xyz-configuration", "stack", "xyz-fw-base.yaml"
    ),
]

# List of invalid YAML files to test (extensible)
FIREWALL_INVALID_FILES = [os.path.join(FIREWALL_FOLDER, "firewall-invalid.yaml")]


@pytest.mark.parametrize("yaml_path", FIREWALL_VALID_FILES)
def test_firewall_yaml_valid(yaml_path):
    """Test that a firewall YAML file is a valid FirewallModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = FirewallModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", FIREWALL_INVALID_FILES)
def test_firewall_yaml_invalid(yaml_path):
    """Test that a firewall YAML file is NOT a valid FirewallModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)
    model = None
    assert model is None
