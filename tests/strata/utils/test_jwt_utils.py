#!/usr/bin/env python3
"""Unit tests for the minimal JWT payload decoder (ADR-0067)."""

import base64
import json

from strata.utils.jwt_utils import decode_payload_unverified


def _make_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.fakesignature"


def test_decodes_valid_payload():
    token = _make_jwt({"email": "dev@example.com", "sub": "u123"})
    claims = decode_payload_unverified(token)
    assert claims == {"email": "dev@example.com", "sub": "u123"}


def test_handles_missing_padding():
    # Deliberately construct a payload whose base64 length needs padding.
    token = _make_jwt({"a": "b"})
    claims = decode_payload_unverified(token)
    assert claims == {"a": "b"}


def test_malformed_token_returns_empty_dict():
    assert decode_payload_unverified("not-a-jwt") == {}


def test_empty_string_returns_empty_dict():
    assert decode_payload_unverified("") == {}


def test_invalid_base64_returns_empty_dict():
    assert decode_payload_unverified("aaa.!!!not-base64!!!.bbb") == {}


def test_valid_base64_but_not_json_returns_empty_dict():
    payload_segment = base64.urlsafe_b64encode(b"not json").decode().rstrip("=")
    token = f"header.{payload_segment}.sig"
    assert decode_payload_unverified(token) == {}
