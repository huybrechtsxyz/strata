#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_dns.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : Tests for DNS models in strata.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from strata.models.dns_model import DnsModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


DNS_FOLDER = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "data",
    "dns",
)

DNS_VALID_FILES = [
    os.path.join(DNS_FOLDER, "dns-standard.yaml"),
]

DNS_INVALID_FILES = [
    os.path.join(DNS_FOLDER, "dns-invalid.yaml"),
]


@pytest.mark.parametrize("yaml_path", DNS_VALID_FILES)
def test_valid_dns_model_standard(yaml_path):
    """Test that a DNS YAML file is a valid DnsModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = DnsModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", DNS_INVALID_FILES)
def test_invalid_dns_model(yaml_path):
    """Test that an invalid DNS YAML file raises ValidationError."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)
    model = None
    assert model is None


def test_dns_record_priority_mx_valid():
    """MX record with priority is valid."""
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "dns",
        "meta": {"name": "dns_test"},
        "spec": {
            "zones": [
                {
                    "name": "example.com",
                    "records": [{"name": "@", "type": "MX", "value": "mail.example.com.", "priority": 10}],
                }
            ]
        },
    }
    model = DnsModel.model_validate(data)
    assert model.spec.zones[0].records[0].priority == 10


def test_dns_record_priority_on_a_invalid():
    """A record with priority set raises ValidationError."""
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "dns",
        "meta": {"name": "dns_test"},
        "spec": {
            "zones": [
                {
                    "name": "example.com",
                    "records": [{"name": "@", "type": "A", "value": "1.2.3.4", "priority": 10}],
                }
            ]
        },
    }
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_zone_requires_at_least_one():
    """spec with empty zones list raises ValidationError."""
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "dns",
        "meta": {"name": "dns_test"},
        "spec": {"zones": []},
    }
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_ttl_must_be_positive():
    """Record-level ttl=0 raises ValidationError."""
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "dns",
        "meta": {"name": "dns_test"},
        "spec": {
            "zones": [
                {
                    "name": "example.com",
                    "records": [{"name": "@", "type": "A", "value": "1.2.3.4", "ttl": 0}],
                }
            ]
        },
    }
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_unique_zone_names():
    """Duplicate zone names in the same DnsModel raises ValidationError."""
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "dns",
        "meta": {"name": "dns_test"},
        "spec": {
            "zones": [
                {"name": "example.com", "records": [{"name": "@", "type": "A", "value": "1.2.3.4"}]},
                {"name": "example.com", "records": [{"name": "www", "type": "A", "value": "5.6.7.8"}]},
            ]
        },
    }
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_kind_must_match():
    """kind='firewall' in a DNS YAML payload raises ValidationError."""
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "firewall",
        "meta": {"name": "dns_test"},
        "spec": {"zones": [{"name": "example.com", "records": [{"name": "@", "type": "A", "value": "1.2.3.4"}]}]},
    }
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


# ---------------------------------------------------------------------------
# Record value union tests (value / var / secret)
# ---------------------------------------------------------------------------


def _dns_data(records: list, references: dict | None = None) -> dict:
    """Build a minimal DnsModel payload with the given records and optional references."""
    spec: dict = {"zones": [{"name": "example.com", "records": records}]}
    if references is not None:
        spec["references"] = references
    return {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "dns",
        "meta": {"name": "dns_test"},
        "spec": spec,
    }


def test_dns_record_value_literal():
    """value: 'literal' is valid — the common case is still supported."""
    data = _dns_data([{"name": "@", "type": "A", "value": "1.2.3.4"}])
    model = DnsModel.model_validate(data)
    record = model.spec.zones[0].records[0]
    assert record.value == "1.2.3.4"


def test_dns_record_var_valid():
    """var: 'spf_key' with matching references.variables entry is valid."""
    data = _dns_data(
        records=[{"name": "@", "type": "TXT", "var": "spf_key"}],
        references={"variables": ["spf_key"]},
    )
    model = DnsModel.model_validate(data)
    record = model.spec.zones[0].records[0]
    assert record.var == "spf_key"


def test_dns_record_secret_valid():
    """secret: 'verify_token' with matching references.secrets entry is valid."""
    data = _dns_data(
        records=[{"name": "_verify", "type": "TXT", "secret": "verify_token"}],
        references={"secrets": ["verify_token"]},
    )
    model = DnsModel.model_validate(data)
    record = model.spec.zones[0].records[0]
    assert record.secret == "verify_token"


def test_dns_record_no_source_invalid():
    """Record with none of value/var/secret raises ValidationError."""
    data = _dns_data([{"name": "@", "type": "A"}])
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_record_two_sources_invalid():
    """Record with both value and var set raises ValidationError."""
    data = _dns_data(
        records=[{"name": "@", "type": "TXT", "value": "some text", "var": "spf_key"}],
        references={"variables": ["spf_key"]},
    )
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_var_undeclared_in_references_invalid():
    """Record uses var: 'spf_key' but key not in spec.references.variables — raises ValidationError."""
    data = _dns_data(
        records=[{"name": "@", "type": "TXT", "var": "spf_key"}],
        references={"variables": ["other_key"]},
    )
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_secret_undeclared_in_references_invalid():
    """Record uses secret: 'verify_token' but key not in spec.references.secrets — raises ValidationError."""
    data = _dns_data(
        records=[{"name": "_verify", "type": "TXT", "secret": "verify_token"}],
        references={"secrets": ["other_secret"]},
    )
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_var_without_references_block_invalid():
    """Record uses var: 'spf_key' but spec.references is None — raises ValidationError."""
    data = _dns_data(records=[{"name": "@", "type": "TXT", "var": "spf_key"}])
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_secret_without_references_block_invalid():
    """Record uses secret: 'verify_token' but spec.references is None — raises ValidationError."""
    data = _dns_data(records=[{"name": "_verify", "type": "TXT", "secret": "verify_token"}])
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_record_output_key_valid():
    """output_key: 'hearth_public_ip' is valid without any spec.references entry —
    unlike var:/secret:, output_key names a cross-stage provisioner output, not an
    environment-declared value, so it is never subject to the references check."""
    data = _dns_data(records=[{"name": "@", "type": "A", "output_key": "hearth_public_ip"}])
    model = DnsModel.model_validate(data)
    record = model.spec.zones[0].records[0]
    assert record.output_key == "hearth_public_ip"


def test_dns_record_output_key_and_value_invalid():
    """Record with both value and output_key set raises ValidationError."""
    data = _dns_data(records=[{"name": "@", "type": "A", "value": "1.2.3.4", "output_key": "hearth_public_ip"}])
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)


def test_dns_record_output_key_and_secret_invalid():
    """Record with both secret and output_key set raises ValidationError."""
    data = _dns_data(
        records=[{"name": "@", "type": "TXT", "secret": "tok", "output_key": "hearth_public_ip"}],
        references={"secrets": ["tok"]},
    )
    with pytest.raises(ValidationError):
        DnsModel.model_validate(data)
