#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_utils_strata_uri.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : strata:// URI scheme tests for strata CLI (ADR-0034).
===============================================================================
"""

import pytest

from strata.utils.strata_uri import StrataUri, UriError, build_uri, parse_uri


class TestBuildUri:
    def test_document(self):
        assert build_uri("workspace", "platform") == "strata://workspace/platform"

    def test_child_object(self):
        assert build_uri("workspace", "platform", "resource", "app_server") == (
            "strata://workspace/platform/resource/app_server"
        )

    def test_file_keeps_its_slashes(self):
        assert build_uri("file", "deploy/deploy-prd.yaml") == "strata://file/deploy/deploy-prd.yaml"

    def test_child_kind_without_a_name_is_dropped(self):
        assert build_uri("workspace", "platform", "resource", None) == "strata://workspace/platform"


class TestParseUri:
    def test_document(self):
        assert parse_uri("strata://workspace/platform") == StrataUri(kind="workspace", name="platform")

    def test_child_object(self):
        parsed = parse_uri("strata://workspace/platform/resource/app_server")
        assert parsed == StrataUri("workspace", "platform", "resource", "app_server")

    def test_file_path_is_taken_verbatim(self):
        """'file' is the one kind whose name is a path, so it keeps its slashes."""
        parsed = parse_uri("strata://file/deploy/nested/deploy-prd.yaml")
        assert parsed.kind == "file"
        assert parsed.name == "deploy/nested/deploy-prd.yaml"
        assert parsed.is_file

    def test_non_file_uris_are_not_files(self):
        assert not parse_uri("strata://workspace/platform").is_file

    def test_whitespace_is_tolerated(self):
        assert parse_uri("  strata://workspace/platform  ").name == "platform"

    def test_secret_key_child(self):
        parsed = parse_uri("strata://environment/env-prd/secret/DB_PASSWORD")
        assert parsed.child_kind == "secret"
        assert parsed.child_name == "DB_PASSWORD"


class TestRoundTrip:
    @pytest.mark.parametrize(
        "uri",
        [
            "strata://file/deploy/deploy-prd.yaml",
            "strata://workspace/platform/resource/app_server",
            "strata://deployment/acme_prd/stage/infrastructure",
            "strata://environment/env-prd/secret/DB_PASSWORD",
            "strata://module/api-gateway/service/web",
        ],
    )
    def test_parse_then_str_is_identity(self, uri):
        assert str(parse_uri(uri)) == uri


class TestParseErrors:
    def test_wrong_scheme(self):
        with pytest.raises(UriError, match="not a strata URI"):
            parse_uri("https://example.com/x")

    def test_empty(self):
        with pytest.raises(UriError):
            parse_uri("")

    def test_kind_only(self):
        with pytest.raises(UriError, match="names no workspace"):
            parse_uri("strata://workspace")

    def test_file_without_a_path(self):
        with pytest.raises(UriError, match="names no file"):
            parse_uri("strata://file/")

    def test_three_segments_is_ambiguous(self):
        """A child needs both a kind and a name — one of the two is a typo."""
        with pytest.raises(UriError, match="segments"):
            parse_uri("strata://workspace/platform/resource")

    def test_too_many_segments(self):
        with pytest.raises(UriError, match="segments"):
            parse_uri("strata://workspace/a/resource/b/extra/c")

    def test_empty_segment(self):
        with pytest.raises(UriError, match="empty segment"):
            parse_uri("strata://workspace/platform//app_server")

    def test_uppercase_kind_is_rejected(self):
        with pytest.raises(UriError, match="no valid kind"):
            parse_uri("strata://Workspace/platform")
