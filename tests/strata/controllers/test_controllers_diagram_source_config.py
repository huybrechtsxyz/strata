#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_controllers_diagram_source_config.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : NETWORK/FIREWALLS/DNS diagram source resolver tests (ADR-0034 Task B).
===============================================================================
"""

import pytest

from strata.controllers.diagram_source_controller import DiagramSourceController
from strata.models.diagram_model import DiagramSourceModel


def _source(type_: str, filter_: dict | None = None) -> DiagramSourceModel:
    payload: dict = {"type": type_}
    if filter_ is not None:
        payload["filter"] = filter_
    return DiagramSourceModel.model_validate(payload)


NETWORK_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: network
meta:
  name: net_platform
spec:
  references:
    variables:
      - vnet_b_cidr
  networks:
    - name: vnet_a
      address_space:
        - value: "10.0.0.0/16"
      subnets:
        - name: sub1
          cidr:
            value: "10.0.0.0/24"
      peerings:
        - name: peer_to_b
          target: vnet_b
    - name: vnet_b
      address_space:
        - var: vnet_b_cidr
      subnets:
        - name: sub2
          cidr:
            value: "10.1.0.0/24"
"""

FIREWALL_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: firewall
meta:
  name: fw_base
  labels: {}
spec:
  reset: true
  defaults:
    - direction: in
      permission: deny
  allow:
    - direction: in
      proto: tcp
      port: 443
      from: "0.0.0.0/0"
  deny:
    - direction: out
      proto: tcp
      port: 25
"""

DNS_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: dns
meta:
  name: dns_primary
spec:
  provider: cloudflare
  zones:
    - name: example.com
      ttl: 300
      records:
        - name: "@"
          type: A
          value: "1.2.3.4"
        - name: www
          type: CNAME
          value: "example.com."
    - name: example.org
      records: []
"""


@pytest.fixture
def config_workspace(tmp_path):
    """A workspace referencing one network, one firewall, and one DNS document."""
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v1\n"
        "kind: workspace\n"
        "meta:\n"
        "  name: sample_ws\n"
        "spec:\n"
        "  networks:\n"
        "    - name: platform_net\n"
        "      file: net.yaml\n"
        "  firewalls:\n"
        "    - name: base_fw\n"
        "      file: fw.yaml\n"
        "  dns_zones:\n"
        "    - name: primary_zone\n"
        "      file: dns.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "net.yaml").write_text(NETWORK_YAML, encoding="utf-8")
    (tmp_path / "fw.yaml").write_text(FIREWALL_YAML, encoding="utf-8")
    (tmp_path / "dns.yaml").write_text(DNS_YAML, encoding="utf-8")
    return tmp_path


class TestNetworkSource:
    @pytest.fixture
    def network(self, config_workspace):
        controller = DiagramSourceController(config_workspace, entry="workspace.yaml", no_validate=True)
        return controller.resolve([_source("network")])["network"]

    def test_one_node_per_network_definition(self, network):
        """A single reference can point at a file declaring several networks."""
        assert {n["id"] for n in network["nodes"]} == {"vnet_a", "vnet_b"}

    def test_uri_is_document_plus_nested_network_name(self, network):
        node = next(n for n in network["nodes"] if n["id"] == "vnet_a")
        assert node["uri"] == "strata://network/net_platform/network/vnet_a"

    def test_location_points_at_the_referenced_file(self, network):
        node = next(n for n in network["nodes"] if n["id"] == "vnet_a")
        assert node["location"] == {"file": "net.yaml"}

    def test_literal_cidr_is_shown_directly(self, network):
        node = next(n for n in network["nodes"] if n["id"] == "vnet_a")
        assert node["metadata"]["address_space"] == ["10.0.0.0/16"]

    def test_variable_cidr_shows_its_provenance_not_a_resolved_value(self, network):
        """A CIDR sourced from a variable cannot be resolved here — show where it comes from."""
        node = next(n for n in network["nodes"] if n["id"] == "vnet_b")
        assert node["metadata"]["address_space"] == ["var:vnet_b_cidr"]

    def test_subnet_and_peering_counts(self, network):
        node = next(n for n in network["nodes"] if n["id"] == "vnet_a")
        assert node["metadata"]["subnet_count"] == 1
        assert node["metadata"]["peering_count"] == 1

    def test_reference_name_is_recorded(self, network):
        node = next(n for n in network["nodes"] if n["id"] == "vnet_a")
        assert node["metadata"]["reference"] == "platform_net"

    def test_peering_becomes_an_edge(self, network):
        assert {"source": "vnet_a", "target": "vnet_b", "label": "peering"} in network["edges"]

    def test_network_without_peerings_has_no_edges_from_it(self, network):
        assert not any(e["source"] == "vnet_b" for e in network["edges"])


