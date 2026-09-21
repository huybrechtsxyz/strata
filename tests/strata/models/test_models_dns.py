#!/usr/bin/env python3
"""Tests for DnsModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.dns_model import DnsModel


def _minimal_dns() -> dict:
    return {
        "meta": {"name": "public-dns"},
        "spec": {
            "zones": [
                {
                    "name": "example.com",
                    "records": [
                        {"name": "@", "type": "A", "value": "1.2.3.4"},
                    ],
                    "default_tags": {"environment": "test"},
                }
            ]
        },
    }


def test_dns_minimal_is_valid():
    """A minimal DNS document (only required fields) validates successfully."""
    model = DnsModel.model_validate(_minimal_dns())
    assert model.meta.name == "public-dns"
    assert model.spec.zones[0].name == "example.com"
    assert model.spec.zones[0].records[0].value == "1.2.3.4"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "dns"


def test_dns_record_accepts_value_tokens():
    """A record value may embed '${var:}'/'${secret:}' tokens instead of a literal."""
    data = _minimal_dns()
    data["spec"]["zones"][0]["records"][0]["value"] = "${var:public_ip}"
    model = DnsModel.model_validate(data)
    assert model.spec.zones[0].records[0].value == "${var:public_ip}"


def test_dns_record_accepts_composite_value_tokens():
    """A record value may mix a literal and multiple tokens (concatenation case)."""
    data = _minimal_dns()
    data["spec"]["zones"][0]["records"][0]["value"] = "prefix-${var:region}-${secret:suffix_key}"
    model = DnsModel.model_validate(data)
    assert model.spec.zones[0].records[0].value == "prefix-${var:region}-${secret:suffix_key}"


def test_dns_record_rejects_malformed_value_token():
    """An unknown token kind is rejected at Phase 1, without needing an Environment."""
    data = _minimal_dns()
    data["spec"]["zones"][0]["records"][0]["value"] = "${vars:region}"
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_record_rejects_empty_token_key():
    """A token with a missing key is rejected at Phase 1."""
    data = _minimal_dns()
    data["spec"]["zones"][0]["records"][0]["value"] = "${var:}"
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_record_rejects_output_key_field():
    """output_key is not ported yet (deferred to ADR-0006's Context concept) — rejected as unknown."""
    data = _minimal_dns()
    data["spec"]["zones"][0]["records"][0] = {"name": "vm", "type": "A", "output_key": "vm_public_ip"}
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_record_rejects_missing_value():
    """A record with no value set is rejected (value is required)."""
    data = _minimal_dns()
    data["spec"]["zones"][0]["records"][0] = {"name": "@", "type": "A"}
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_record_priority_allowed_for_mx():
    """priority is accepted for MX records."""
    data = _minimal_dns()
    data["spec"]["zones"][0]["records"][0] = {
        "name": "@",
        "type": "MX",
        "value": "mail.example.com",
        "priority": 10,
    }
    model = DnsModel.model_validate(data)
    assert model.spec.zones[0].records[0].priority == 10


def test_dns_record_rejects_priority_for_non_mx_srv():
    """priority is rejected for record types other than MX/SRV."""
    data = _minimal_dns()
    data["spec"]["zones"][0]["records"][0]["priority"] = 10
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_rejects_duplicate_zone_names():
    """Duplicate zone names within spec.zones are rejected."""
    data = _minimal_dns()
    data["spec"]["zones"].append(dict(data["spec"]["zones"][0]))
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_rejects_empty_zones_list():
    """An empty spec.zones list is rejected (min_length=1)."""
    data = _minimal_dns()
    data["spec"]["zones"] = []
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_rejects_references_field():
    """spec.references is rejected (Requirement was removed as a schema concept — ADR-0002)."""
    data = _minimal_dns()
    data["spec"]["references"] = {"variables": ["public_ip"]}
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_dns()
    data["spec"]["zones"][0]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)
