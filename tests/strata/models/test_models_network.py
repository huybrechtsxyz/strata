#!/usr/bin/env python3
"""Tests for NetworkModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.network_model import NetworkModel


def _minimal_network() -> dict:
    return {
        "meta": {"name": "core-network"},
        "spec": {
            "networks": [
                {
                    "name": "vpc-main",
                    "address_space": ["10.0.0.0/16"],
                    "subnets": [{"name": "web", "cidr": "10.0.1.0/24"}],
                    "default_tags": {"environment": "test"},
                }
            ]
        },
    }


def test_network_minimal_is_valid():
    """A minimal network document (only required fields) validates successfully."""
    model = NetworkModel.model_validate(_minimal_network())
    assert model.meta.name == "core-network"
    assert model.spec.networks[0].name == "vpc-main"
    assert model.spec.networks[0].subnets[0].cidr == "10.0.1.0/24"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "network"


def test_network_rejects_invalid_literal_cidr():
    """A malformed literal CIDR is rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"][0]["cidr"] = "not-a-cidr"
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_accepts_value_tokens_for_cidr():
    """A subnet cidr may embed '${var:}' tokens instead of a literal."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"][0]["cidr"] = "${var:web_subnet_cidr}"
    model = NetworkModel.model_validate(data)
    assert model.spec.networks[0].subnets[0].cidr == "${var:web_subnet_cidr}"


def test_network_rejects_malformed_value_token():
    """An unknown token kind in a CIDR field is rejected at Phase 1."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"][0]["cidr"] = "${vars:web_subnet_cidr}"
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_overlapping_subnets():
    """Overlapping literal subnet CIDRs within a network are rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"].append({"name": "app", "cidr": "10.0.1.128/25"})
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_skips_overlap_check_when_cidr_is_a_token():
    """Overlap checking is skipped (not falsely triggered) when a CIDR is a Value binding."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"][0]["cidr"] = "${var:web_subnet_cidr}"
    data["spec"]["networks"][0]["subnets"].append({"name": "app", "cidr": "10.0.1.0/24"})
    model = NetworkModel.model_validate(data)
    assert model.spec.networks[0].subnets[1].cidr == "10.0.1.0/24"


def test_network_rejects_subnet_outside_address_space():
    """A subnet CIDR that doesn't fit any address_space entry is rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"][0]["cidr"] = "192.168.1.0/24"
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_self_peering():
    """A peering that targets the network itself is rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["peerings"] = [{"name": "loopback", "target": "vpc-main"}]
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_peering_to_unknown_network():
    """A peering targeting a network not defined in spec.networks is rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["peerings"] = [{"name": "to-other", "target": "vpc-other"}]
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_accepts_valid_peering():
    """A peering targeting another defined network is accepted."""
    data = _minimal_network()
    data["spec"]["networks"][0]["peerings"] = [{"name": "to-other", "target": "vpc-other"}]
    data["spec"]["networks"].append(
        {
            "name": "vpc-other",
            "address_space": ["10.1.0.0/16"],
            "subnets": [{"name": "web", "cidr": "10.1.1.0/24"}],
            "default_tags": {"environment": "test"},
        }
    )
    model = NetworkModel.model_validate(data)
    assert model.spec.networks[0].peerings[0].target == "vpc-other"


def test_network_rejects_overlapping_peered_networks():
    """Peered networks with overlapping literal address spaces are rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["peerings"] = [{"name": "to-other", "target": "vpc-other"}]
    data["spec"]["networks"].append(
        {
            "name": "vpc-other",
            "address_space": ["10.0.0.0/16"],
            "subnets": [{"name": "web", "cidr": "10.0.2.0/24"}],
            "default_tags": {"environment": "test"},
        }
    )
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_duplicate_network_names():
    """Duplicate network names within spec.networks are rejected."""
    data = _minimal_network()
    data["spec"]["networks"].append(dict(data["spec"]["networks"][0]))
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_duplicate_subnet_names():
    """Duplicate subnet names within a network are rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"].append({"name": "web", "cidr": "10.0.5.0/24"})
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_references_field():
    """spec.references is rejected (Requirement was removed as a schema concept — ADR-0002)."""
    data = _minimal_network()
    data["spec"]["references"] = {"variables": ["web_subnet_cidr"]}
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_network()
    data["spec"]["networks"][0]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)