class TestFirewallSource:
    @pytest.fixture
    def firewalls(self, config_workspace):
        controller = DiagramSourceController(config_workspace, entry="workspace.yaml", no_validate=True)
        return controller.resolve([_source("firewalls")])["firewalls"]

    def test_node_id_is_the_workspace_reference_name(self, firewalls):
        """Individual rules have no name of their own — the ruleset reference is the node."""
        assert {n["id"] for n in firewalls["nodes"]} == {"base_fw"}

    def test_uri_is_the_document_only_no_child(self, firewalls):
        node = firewalls["nodes"][0]
        assert node["uri"] == "strata://firewall/fw_base"

    def test_rule_counts(self, firewalls):
        node = firewalls["nodes"][0]
        assert node["metadata"]["allow_count"] == 1
        assert node["metadata"]["deny_count"] == 1
        assert node["metadata"]["default_count"] == 1

    def test_reset_flag_is_exposed(self, firewalls):
        assert firewalls["nodes"][0]["metadata"]["reset"] is True

    def test_no_edges(self, firewalls):
        assert firewalls["edges"] == []


class TestDnsSource:
    @pytest.fixture
    def dns(self, config_workspace):
        controller = DiagramSourceController(config_workspace, entry="workspace.yaml", no_validate=True)
        return controller.resolve([_source("dns")])["dns"]

    def test_one_node_per_zone(self, dns):
        assert {n["id"] for n in dns["nodes"]} == {"example_com", "example_org"}

    def test_domain_dots_do_not_collide_across_tlds(self, dns):
        """example.com and example.org must not both slugify to 'example'."""
        ids = [n["id"] for n in dns["nodes"]]
        assert len(ids) == len(set(ids))

    def test_uri_is_document_plus_zone_name(self, dns):
        node = next(n for n in dns["nodes"] if n["id"] == "example_com")
        assert node["uri"] == "strata://dns/dns_primary/zone/example.com"

    def test_provider_comes_from_the_document_not_the_zone(self, dns):
        for node in dns["nodes"]:
            assert node["metadata"]["provider"] == "cloudflare"

    def test_record_count(self, dns):
        node = next(n for n in dns["nodes"] if n["id"] == "example_com")
        assert node["metadata"]["record_count"] == 2
        empty = next(n for n in dns["nodes"] if n["id"] == "example_org")
        assert empty["metadata"]["record_count"] == 0

    def test_ttl_defaults_are_exposed(self, dns):
        empty = next(n for n in dns["nodes"] if n["id"] == "example_org")
        assert empty["metadata"]["ttl"] == 3600


