#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_network.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : Tests for Network models in strata.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from strata.models.network_model import NetworkModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


NETWORK_FOLDER = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "data",
    "network",
)


def _net_data(networks: list, references: dict | None = None) -> dict:
    """Build a minimal NetworkModel payload with given networks and optional references."""
    spec: dict = {"networks": networks}
    if references is not None:
        spec["references"] = references
    return {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "network",
        "meta": {"name": "net_test"},
        "spec": spec,
    }


def _simple_network(
    name: str = "test_net",
    address_space: list | None = None,
    subnets: list | None = None,
    peerings: list | None = None,
) -> dict:
    """Build a single network definition dict."""
    net: dict = {
        "name": name,
        "address_space": address_space or [{"value": "10.0.0.0/16"}],
        "subnets": subnets or [{"name": "default", "cidr": {"value": "10.0.0.0/24"}}],
    }
    if peerings is not None:
        net["peerings"] = peerings
    return net


class TestNetworkModel:
    # -----------------------------------------------------------------
    # Valid fixture loads
    # -----------------------------------------------------------------

    def test_valid_haven_model(self):
        """Load network-haven.yaml — simple flat network with 3 subnets."""
        path = os.path.join(NETWORK_FOLDER, "network-haven.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        model = NetworkModel.model_validate(data)
        assert model is not None
        assert len(model.spec.networks) == 1
        assert len(model.spec.networks[0].subnets) == 3

    def test_valid_enterprise_model(self):
        """Load network-enterprise.yaml — hub + 2 spokes with peerings and var refs."""
        path = os.path.join(NETWORK_FOLDER, "network-enterprise.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        model = NetworkModel.model_validate(data)
        assert model is not None
        assert len(model.spec.networks) == 3

    def test_valid_var_refs_model(self):
        """Load network-var-refs.yaml — CIDRs with var and secret sources."""
        path = os.path.join(NETWORK_FOLDER, "network-var-refs.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        model = NetworkModel.model_validate(data)
        assert model is not None
        net = model.spec.networks[0]
        # One subnet uses value, one uses var, one uses secret
        assert net.subnets[0].cidr.value == "10.0.0.0/24"
        assert net.subnets[1].cidr.var == "mgmt_cidr"
        assert net.subnets[2].cidr.secret == "secure_subnet_cidr"

    # -----------------------------------------------------------------
    # Invalid fixture loads
    # -----------------------------------------------------------------

    def test_invalid_wrong_kind(self):
        """network-invalid.yaml has kind=namespace — raises ValidationError."""
        path = os.path.join(NETWORK_FOLDER, "network-invalid.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    def test_empty_networks_list_rejected(self):
        """spec.networks=[] violates min_length=1."""
        data = _net_data(networks=[])
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    # -----------------------------------------------------------------
    # CidrSourceModel union tests (V1, V2)
    # -----------------------------------------------------------------

    def test_cidr_source_value_literal_valid(self):
        """Literal CIDR value is accepted."""
        data = _net_data([_simple_network()])
        model = NetworkModel.model_validate(data)
        assert model.spec.networks[0].address_space[0].value == "10.0.0.0/16"

    def test_cidr_source_var_valid(self):
        """var key with matching references is accepted."""
        net = _simple_network(address_space=[{"var": "my_cidr"}])
        data = _net_data([net], references={"variables": ["my_cidr"]})
        model = NetworkModel.model_validate(data)
        assert model.spec.networks[0].address_space[0].var == "my_cidr"

    def test_cidr_source_secret_valid(self):
        """secret key with matching references is accepted."""
        net = _simple_network(address_space=[{"secret": "secure_cidr"}])
        data = _net_data([net], references={"secrets": ["secure_cidr"]})
        model = NetworkModel.model_validate(data)
        assert model.spec.networks[0].address_space[0].secret == "secure_cidr"

    def test_cidr_source_no_source_invalid(self):
        """CidrSourceModel with none of value/var/secret set raises ValidationError (V1)."""
        net = _simple_network(address_space=[{}])
        data = _net_data([net])
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    def test_cidr_source_two_sources_invalid(self):
        """CidrSourceModel with both value and var set raises ValidationError (V1)."""
        net = _simple_network(address_space=[{"value": "10.0.0.0/16", "var": "my_cidr"}])
        data = _net_data([net], references={"variables": ["my_cidr"]})
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    def test_cidr_source_invalid_cidr_format(self):
        """CidrSourceModel value='not-a-cidr' raises ValidationError (V2)."""
        net = _simple_network(address_space=[{"value": "not-a-cidr"}])
        data = _net_data([net])
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    # -----------------------------------------------------------------
    # Unique names (V3, V4)
    # -----------------------------------------------------------------

    def test_unique_network_names(self):
        """Duplicate network names in spec raises ValidationError (V3)."""
        net1 = _simple_network(name="same_name")
        net2 = _simple_network(name="same_name")
        data = _net_data([net1, net2])
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    def test_unique_subnet_names_within_network(self):
        """Duplicate subnet names within a network raises ValidationError (V4)."""
        net = _simple_network(
            subnets=[
                {"name": "dup_sub", "cidr": {"value": "10.0.0.0/24"}},
                {"name": "dup_sub", "cidr": {"value": "10.0.1.0/24"}},
            ]
        )
        data = _net_data([net])
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    # -----------------------------------------------------------------
    # CIDR overlap detection (V9, V10)
    # -----------------------------------------------------------------

    def test_subnet_cidr_overlap_within_network(self):
        """Two subnets with overlapping CIDRs within same network raises ValidationError (V9)."""
        path = os.path.join(NETWORK_FOLDER, "network-overlapping-subnets.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    def test_subnet_must_fit_within_address_space(self):
        """Subnet CIDR outside address space raises ValidationError (V10)."""
        net = _simple_network(
            address_space=[{"value": "10.0.0.0/24"}],
            subnets=[{"name": "outside", "cidr": {"value": "192.168.1.0/24"}}],
        )
        data = _net_data([net])
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    # -----------------------------------------------------------------
    # Peering validations (V5, V6, V7)
    # -----------------------------------------------------------------

    def test_peering_target_must_exist(self):
        """Peering target referencing a non-existent network raises ValidationError (V5)."""
        net = _simple_network(
            name="net_a",
            peerings=[{"name": "to_ghost", "target": "non_existent_net"}],
        )
        data = _net_data([net])
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    def test_no_self_peering(self):
        """Peering target = own network name raises ValidationError (V6)."""
        net = _simple_network(
            name="net_a",
            peerings=[{"name": "self_peer", "target": "net_a"}],
        )
        data = _net_data([net])
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    def test_unique_peering_names(self):
        """Duplicate peering names within a network raises ValidationError (V7)."""
        net_a = _simple_network(
            name="net_a",
            peerings=[
                {"name": "dup_peer", "target": "net_b"},
                {"name": "dup_peer", "target": "net_b"},
            ],
        )
        net_b = _simple_network(name="net_b")
        data = _net_data([net_a, net_b])
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    # -----------------------------------------------------------------
    # References declared (V8)
    # -----------------------------------------------------------------

    def test_undeclared_var_in_references(self):
        """var key used in CIDR but not in references.variables raises ValidationError (V8)."""
        net = _simple_network(address_space=[{"var": "undeclared_key"}])
        data = _net_data([net], references={"variables": ["other_key"]})
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    def test_undeclared_secret_in_references(self):
        """secret key used in CIDR but not in references.secrets raises ValidationError (V8)."""
        net = _simple_network(subnets=[{"name": "secure", "cidr": {"secret": "undeclared_secret"}}])
        data = _net_data([net], references={"secrets": ["other_secret"]})
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    # -----------------------------------------------------------------
    # Peered overlap (V11)
    # -----------------------------------------------------------------

    def test_peered_networks_overlapping_address_space(self):
        """Two peered networks with overlapping address spaces raises ValidationError (V11)."""
        path = os.path.join(NETWORK_FOLDER, "network-peered-overlap.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)

    # -----------------------------------------------------------------
    # Kind frozen
    # -----------------------------------------------------------------

    def test_kind_frozen_to_network(self):
        """kind='firewall' in a network payload raises ValidationError."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "firewall",
            "meta": {"name": "net_test"},
            "spec": {
                "networks": [
                    {
                        "name": "test_net",
                        "address_space": [{"value": "10.0.0.0/16"}],
                        "subnets": [{"name": "default", "cidr": {"value": "10.0.0.0/24"}}],
                    }
                ]
            },
        }
        with pytest.raises(ValidationError):
            NetworkModel.model_validate(data)
