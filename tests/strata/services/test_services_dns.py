#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_dns.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : DnsService tests for strata CLI.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.models.dns_model import DnsModel
from strata.services.dns_service import DnsService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


def _make_dns_model(name: str, zones: list) -> DnsModel:
    """Construct a minimal DnsModel programmatically."""
    return DnsModel.model_validate(
        {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "dns",
            "meta": {"name": name},
            "spec": {"zones": zones},
        }
    )


class TestDnsService:
    @pytest.fixture
    def get_dns_service(self):
        return DnsService(_data("dns/dns-standard.yaml"))

    def test_get_model_class(self, get_dns_service):
        service = get_dns_service
        model_class = service._get_model_class()
        assert model_class == DnsModel

    def test_validate_standard(self, get_dns_service):
        service = get_dns_service
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.is_validated()

    def test_get_kind_after_validate(self, get_dns_service):
        service = get_dns_service
        service.validate()
        assert service.get_kind() == "dns"

    def test_merge_dns_zones(self):
        """Merging two DnsModels with overlapping zone names: last-wins by zone name."""
        first = _make_dns_model(
            "dns_first",
            [
                {
                    "name": "example.com",
                    "records": [{"name": "@", "type": "A", "value": "1.1.1.1"}],
                },
                {
                    "name": "other.com",
                    "records": [{"name": "@", "type": "A", "value": "2.2.2.2"}],
                },
            ],
        )
        second = _make_dns_model(
            "dns_second",
            [
                {
                    "name": "example.com",
                    "records": [{"name": "@", "type": "A", "value": "9.9.9.9"}],
                },
            ],
        )
        merged = DnsService.merge_dns([first, second])

        zone_names = [z.name for z in merged.spec.zones]
        assert "example.com" in zone_names
        assert "other.com" in zone_names

        example_zone = next(z for z in merged.spec.zones if z.name == "example.com")
        a_record = next(r for r in example_zone.records if r.name == "@" and r.type.value == "A")
        # last-wins: second model's value
        assert a_record.value == "9.9.9.9"

    def test_merge_dns_records(self):
        """Records with the same (name, type) key: last-wins semantics."""
        first = _make_dns_model(
            "dns_first",
            [
                {
                    "name": "example.com",
                    "records": [
                        {"name": "@", "type": "TXT", "value": "v=spf1 ~all"},
                        {"name": "@", "type": "A", "value": "1.1.1.1"},
                    ],
                }
            ],
        )
        second = _make_dns_model(
            "dns_second",
            [
                {
                    "name": "example.com",
                    "records": [
                        {"name": "@", "type": "TXT", "value": "v=spf1 include:example.com ~all"},
                    ],
                }
            ],
        )
        merged = DnsService.merge_dns([first, second])

        example_zone = next(z for z in merged.spec.zones if z.name == "example.com")
        txt_records = [r for r in example_zone.records if r.name == "@" and r.type.value == "TXT"]
        # last-wins: only one TXT record for "@", with second model's value
        assert len(txt_records) == 1
        assert txt_records[0].value == "v=spf1 include:example.com ~all"

        # A record from first is preserved
        a_records = [r for r in example_zone.records if r.name == "@" and r.type.value == "A"]
        assert len(a_records) == 1
        assert a_records[0].value == "1.1.1.1"
