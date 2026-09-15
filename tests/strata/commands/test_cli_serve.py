"""Tests for `_parse_trusted_issuer()` — parsing `--m2m-trusted-issuer` values into
`TrustedIssuer` entries (ADR-0067 Step 10).
"""

from __future__ import annotations

import click
import pytest

from strata.commands.cli_serve import _parse_trusted_issuer
from strata.server.auth.m2m_verifier import TrustedIssuer


class TestParseTrustedIssuer:
    def test_parses_required_fields(self) -> None:
        result = _parse_trusted_issuer(
            "name=github-actions,issuer=https://token.actions.githubusercontent.com,audience=api://strata"
        )
        assert result == TrustedIssuer(
            name="github-actions",
            issuer="https://token.actions.githubusercontent.com",
            audience="api://strata",
        )

    def test_defaults_subject_claim_to_sub(self) -> None:
        result = _parse_trusted_issuer("name=x,issuer=https://idp.example.test,audience=aud")
        assert result.subject_claim == "sub"

    def test_parses_optional_subject_claim(self) -> None:
        result = _parse_trusted_issuer(
            "name=github-actions,issuer=https://token.actions.githubusercontent.com,"
            "audience=api://strata,subject_claim=repository"
        )
        assert result.subject_claim == "repository"

    def test_tolerates_surrounding_whitespace(self) -> None:
        result = _parse_trusted_issuer(" name = github , issuer = https://idp.example.test , audience = aud ")
        assert result == TrustedIssuer(name="github", issuer="https://idp.example.test", audience="aud")

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(click.ClickException, match="issuer"):
            _parse_trusted_issuer("name=github-actions,audience=api://strata")

    def test_malformed_entry_without_equals_raises(self) -> None:
        with pytest.raises(click.ClickException, match="key=value"):
            _parse_trusted_issuer("not-a-key-value-pair")
