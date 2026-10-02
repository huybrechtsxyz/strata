#!/usr/bin/env python3
"""Tests for FirewallModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.firewall_model import FirewallModel


def _minimal_firewall() -> dict:
    return {
        "meta": {"name": "web-firewall"},
        "spec": {
            "allow": [
                {"direction": "in", "proto": "tcp", "port": 443, "from": "0.0.0.0/0"},
            ],
            "default_tags": {"environment": "test"},
        },
    }


def test_firewall_minimal_is_valid():
    """A minimal firewall document (only required fields) validates successfully."""
    model = FirewallModel.model_validate(_minimal_firewall())
    assert model.meta.name == "web-firewall"
    assert model.spec.allow[0].direction.value == "in"
    assert model.spec.allow[0].from_ == "0.0.0.0/0"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "firewall"


def test_firewall_rule_accepts_value_token_for_from():
    """A rule's 'from' field may embed '${var:}'/'${secret:}' tokens instead of a literal."""
    data = _minimal_firewall()
    data["spec"]["allow"][0]["from"] = "${var:partner_cidr}"
    model = FirewallModel.model_validate(data)
    assert model.spec.allow[0].from_ == "${var:partner_cidr}"


def test_firewall_rule_rejects_malformed_value_token():
    """An unknown token kind in 'from'/'to' is rejected at Phase 1."""
    data = _minimal_firewall()
    data["spec"]["allow"][0]["from"] = "${vars:partner_cidr}"
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)


def test_firewall_rule_rejects_invalid_literal_ip():
    """A malformed literal IP/CIDR in 'from' is rejected."""
    data = _minimal_firewall()
    data["spec"]["allow"][0]["from"] = "not-an-ip"
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)


def test_firewall_rule_rejects_port_without_proto():
    """A rule with a port but no proto is rejected."""
    data = _minimal_firewall()
    del data["spec"]["allow"][0]["proto"]
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)


def test_firewall_rule_rejects_icmp_with_port():
    """ICMP rules cannot specify a port."""
    data = _minimal_firewall()
    data["spec"]["allow"][0]["proto"] = "icmp"
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)


def test_firewall_rule_accepts_port_range_string():
    """A port range like '80:90' is accepted."""
    data = _minimal_firewall()
    data["spec"]["allow"][0]["port"] = "80:90"
    model = FirewallModel.model_validate(data)
    assert model.spec.allow[0].port == "80:90"


def test_firewall_rule_name_priority_and_custom_are_optional():
    """A rule without name/priority/custom still validates (non-breaking)."""
    model = FirewallModel.model_validate(_minimal_firewall())
    rule = model.spec.allow[0]
    assert rule.name is None
    assert rule.priority is None
    assert rule.custom is None


def test_firewall_rule_accepts_name_priority_and_custom_when_set():
    data = _minimal_firewall()
    data["spec"]["allow"][0]["name"] = "allow-https"
    data["spec"]["allow"][0]["priority"] = 100
    data["spec"]["allow"][0]["custom"] = {"asg": "web-tier"}
    model = FirewallModel.model_validate(data)
    rule = model.spec.allow[0]
    assert rule.name == "allow-https"
    assert rule.priority == 100
    assert rule.custom == {"asg": "web-tier"}


def test_firewall_rejects_duplicate_rule_names_across_allow_and_deny():
    data = _minimal_firewall()
    data["spec"]["allow"][0]["name"] = "ssh"
    data["spec"]["deny"] = [
        {"direction": "in", "proto": "tcp", "port": 22, "name": "ssh"},
    ]
    with pytest.raises(ValidationError, match="Duplicate firewall rule names"):
        FirewallModel.model_validate(data)


def test_firewall_allows_duplicate_unnamed_rules():
    """Unnamed rules (the common case today) are never checked for uniqueness."""
    data = _minimal_firewall()
    data["spec"]["allow"].append({"direction": "in", "proto": "tcp", "port": 443, "from": "0.0.0.0/0"})
    model = FirewallModel.model_validate(data)
    assert len(model.spec.allow) == 2


def test_firewall_rule_rejects_invalid_port_range():
    """A malformed port range is rejected."""
    data = _minimal_firewall()
    data["spec"]["allow"][0]["port"] = "90:80"
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)


def test_firewall_rule_rejects_invalid_interface_name():
    """An interface name with invalid characters is rejected."""
    data = _minimal_firewall()
    data["spec"]["allow"][0]["interface"] = "eth0!"
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)


def test_firewall_rejects_duplicate_default_directions():
    """Duplicate default rule directions are rejected."""
    data = _minimal_firewall()
    data["spec"]["defaults"] = [
        {"direction": "in", "permission": "deny"},
        {"direction": "in", "permission": "allow"},
    ]
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)


def test_firewall_rejects_conflicting_allow_deny_rules():
    """An identical rule signature in both allow and deny is rejected."""
    data = _minimal_firewall()
    data["spec"]["deny"] = [
        {"direction": "in", "proto": "tcp", "port": 443, "from": "0.0.0.0/0"},
    ]
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)


def test_firewall_rejects_references_field():
    """spec.references is rejected (Requirement was removed as a schema concept — ADR-0002)."""
    data = _minimal_firewall()
    data["spec"]["references"] = {"variables": ["partner_cidr"]}
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)


def test_firewall_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_firewall()
    data["spec"]["allow"][0]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        FirewallModel.model_validate(data)
