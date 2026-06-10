#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_network.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : NetworkService tests for strata CLI.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.models.network_model import NetworkModel
from strata.services.network_service import NetworkService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


def _make_network_model(name: str, networks: list, references: dict | None = None) -> NetworkModel:
    """Construct a minimal NetworkModel programmatically."""
    spec: dict = {"networks": networks}
    if references is not None:
        spec["references"] = references
    return NetworkModel.model_validate(
        {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "network",
            "meta": {"name": name},
            "spec": spec,
        }
    )


class TestNetworkService:
    @pytest.fixture
    def get_network_service(self):
        return NetworkService(_data("network/network-haven.yaml"))

    def test_get_model_class(self, get_network_service):
        service = get_network_service
        model_class = service._get_model_class()
        assert model_class == NetworkModel

    def test_validate_standard(self, get_network_service):
        service = get_network_service
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.is_validated()

    def test_get_kind_after_validate(self, get_network_service):
        service = get_network_service
        service.validate()
        assert service.get_kind() == "network"

    def test_merge_networks_by_name(self):
        """Merging two NetworkModels with overlapping network names: last-wins by name."""
        first = _make_network_model(
            "net_first",
            [
                {
                    "name": "vnet_a",
                    "address_space": [{"value": "10.0.0.0/16"}],
                    "subnets": [{"name": "sub1", "cidr": {"value": "10.0.0.0/24"}}],
                },
                {
                    "name": "vnet_b",
                    "address_space": [{"value": "10.1.0.0/16"}],
                    "subnets": [{"name": "sub1", "cidr": {"value": "10.1.0.0/24"}}],
                },
            ],
        )
        second = _make_network_model(
            "net_second",
            [
                {
                    "name": "vnet_a",
                    "address_space": [{"value": "172.16.0.0/16"}],
                    "subnets": [{"name": "sub1", "cidr": {"value": "172.16.0.0/24"}}],
                },
            ],
        )
        merged = NetworkService.merge_networks([first, second])

        net_names = [n.name for n in merged.spec.networks]
        assert "vnet_a" in net_names
        assert "vnet_b" in net_names

        vnet_a = next(n for n in merged.spec.networks if n.name == "vnet_a")
        # last-wins: second model's address_space
        assert vnet_a.address_space[0].value == "172.16.0.0/16"

    def test_merge_subnets_by_network_subnet_tuple(self):
        """Subnets with same (network_name, subnet_name) key: last-wins semantics."""
        first = _make_network_model(
            "net_first",
            [
                {
                    "name": "vnet_a",
                    "address_space": [{"value": "10.0.0.0/16"}],
                    "subnets": [
                        {"name": "frontend", "cidr": {"value": "10.0.0.0/24"}},
                        {"name": "backend", "cidr": {"value": "10.0.1.0/24"}},
                    ],
                }
            ],
        )
        second = _make_network_model(
            "net_second",
            [
                {
                    "name": "vnet_a",
                    "address_space": [{"value": "10.0.0.0/16"}],
                    "subnets": [
                        {"name": "frontend", "cidr": {"value": "10.0.10.0/24"}},
                    ],
                }
            ],
        )
        merged = NetworkService.merge_networks([first, second])

        vnet_a = next(n for n in merged.spec.networks if n.name == "vnet_a")
        subnet_names = [s.name for s in vnet_a.subnets]
        assert "frontend" in subnet_names
        assert "backend" in subnet_names

        frontend = next(s for s in vnet_a.subnets if s.name == "frontend")
        # last-wins: second model's CIDR for frontend
        assert frontend.cidr.value == "10.0.10.0/24"

        # backend preserved from first
        backend = next(s for s in vnet_a.subnets if s.name == "backend")
        assert backend.cidr.value == "10.0.1.0/24"
