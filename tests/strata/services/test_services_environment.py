#!/usr/bin/env python3
"""Tests for EnvironmentService — declared keys and Phase 2 token resolution."""

from strata.models.dns_model import DnsModel
from strata.models.network_model import NetworkModel
from strata.services.environment_service import EnvironmentService


def _environment() -> EnvironmentService:
    service = EnvironmentService(
        data={
            "meta": {"name": "prd"},
            "spec": {
                "variables": [{"key": "PUBLIC_IP", "store": "constant", "value": "1.2.3.4"}],
                "secrets": [{"key": "DB_PASSWORD", "store": "constant", "value": "x"}],
                "features": [{"key": "ENABLE_X", "store": "constant", "value": "true"}],
            },
        }
    )
    service.validate()
    return service


def _dns(value: str) -> DnsModel:
    return DnsModel.model_validate(
        {
            "meta": {"name": "public-dns"},
            "spec": {
                "zones": [
                    {
                        "name": "example.com",
                        "records": [{"name": "@", "type": "A", "value": value}],
                        "default_tags": {"environment": "test"},
                    }
                ]
            },
        }
    )


def test_environment_service_validates_from_data():
    """An EnvironmentService constructed from an in-memory dict validates."""
    service = _environment()
    assert service.model is not None
    assert service.model.meta.name == "prd"


def test_declared_keys_are_grouped_by_token_kind():
    """declared_keys() is keyed by token kind, matching parsed tokens."""
    assert _environment().declared_keys() == {
        "var": {"PUBLIC_IP"},
        "secret": {"DB_PASSWORD"},
        "feature": {"ENABLE_X"},
    }


def test_document_with_resolvable_token_passes():
    """A token whose key the environment declares is accepted."""
    result = _environment().validate_document_tokens(_dns("${var:PUBLIC_IP}"))
    assert result.ok
    assert result.messages() == []


def test_document_with_unknown_key_is_rejected_with_path_and_suggestions():
    """An undeclared key is reported with where it was found and what exists."""
    result = _environment().validate_document_tokens(_dns("${var:GHOST_IP}"))
    assert not result.ok
    assert len(result.errors) == 1
    message = result.messages()[0]
    assert "GHOST_IP" in message
    assert "PUBLIC_IP" in message
    assert "records" in message


def test_token_kind_is_checked_against_the_right_store():
    """A key declared as a variable does not satisfy a '${secret:}' token."""
    result = _environment().validate_document_tokens(_dns("${secret:PUBLIC_IP}"))
    assert not result.ok
    assert "spec.secrets" in result.messages()[0]


def test_literal_document_without_tokens_passes():
    """A document with no tokens has nothing to resolve."""
    result = _environment().validate_document_tokens(_dns("1.2.3.4"))
    assert result.ok
    assert result.messages() == []


def test_tokens_are_found_in_deeply_nested_documents():
    """The walk is generic — it reaches tokens at any depth, in any kind."""
    network = NetworkModel.model_validate(
        {
            "meta": {"name": "core"},
            "spec": {
                "networks": [
                    {
                        "name": "vpc-main",
                        "address_space": ["10.0.0.0/16"],
                        "subnets": [{"name": "web", "cidr": "${var:MISSING_CIDR}"}],
                        "default_tags": {"environment": "test"},
                    }
                ]
            },
        }
    )
    result = _environment().validate_document_tokens(network)
    assert not result.ok
    message = result.messages()[0]
    assert "MISSING_CIDR" in message
    assert "subnets" in message


def test_multiple_tokens_in_one_string_are_each_checked():
    """A composite string reports one error per unresolved token."""
    result = _environment().validate_document_tokens(
        _dns("${var:PUBLIC_IP}-${var:NOPE}-${secret:ALSO_NOPE}")
    )
    assert not result.ok
    assert len(result.errors) == 2
