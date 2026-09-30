#!/usr/bin/env python3
"""Tests for `audit_push.push_audit_files` (docs/design/audit-trail.md's
"Durable push destination"/"The endpoints").

Phase 4 of that doc's Layer 2 Implementation Plan. Most tests mock
`run_command` (matching `test_remote_resolution.py`'s own established
style); one true end-to-end test drives a real local bare git repository,
confirming the whole clone/fetch/reset/add/commit/push chain actually
works, not just that the right subprocess args were assembled.
"""

import subprocess
from pathlib import Path

import pytest

from strata.controllers import audit_push as audit_push_module
from strata.controllers.audit_push import push_audit_files
from strata.models.audit_model import AuditGitSinkTargetModel
from strata.models.solution_model import RemoteType, SolutionModel, SolutionRemoteModel
from strata.utils import layout
from strata.utils.transport import CommandResult


def _sink(remote: str = "xyz-configuration", branch: str = "main", path: str = "audit") -> AuditGitSinkTargetModel:
    return AuditGitSinkTargetModel(remote=remote, branch=branch, path=path)


def _solution(remotes: list[SolutionRemoteModel] | None = None) -> SolutionModel:
    return SolutionModel.model_validate(
        {
            "meta": {"name": "xyz-solution"},
            "spec": {"remotes": [r.model_dump() for r in (remotes or [])]},
        }
    )


def _git_remote(name: str = "xyz-configuration", url: str = "https://example.com/org/xyz-configuration.git"):
    return SolutionRemoteModel(name=name, type=RemoteType.GIT, url=url, reference="main")


def _capture(monkeypatch):
    captured: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        captured.append(list(args))
        if args[:2] == ["git", "status"]:
            # Simulate real changes present, so add/commit/push aren't
            # short-circuited by the "nothing to commit" fast path.
            return CommandResult(returncode=0, stdout=" M _manifest.json\n", stderr="")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(audit_push_module, "run_command", _fake_run_command)
    return captured


# ---------------------------------------------------------------------------
# Mocked-command tests
# ---------------------------------------------------------------------------


def test_unknown_remote_fails_without_running_any_command(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    result = push_audit_files(tmp_path, _sink(remote="no-such-remote"), _solution(), {}, Path("p"), "actor")
    assert result.success is False
    assert "not declared" in result.detail
    assert captured == []


def test_non_git_remote_type_fails(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    remote = SolutionRemoteModel(name="xyz-configuration", type=RemoteType.LOCAL, url=".")
    result = push_audit_files(tmp_path, _sink(), _solution([remote]), {}, Path("p"), "actor")
    assert result.success is False
    assert "needs 'git'" in result.detail
    assert captured == []


def test_no_solution_fails_cleanly(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    result = push_audit_files(tmp_path, _sink(), None, {}, Path("p"), "actor")
    assert result.success is False
    assert captured == []


def test_fresh_checkout_clones_fetches_resets_and_pushes(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    manifest_src = tmp_path / "_manifest.json"
    manifest_src.write_text("{}")

    result = push_audit_files(
        tmp_path, _sink(), _solution([_git_remote()]), {"_manifest.json": manifest_src}, Path("run1"), "ci-runner"
    )

    assert result.success is True
    commands = [c[0] for c in captured]
    assert commands[0:2] == ["git", "git"]  # clone, then fetch
    assert "clone" in captured[0]
    assert "fetch" in captured[1]
    assert any("push" in c for c in captured)
    assert any(c[:3] == ["git", "push", "origin"] and c[3] == "HEAD:main" for c in captured)


def test_identity_configured_before_commit(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    src = tmp_path / "_manifest.json"
    src.write_text("{}")

    push_audit_files(tmp_path, _sink(), _solution([_git_remote()]), {"_manifest.json": src}, Path("run1"), "jane.doe")

    config_calls = [c for c in captured if c[:2] == ["git", "config"]]
    assert ["git", "config", "user.name", "jane.doe"] in config_calls
    assert ["git", "config", "user.email", "jane.doe@strata-audit.local"] in config_calls


def test_clone_failure_short_circuits(tmp_path: Path, monkeypatch):
    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if "clone" in args:
            return CommandResult(returncode=1, stdout="", stderr="fatal: could not read")
        raise AssertionError("should not run further commands after a clone failure")

    monkeypatch.setattr(audit_push_module, "run_command", _fake_run_command)
    result = push_audit_files(tmp_path, _sink(), _solution([_git_remote()]), {}, Path("run1"), "actor")
    assert result.success is False
    assert "clone" in result.detail


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
def test_real_push_round_trip(tmp_path: Path):
    """Real clone -> fetch -> reset -> add -> commit -> push against a real local
    bare repository, run twice to confirm the second push (fetch+reset against
    an origin/<branch> that now exists) works too, not just the first."""
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)

    root = tmp_path / "solution"
    root.mkdir()
    remote = SolutionRemoteModel(name="xyz-configuration", type=RemoteType.GIT, url=str(bare_repo), reference="main")
    solution = _solution([remote])
    sink = _sink()

    manifest_src = root / "_manifest.json"
    manifest_src.write_text('{"execution_id": "first"}')

    first = push_audit_files(root, sink, solution, {"_manifest.json": manifest_src}, Path("run1"), "ci-runner")
    assert first.success, first.detail

    # Verify by cloning the bare repo fresh, independent of strata's own checkout.
    verify_dir = tmp_path / "verify"
    subprocess.run(["git", "clone", str(bare_repo), str(verify_dir)], check=True, capture_output=True)
    pushed_file = verify_dir / sink.path / "run1" / "_manifest.json"
    assert pushed_file.exists()
    assert pushed_file.read_text() == '{"execution_id": "first"}'

    # Second run, second file — the checkout directory is reused, fetch+reset
    # must handle a branch that now really exists on origin.
    manifest_src.write_text('{"execution_id": "second"}')
    second = push_audit_files(root, sink, solution, {"_manifest.json": manifest_src}, Path("run2"), "ci-runner")
    assert second.success, second.detail

    subprocess.run(["git", "-C", str(verify_dir), "pull"], check=True, capture_output=True)
    assert (verify_dir / sink.path / "run1" / "_manifest.json").exists()  # first run's file still present
    second_file = verify_dir / sink.path / "run2" / "_manifest.json"
    assert second_file.exists()
    assert second_file.read_text() == '{"execution_id": "second"}'


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_real_push_checkout_path_matches_layout(tmp_path: Path):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    root = tmp_path / "solution"
    root.mkdir()
    remote = SolutionRemoteModel(name="xyz-configuration", type=RemoteType.GIT, url=str(bare_repo), reference="main")

    src = root / "_manifest.json"
    src.write_text("{}")
    push_audit_files(root, _sink(), _solution([remote]), {"_manifest.json": src}, Path("run1"), "actor")

    expected = layout.audit_push_checkout_path(root, "xyz-configuration", "main")
    assert expected.exists()
    assert (expected / ".git").exists()
