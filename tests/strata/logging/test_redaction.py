#!/usr/bin/env python3
"""Tests for strata.logging.redaction."""

from strata.logging.redaction import censor_secrets, censored, is_sensitive


def test_is_sensitive_matches_exact_keys():
    assert is_sensitive("auth")
    assert is_sensitive("PWD")
    assert not is_sensitive("path")  # 'pat' must not match as a substring here


def test_is_sensitive_matches_fragments_case_insensitive():
    assert is_sensitive("github_token")
    assert is_sensitive("AZURE_CLIENT_SECRET")
    assert is_sensitive("db_password")


def test_is_sensitive_does_not_match_plural_tokens_count():
    """A plural 'tokens' is a usage count, not a credential."""
    assert not is_sensitive("input_tokens")
    assert not is_sensitive("max_tokens")
    assert not is_sensitive("tokens")


def test_censored_redacts_nested_dict():
    value = {"password": "hunter2", "nested": {"api_key": "abc123", "count": 5}}
    result = censored(value)
    assert result["password"] == "***redacted***"
    assert result["nested"]["api_key"] == "***redacted***"
    assert result["nested"]["count"] == 5


def test_censored_stops_at_max_depth():
    deeply_nested: dict = {"password": "top"}
    current = deeply_nested
    for _ in range(6):
        current["child"] = {"password": "deep"}
        current = current["child"]
    result = censored(deeply_nested)
    assert result["password"] == "***redacted***"


def test_censor_secrets_processor_redacts_event_dict():
    event_dict = {"event": "login", "password": "hunter2", "user": "alice"}
    result = censor_secrets(None, "info", event_dict)
    assert result["password"] == "***redacted***"
    assert result["user"] == "alice"
    assert result["event"] == "login"
