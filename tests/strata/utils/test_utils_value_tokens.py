#!/usr/bin/env python3
"""Tests for `value_tokens.resolve_value_tokens()`/`resolve_value_tokens_in_mapping()`
— the deploy-time Value token resolver (docs/design/value-token-resolution.md,
docs/design/deploy-command.md)."""

import pytest

from strata.utils.value_tokens import (
    extract_value_tokens,
    resolve_value_tokens,
    resolve_value_tokens_in_mapping,
    validate_value_tokens,
)


def test_resolve_value_tokens_replaces_a_single_token():
    assert resolve_value_tokens("${var:REGION}", {"REGION": "westeurope"}) == "westeurope"


def test_resolve_value_tokens_replaces_multiple_tokens_in_one_string():
    result = resolve_value_tokens(
        "postgres://${var:HOST}/${secret:DB_PASS}",
        {"HOST": "db.internal", "DB_PASS": "hunter2"},
    )
    assert result == "postgres://db.internal/hunter2"


def test_resolve_value_tokens_ignores_kind_prefix_only_key_matters():
    """var/secret/feature only matter to the token's author — resolution reads key only."""
    assert resolve_value_tokens("${feature:ENABLE_X}", {"ENABLE_X": "true"}) == "true"
    assert resolve_value_tokens("${secret:ENABLE_X}", {"ENABLE_X": "true"}) == "true"


def test_resolve_value_tokens_plain_literal_passes_through_unchanged():
    assert resolve_value_tokens("cdn-feeds.omp.com", {}) == "cdn-feeds.omp.com"


def test_resolve_value_tokens_raises_on_unresolved_key():
    with pytest.raises(ValueError, match="did not resolve to a value"):
        resolve_value_tokens("${var:MISSING}", {})


def test_resolve_value_tokens_matches_extract_value_tokens_keys():
    """Sanity check both functions agree on what a token's key is."""
    text = "${var:tf_state_resource_group}"
    (kind, key) = extract_value_tokens(text)[0]
    assert kind == "var"
    assert resolve_value_tokens(text, {key: "rg-prod"}) == "rg-prod"


def test_resolve_value_tokens_in_mapping_resolves_flat_string_values():
    """Real shape: provisioner.backend.configuration's real values (cfg-int-deployment)."""
    data = {
        "resource_group_name": "${var:tf_state_resource_group}",
        "storage_account_name": "${var:tf_state_storage_account}",
        "use_azuread_auth": "true",
    }
    values = {"tf_state_resource_group": "rg-int", "tf_state_storage_account": "stint001"}
    resolved = resolve_value_tokens_in_mapping(data, values)
    assert resolved == {
        "resource_group_name": "rg-int",
        "storage_account_name": "stint001",
        "use_azuread_auth": "true",
    }


def test_resolve_value_tokens_in_mapping_recurses_into_nested_dicts_and_lists():
    data = {
        "nested": {"key": "${var:REGION}"},
        "list": ["${var:REGION}", "literal"],
    }
    resolved = resolve_value_tokens_in_mapping(data, {"REGION": "westeurope"})
    assert resolved == {"nested": {"key": "westeurope"}, "list": ["westeurope", "literal"]}


def test_resolve_value_tokens_in_mapping_passes_through_non_string_leaves():
    data = {"count": 3, "enabled": True, "nothing": None}
    assert resolve_value_tokens_in_mapping(data, {}) == data


def test_resolve_value_tokens_in_mapping_raises_on_unresolved_key():
    with pytest.raises(ValueError, match="did not resolve to a value"):
        resolve_value_tokens_in_mapping({"key": "${secret:MISSING}"}, {})


def test_malformed_token_still_rejected_by_validate_value_tokens_first():
    """resolve_value_tokens() assumes Phase 1 syntax validation already ran —
    confirm a malformed token is still caught upstream, not silently ignored."""
    with pytest.raises(ValueError, match="Malformed Value token"):
        validate_value_tokens("${vars:region}")
