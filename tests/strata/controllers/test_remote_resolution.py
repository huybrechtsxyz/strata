#!/usr/bin/env python3
"""Tests for `remote_resolution.resolve_remote` (docs/design/remotes.md)."""

from pathlib import Path

import pytest

from strata.controllers import remote_resolution as remote_resolution_module
from strata.controllers.remote_resolution import RemoteResolutionError, resolve_remote
from strata.models.auth_models import AuthenticationModel, SSHKeyAuthenticationModel
from strata.models.solution_model import RemoteFetch, RemoteType, SolutionRemoteModel
from strata.utils import layout
from strata.utils.transport import CommandResult


def _remote(
    name: str = "infra",
    *,
    type: RemoteType = RemoteType.GIT,
    url: str = "https://example.com/org/infra.git",
    reference: str | None = "main",
    fetch: RemoteFetch = RemoteFetch.STRATA,
) -> SolutionRemoteModel:
    return SolutionRemoteModel(name=name, type=type, url=url, reference=reference, fetch=fetch)


def _capture(monkeypatch):
    captured: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        captured.append(list(args))
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_run_command)
    return captured


def test_no_remote_resolves_to_root(tmp_path: Path):
    assert resolve_remote(tmp_path, None) == tmp_path


def test_local_remote_resolves_directly_no_checkout_dir(tmp_path: Path):
    remote = SolutionRemoteModel(name="config", type=RemoteType.LOCAL, url=".")
    assert resolve_remote(tmp_path, remote) == tmp_path.resolve()


def test_local_remote_with_subpath(tmp_path: Path):
    remote = SolutionRemoteModel(name="config", type=RemoteType.LOCAL, url="config")
    assert resolve_remote(tmp_path, remote) == (tmp_path / "config").resolve()


def test_git_remote_already_checked_out_is_reused(tmp_path: Path, monkeypatch):
    """Ref-keyed path means an existing checkout can never be stale — reused
    as-is, no git commands run at all."""
    captured = _capture(monkeypatch)
    remote = _remote()
    checkout_path = layout.remote_checkout_path(tmp_path, remote.name, remote.reference)
    checkout_path.mkdir(parents=True)

    result = resolve_remote(tmp_path, remote)

    assert result == checkout_path
    assert captured == []


def test_git_remote_fetch_external_missing_checkout_raises(tmp_path: Path):
    remote = _remote(fetch=RemoteFetch.EXTERNAL)
    with pytest.raises(RemoteResolutionError, match="fetch: external"):
        resolve_remote(tmp_path, remote)


def test_git_remote_fetch_strata_clones_and_checks_out(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    remote = _remote()

    result = resolve_remote(tmp_path, remote)

    expected_path = layout.remote_checkout_path(tmp_path, remote.name, remote.reference)
    assert result == expected_path
    assert captured[0] == ["git", "clone", remote.url, str(expected_path)]
    assert captured[1] == ["git", "checkout", "main"]


def test_git_clone_failure_raises_with_stderr(tmp_path: Path, monkeypatch):
    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=128, stdout="", stderr="fatal: could not read Username")

    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_run_command)
    remote = _remote()

    with pytest.raises(RemoteResolutionError, match="could not read Username"):
        resolve_remote(tmp_path, remote)


def test_git_checkout_failure_raises_with_stderr(tmp_path: Path, monkeypatch):
    calls = {"n": 0}

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return CommandResult(returncode=0, stdout="", stderr="")
        return CommandResult(returncode=1, stdout="", stderr="error: pathspec 'main' did not match")

    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_run_command)
    remote = _remote()

    with pytest.raises(RemoteResolutionError, match="pathspec"):
        resolve_remote(tmp_path, remote)


def test_unfetchable_type_raises_a_clear_error(tmp_path: Path):
    remote = _remote(type=RemoteType.OCI, url="oci://example.com/chart", reference="v1.0.0")
    with pytest.raises(RemoteResolutionError, match="cannot fetch yet"):
        resolve_remote(tmp_path, remote)


# ---------------------------------------------------------------------------
# Real credentials (docs/design/gitops-integration.md Phase 4) — resolve_remote()/
# _git_clone() thread `auth`/`resolved_values` into every git call via
# `git_push.prepare_git_credentials()`.
# ---------------------------------------------------------------------------


def test_resolve_remote_without_auth_passes_no_credential_env(tmp_path: Path, monkeypatch):
    """Default behaviour (no `auth`) is unchanged — every existing caller."""
    seen_envs: list[dict | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        seen_envs.append(dict(env) if env is not None else None)
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_run_command)
    remote = _remote()

    resolve_remote(tmp_path, remote)

    assert seen_envs
    assert all(e == {} for e in seen_envs)


def test_resolve_remote_with_ssh_key_auth_threads_credentials_through(tmp_path: Path, monkeypatch):
    seen_envs: list[dict | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        seen_envs.append(dict(env) if env is not None else None)
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_run_command)
    remote = _remote()
    auth = AuthenticationModel(method="ssh_key", ssh_key=SSHKeyAuthenticationModel(private_key="deploy-key-ref"))

    resolve_remote(tmp_path, remote, auth=auth, resolved_values={"deploy-key-ref": "key-material"})

    assert seen_envs
    assert all(e is not None and "GIT_SSH_COMMAND" in e for e in seen_envs)


def test_resolve_remote_with_misconfigured_auth_raises_remote_resolution_error_not_a_crash(tmp_path: Path, monkeypatch):
    """Regression test found on follow-up review (2026-10-02): a credential
    reference that doesn't resolve raises `IntegrationError` from
    `prepare_git_credentials()` — a plain `Exception`, not a `StrataError`,
    that must be converted to this module's own `RemoteResolutionError`
    rather than escaping uncaught (same fix applied to `gitops.py`'s
    `plan`/`deploy`/`destroy`/`output`)."""
    captured = _capture(monkeypatch)
    remote = _remote()
    auth = AuthenticationModel(method="ssh_key", ssh_key=SSHKeyAuthenticationModel(private_key="missing-ref"))

    with pytest.raises(RemoteResolutionError, match="did not resolve to a value"):
        resolve_remote(tmp_path, remote, auth=auth, resolved_values={})

    assert captured == []  # fails before any git command ever runs
