#!/usr/bin/env python3
"""Tests for `git_checkout.ensure_synced_checkout` (docs/design/
audit-commands.md's "Read mechanism — reuse, not reimplement, the push
side's fetch+reset").

Extracted from `audit_push.py`'s own `_ensure_checkout()` once
`strata audit status`'s read-only checkout needed the identical behaviour
(docs/design/audit-commands.md's Phase 2) — these are the direct unit
tests the logic never had in isolation before the extraction (it was only
exercised indirectly via `test_audit_push.py`'s `push_audit_files()`
round-trips). Mirrors that file's own style: mocked `run_command` for the
branching logic, one real end-to-end test against a real local bare repo.
"""

import subprocess
from pathlib import Path

import pytest

from strata.controllers import git_checkout as git_checkout_module
from strata.controllers.git_checkout import ensure_synced_checkout
from strata.utils.transport import CommandResult


def _capture(monkeypatch):
    captured: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        captured.append(list(args))
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_checkout_module, "run_command", _fake_run_command)
    return captured


# ---------------------------------------------------------------------------
# Mocked-command tests
# ---------------------------------------------------------------------------


def test_fresh_checkout_clones_then_fetches_and_resets(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    checkout_path = tmp_path / "checkout"

    result = ensure_synced_checkout("https://example.com/org/repo.git", "main", checkout_path)

    assert result.success is True
    assert captured[0][:2] == ["git", "clone"]
    assert captured[1][:2] == ["git", "fetch"]
    assert captured[2] == ["git", "rev-parse", "--verify", "origin/main"]
    assert captured[3] == ["git", "reset", "--hard", "origin/main"]


def test_existing_checkout_is_not_cloned_again(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()

    result = ensure_synced_checkout("https://example.com/org/repo.git", "main", checkout_path)

    assert result.success is True
    assert not any("clone" in c for c in captured)
    assert captured[0][:2] == ["git", "fetch"]


def test_branch_not_yet_on_remote_falls_back_to_origin_head(tmp_path: Path, monkeypatch):
    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[:3] == ["git", "rev-parse", "--verify"] and args[3] == "origin/missing-branch":
            return CommandResult(returncode=1, stdout="", stderr="fatal: unknown revision")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_checkout_module, "run_command", _fake_run_command)
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()

    result = ensure_synced_checkout("https://example.com/org/repo.git", "missing-branch", checkout_path)

    assert result.success is True


def test_completely_empty_remote_succeeds_without_a_reset(tmp_path: Path, monkeypatch):
    """No commits exist anywhere upstream yet — nothing to reset to, but not a failure."""

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[:2] == ["git", "rev-parse"]:
            return CommandResult(returncode=1, stdout="", stderr="fatal: unknown revision")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_checkout_module, "run_command", _fake_run_command)
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()

    result = ensure_synced_checkout("https://example.com/org/repo.git", "main", checkout_path)

    assert result.success is True


def test_clone_failure_short_circuits_before_any_fetch(tmp_path: Path, monkeypatch):
    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if "clone" in args:
            return CommandResult(returncode=1, stdout="", stderr="fatal: could not read")
        raise AssertionError("should not run further commands after a clone failure")

    monkeypatch.setattr(git_checkout_module, "run_command", _fake_run_command)
    result = ensure_synced_checkout("https://example.com/org/repo.git", "main", tmp_path / "checkout")

    assert result.success is False
    assert "clone" in result.detail


def test_fetch_failure_is_reported(tmp_path: Path, monkeypatch):
    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[:2] == ["git", "fetch"]:
            return CommandResult(returncode=1, stdout="", stderr="fatal: could not read from remote")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_checkout_module, "run_command", _fake_run_command)
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()

    result = ensure_synced_checkout("https://example.com/org/repo.git", "main", checkout_path)

    assert result.success is False
    assert "fetch" in result.detail


def test_reset_failure_is_reported(tmp_path: Path, monkeypatch):
    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[:2] == ["git", "reset"]:
            return CommandResult(returncode=1, stdout="", stderr="fatal: could not reset")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_checkout_module, "run_command", _fake_run_command)
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()

    result = ensure_synced_checkout("https://example.com/org/repo.git", "main", checkout_path)

    assert result.success is False
    assert "reset" in result.detail


# ---------------------------------------------------------------------------
# Real end-to-end test — a real local bare git repository
# ---------------------------------------------------------------------------


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=False)
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_real_sync_round_trip(tmp_path: Path):
    """Real clone -> fetch -> reset against a real local bare repository, run
    twice: once against a genuinely empty remote (no commits at all), once
    after a real commit lands on the branch — confirming both the "nothing
    to reset to yet" path and the ordinary fetch+reset path actually work,
    not just that the right subprocess args were assembled."""
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    checkout_path = tmp_path / "checkout"

    first = ensure_synced_checkout(str(bare_repo), "main", checkout_path)
    assert first.success, first.detail
    assert (checkout_path / ".git").exists()

    # Make a real commit land on the remote via an independent clone, then
    # confirm a second sync of the SAME checkout picks it up.
    other_clone = tmp_path / "other-clone"
    subprocess.run(["git", "clone", str(bare_repo), str(other_clone)], check=True, capture_output=True)
    (other_clone / "file.txt").write_text("hello")
    subprocess.run(["git", "-C", str(other_clone), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(other_clone), "-c", "user.name=t", "-c", "user.email=t@t.com", "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(other_clone), "push", "origin", "main"], check=True, capture_output=True)

    second = ensure_synced_checkout(str(bare_repo), "main", checkout_path)
    assert second.success, second.detail
    assert (checkout_path / "file.txt").exists()
    assert (checkout_path / "file.txt").read_text() == "hello"
