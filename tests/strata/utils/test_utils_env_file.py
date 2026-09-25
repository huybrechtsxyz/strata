#!/usr/bin/env python3
"""Tests for `env_file.load_env_file()` (docs/design/build-time-value-categories.md, Q9)."""

from pathlib import Path

from strata.utils.env_file import load_env_file


def test_parses_key_value_pairs(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("FOO=bar\nBAZ=qux\n")
    assert load_env_file(path) == {"FOO": "bar", "BAZ": "qux"}


def test_skips_comments_and_blank_lines(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("# a comment\n\nFOO=bar\n   \n# another\nBAZ=qux\n")
    assert load_env_file(path) == {"FOO": "bar", "BAZ": "qux"}


def test_strips_matching_quotes(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text('SINGLE=\'quoted\'\nDOUBLE="quoted"\nMISMATCHED=\'not"closed\n')
    result = load_env_file(path)
    assert result["SINGLE"] == "quoted"
    assert result["DOUBLE"] == "quoted"
    assert result["MISMATCHED"] == "'not\"closed"


def test_ignores_lines_without_equals(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("not_a_kv_line\nFOO=bar\n")
    assert load_env_file(path) == {"FOO": "bar"}


def test_empty_file_is_an_empty_dict(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("")
    assert load_env_file(path) == {}