class TestConfigSourceSharedBehaviour:
    def test_no_references_yields_no_nodes_and_no_error(self, tmp_path):
        """A workspace that declares none of networks/firewalls/dns_zones is not an error."""
        (tmp_path / "workspace.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: workspace\nmeta:\n  name: bare\nspec: {}\n",
            encoding="utf-8",
        )
        controller = DiagramSourceController(tmp_path, entry="workspace.yaml", no_validate=True)
        context = controller.resolve([_source("network"), _source("firewalls"), _source("dns")])
        assert context["network"]["nodes"] == []
        assert context["firewalls"]["nodes"] == []
        assert context["dns"]["nodes"] == []
        assert not controller.has_errors()

    def test_missing_referenced_file_is_reported(self, tmp_path):
        (tmp_path / "workspace.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: workspace\n"
            "meta:\n"
            "  name: sample_ws\n"
            "spec:\n"
            "  networks:\n"
            "    - name: platform_net\n"
            "      file: missing.yaml\n",
            encoding="utf-8",
        )
        controller = DiagramSourceController(tmp_path, entry="workspace.yaml", no_validate=True)
        context = controller.resolve([_source("network")])
        assert context["network"]["nodes"] == []
        assert controller.has_errors()
        assert "does not exist" in controller.get_errors()[0]

    def test_cross_repo_reference_is_skipped_not_crashed(self, tmp_path):
        (tmp_path / "workspace.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: workspace\n"
            "meta:\n"
            "  name: sample_ws\n"
            "spec:\n"
            "  firewalls:\n"
            "    - name: shared_fw\n"
            '      file: "@other_repo/fw.yaml"\n',
            encoding="utf-8",
        )
        controller = DiagramSourceController(tmp_path, entry="workspace.yaml", no_validate=True)
        context = controller.resolve([_source("firewalls")])
        assert context["firewalls"]["nodes"] == []
        assert "cross-repository" in controller.get_errors()[0]

    def test_invalid_referenced_document_reports_per_reference_error(self, tmp_path):
        """One bad file must not silently produce zero nodes with no explanation."""
        (tmp_path / "workspace.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: workspace\n"
            "meta:\n"
            "  name: sample_ws\n"
            "spec:\n"
            "  dns_zones:\n"
            "    - name: broken_zone\n"
            "      file: dns.yaml\n",
            encoding="utf-8",
        )
        (tmp_path / "dns.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: dns\nmeta:\n  name: broken\nspec: {}\n",
            encoding="utf-8",
        )
        controller = DiagramSourceController(tmp_path, entry="workspace.yaml", no_validate=True)
        context = controller.resolve([_source("dns")])
        assert context["dns"]["nodes"] == []
        assert controller.has_errors()
        assert "broken_zone" in controller.get_errors()[0]

    def test_workspace_document_is_loaded_once_across_config_sources(self, config_workspace, monkeypatch):
        """Three config sources in one diagram must not re-resolve the workspace three times."""
        import strata.controllers.diagram_source_controller as module

        original = module.GraphController.resolve_workspace
        calls = []

        def counting_resolve(self):
            calls.append(1)
            return original(self)

        monkeypatch.setattr(module.GraphController, "resolve_workspace", counting_resolve)

        controller = DiagramSourceController(config_workspace, entry="workspace.yaml", no_validate=True)
        controller.resolve([_source("network"), _source("firewalls"), _source("dns")])
        assert len(calls) == 1

    def test_user_filter_applies_on_top(self, config_workspace):
        controller = DiagramSourceController(config_workspace, entry="workspace.yaml", no_validate=True)
        context = controller.resolve([_source("network", filter_={"id": "vnet_a"})])
        assert {n["id"] for n in context["network"]["nodes"]} == {"vnet_a"}


class TestConfigSourceUriRoundTrip:
    """Every URI a config source emits must be resolvable by 'diagram resolve'."""

    def test_network_uri_resolves(self, config_workspace):
        from strata.controllers.diagram_resolve_controller import DiagramResolveController

        result = DiagramResolveController(config_workspace).resolve("strata://network/net_platform/network/vnet_a")
        assert result is not None
        assert result["file"] == "net.yaml"

    def test_firewall_uri_resolves(self, config_workspace):
        from strata.controllers.diagram_resolve_controller import DiagramResolveController

        result = DiagramResolveController(config_workspace).resolve("strata://firewall/fw_base")
        assert result is not None
        assert result["file"] == "fw.yaml"

    def test_dns_uri_resolves(self, config_workspace):
        from strata.controllers.diagram_resolve_controller import DiagramResolveController

        result = DiagramResolveController(config_workspace).resolve("strata://dns/dns_primary/zone/example.com")
        assert result is not None
        assert result["file"] == "dns.yaml"
