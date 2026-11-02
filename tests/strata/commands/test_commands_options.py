#!/usr/bin/env python3
"""Tests for `commands.options` — shared CLI options and env-var-backed
defaults, resolved once at the command layer (docs/decisions/
0020-v1-consumer-feature-priority.md; mirrors v1's real `cli.py` precedence:
explicit flag > STRATA_* env var > built-in default)."""

from pathlib import Path

from strata.commands.options import resolve_work_path


def test_explicit_path_wins_over_the_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATA_WORK_PATH", str(tmp_path / "elsewhere"))
    explicit = tmp_path / "here"
    assert resolve_work_path(explicit) == explicit


def test_env_var_is_used_when_no_explicit_path_is_given(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATA_WORK_PATH", str(tmp_path))
    assert resolve_work_path(None) == tmp_path


def test_falls_back_to_the_current_directory_when_neither_is_set(monkeypatch):
    monkeypatch.delenv("STRATA_WORK_PATH", raising=False)
    assert resolve_work_path(None) == Path.cwd()
