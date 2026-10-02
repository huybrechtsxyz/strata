#!/usr/bin/env python3
"""Tests for `git_push.push_file` (docs/design/gitops-integration.md,
Implementation Plan Phase 2).

Same two-tier style as `test_audit_push.py`: mocked `run_command` for the
early-exit/validation branches, plus one true end-to-end test driving a real
local bare git repository to confirm the whole clone/fetch/reset/add/commit/
push chain actually works, not just that the right subprocess args were
assembled.
"""

import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from strata.integrations import git_push as git_push_module
from strata.integrations.errors import IntegrationError
from strata.integrations.git_push import prepare_git_credentials, push_file
from strata.models.auth_models import (
    AuthenticationModel,
    CLIAuthenticationModel,
    SSHKeyAuthenticationModel,
    TokenAuthenticationModel,
)
from strata.models.solution_model import RemoteType, SolutionRemoteModel
from strata.utils import layout
from strata.utils.transport import CommandResult


def _remote(name: str = "gitops-config", url: str = "https://example.com/org/gitops-config.git") -> SolutionRemoteModel:
    return SolutionRemoteModel(name=name, type=RemoteType.GIT, url=url, reference="main")


def _capture(monkeypatch):
    captured: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        captured.append(list(args))
        if args[:2] == ["git", "status"]:
            # Simulate real changes present, so add/commit/push aren't
            # short-circuited by the "nothing to commit" fast path.
            return CommandResult(returncode=0, stdout=" M apps/forge/values.yaml\n", stderr="")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_push_module, "run_command", _fake_run_command)
    return captured


# ---------------------------------------------------------------------------
# Mocked-command tests
# ---------------------------------------------------------------------------


def test_non_git_remote_type_fails_without_running_any_command(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    remote = SolutionRemoteModel(name="gitops-config", type=RemoteType.LOCAL, url=".")
    rendered = tmp_path / "values.yaml"
    rendered.write_text("replicas: 3")
    result = push_file(tmp_path, remote, rendered, "apps/forge/values.yaml", "deploy: forge", env=None)
    assert result.success is False
    assert "needs 'git'" in result.detail
    assert captured == []


def test_missing_reference_fails_without_running_any_command(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    remote = SolutionRemoteModel.model_construct(
        name="gitops-config", type=RemoteType.GIT, url="https://example.com/x.git", reference=None
    )
    rendered = tmp_path / "values.yaml"
    rendered.write_text("replicas: 3")
    result = push_file(tmp_path, remote, rendered, "apps/forge/values.yaml", "deploy: forge")
    assert result.success is False
    assert "no reference" in result.detail
    assert captured == []


def test_fresh_checkout_clones_fetches_resets_and_pushes(tmp_path: Path, monkeypatch):
    captured = _capture(monkeypatch)
    rendered = tmp_path / "values.yaml"
    rendered.write_text("replicas: 3")

    result = push_file(tmp_path, _remote(), rendered, "apps/forge/values.yaml", "deploy: forge")

    assert result.success, result.detail
    commands = [c[:2] for c in captured]
    assert ["git", "clone"] in commands
    assert ["git", "fetch"] in commands
    assert ["git", "reset"] in commands or any(c[:3] == ["git", "reset", "--hard"] for c in captured)
    assert ["git", "add"] in commands
    assert ["git", "commit"] in commands
    assert ["git", "push"] in commands


def test_env_is_threaded_through_every_git_call(tmp_path: Path, monkeypatch):
    seen_envs: list[dict | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        seen_envs.append(env)
        if args[:2] == ["git", "status"]:
            return CommandResult(returncode=0, stdout=" M apps/forge/values.yaml\n", stderr="")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_push_module, "run_command", _fake_run_command)
    rendered = tmp_path / "values.yaml"
    rendered.write_text("replicas: 3")

    result = push_file(
        tmp_path, _remote(), rendered, "apps/forge/values.yaml", "deploy: forge", env={"GIT_ASKPASS": "x"}
    )

    assert result.success, result.detail
    assert seen_envs  # at least one git call happened
    assert all(e == {"GIT_ASKPASS": "x"} for e in seen_envs)


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
    remote = SolutionRemoteModel(name="gitops-config", type=RemoteType.GIT, url=str(bare_repo), reference="main")

    rendered = root / "values.yaml"
    rendered.write_text("replicas: 3\n")

    first = push_file(root, remote, rendered, "apps/forge/values.yaml", "deploy forge: replicas=3")
    assert first.success, first.detail

    verify_dir = tmp_path / "verify"
    subprocess.run(["git", "clone", str(bare_repo), str(verify_dir)], check=True, capture_output=True)
    pushed_file = verify_dir / "apps" / "forge" / "values.yaml"
    assert pushed_file.exists()
    assert pushed_file.read_text() == "replicas: 3\n"

    # Second run — the checkout directory is reused, fetch+reset must handle a
    # branch that now really exists on origin.
    rendered.write_text("replicas: 5\n")
    second = push_file(root, remote, rendered, "apps/forge/values.yaml", "deploy forge: replicas=5")
    assert second.success, second.detail

    subprocess.run(["git", "-C", str(verify_dir), "pull"], check=True, capture_output=True)
    assert pushed_file.read_text() == "replicas: 5\n"


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_real_push_is_a_noop_when_content_is_unchanged(tmp_path: Path):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    root = tmp_path / "solution"
    root.mkdir()
    remote = SolutionRemoteModel(name="gitops-config", type=RemoteType.GIT, url=str(bare_repo), reference="main")

    rendered = root / "values.yaml"
    rendered.write_text("replicas: 3\n")
    first = push_file(root, remote, rendered, "apps/forge/values.yaml", "deploy forge")
    assert first.success, first.detail

    second = push_file(root, remote, rendered, "apps/forge/values.yaml", "deploy forge again")
    assert second.success, second.detail
    assert "nothing to commit" in second.detail


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_real_push_checkout_path_matches_layout(tmp_path: Path):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    root = tmp_path / "solution"
    root.mkdir()
    remote = SolutionRemoteModel(name="gitops-config", type=RemoteType.GIT, url=str(bare_repo), reference="main")

    rendered = root / "values.yaml"
    rendered.write_text("replicas: 3\n")
    push_file(root, remote, rendered, "apps/forge/values.yaml", "deploy forge")

    expected = layout.gitops_push_checkout_path(root, "gitops-config", "main")
    assert expected.exists()
    assert (expected / ".git").exists()


# ---------------------------------------------------------------------------
# prepare_git_credentials() (docs/design/gitops-integration.md Phase 4)
# ---------------------------------------------------------------------------


def test_prepare_git_credentials_none_auth_is_a_noop():
    env, cleanup = prepare_git_credentials(None)
    assert env == {}
    cleanup()  # must not raise


def test_prepare_git_credentials_cli_method_is_a_noop():
    auth = AuthenticationModel(method="cli", cli=CLIAuthenticationModel())
    env, cleanup = prepare_git_credentials(auth)
    assert env == {}
    cleanup()


def test_prepare_git_credentials_ssh_key_writes_keyfile_with_0600_and_cleans_up_after():
    auth = AuthenticationModel(method="ssh_key", ssh_key=SSHKeyAuthenticationModel(private_key="deploy-key-ref"))
    env, cleanup = prepare_git_credentials(auth, {"deploy-key-ref": "-----BEGIN KEY-----\nabc\n-----END KEY-----"})

    assert "GIT_SSH_COMMAND" in env
    match = re.search(r'ssh -i "([^"]+)"', env["GIT_SSH_COMMAND"])
    assert match is not None
    key_path = Path(match.group(1))
    assert key_path.is_file()
    assert key_path.read_text() == "-----BEGIN KEY-----\nabc\n-----END KEY-----"
    if os.name != "nt":
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert "StrictHostKeyChecking=accept-new" in env["GIT_SSH_COMMAND"]

    cleanup()
    assert not key_path.exists()


def test_prepare_git_credentials_ssh_key_with_known_hosts():
    auth = AuthenticationModel(
        method="ssh_key",
        ssh_key=SSHKeyAuthenticationModel(private_key="deploy-key-ref", known_hosts="known-hosts-ref"),
    )
    env, cleanup = prepare_git_credentials(
        auth, {"deploy-key-ref": "key-material", "known-hosts-ref": "github.com ssh-ed25519 AAAA..."}
    )
    assert "UserKnownHostsFile=" in env["GIT_SSH_COMMAND"]
    assert "StrictHostKeyChecking=accept-new" not in env["GIT_SSH_COMMAND"]
    cleanup()


def test_prepare_git_credentials_ssh_key_missing_value_raises():
    auth = AuthenticationModel(method="ssh_key", ssh_key=SSHKeyAuthenticationModel(private_key="deploy-key-ref"))
    with pytest.raises(IntegrationError, match="did not resolve to a value"):
        prepare_git_credentials(auth, {})


def test_prepare_git_credentials_ssh_key_with_passphrase_sets_ssh_askpass(tmp_path: Path):
    """Regression test for the gap found on code review (2026-10-02):
    `SSHKeyAuthenticationModel.passphrase` was modelled but never actually
    consulted — an encrypted private key had no non-interactive way to
    supply it."""
    auth = AuthenticationModel(
        method="ssh_key",
        ssh_key=SSHKeyAuthenticationModel(private_key="deploy-key-ref", passphrase="deploy-key-passphrase-ref"),
    )
    env, cleanup = prepare_git_credentials(
        auth, {"deploy-key-ref": "key-material", "deploy-key-passphrase-ref": "hunter2"}
    )

    assert env.get("SSH_ASKPASS_REQUIRE") == "force"
    assert "SSH_ASKPASS" in env
    script_path = Path(env["SSH_ASKPASS"])
    assert script_path.is_file()
    passphrase_env_var = next(k for k in env if k not in ("GIT_SSH_COMMAND", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE"))
    assert env[passphrase_env_var] == "hunter2"

    # Invoke the generated script exactly as ssh would and confirm it prints
    # the real passphrase back.
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", str(script_path), "Enter passphrase:"],
            env={**os.environ, passphrase_env_var: "hunter2"},
            capture_output=True,
            text=True,
            check=True,
        )
    else:
        result = subprocess.run(
            [str(script_path), "Enter passphrase:"],
            env={**os.environ, passphrase_env_var: "hunter2"},
            capture_output=True,
            text=True,
            check=True,
        )
    assert result.stdout.strip() == "hunter2"

    cleanup()
    assert not script_path.exists()


def test_prepare_git_credentials_ssh_key_passphrase_missing_value_raises():
    auth = AuthenticationModel(
        method="ssh_key",
        ssh_key=SSHKeyAuthenticationModel(private_key="deploy-key-ref", passphrase="missing-ref"),
    )
    with pytest.raises(IntegrationError, match="passphrase reference .* did not resolve"):
        prepare_git_credentials(auth, {"deploy-key-ref": "key-material"})


def test_prepare_git_credentials_ssh_key_passphrase_missing_value_leaves_no_tempdir_behind():
    """Regression test for a bug introduced by the passphrase fix itself,
    found on a follow-up review (2026-10-02): the passphrase check used to
    run *after* `tempfile.mkdtemp()`/writing the private key, so raising
    here would leak that tempdir (with the private key still in it)
    forever — the function never returns a `cleanup` callback for anyone
    to call when it raises. Every raise must happen before any filesystem
    side effect."""
    existing_tmp_dirs = {p for p in Path(tempfile.gettempdir()).iterdir() if p.name.startswith("strata-gitops-ssh-")}

    auth = AuthenticationModel(
        method="ssh_key",
        ssh_key=SSHKeyAuthenticationModel(private_key="deploy-key-ref", passphrase="missing-ref"),
    )
    with pytest.raises(IntegrationError):
        prepare_git_credentials(auth, {"deploy-key-ref": "key-material"})

    new_tmp_dirs = {p for p in Path(tempfile.gettempdir()).iterdir() if p.name.startswith("strata-gitops-ssh-")}
    assert new_tmp_dirs == existing_tmp_dirs, "a raise must never leave a new strata-gitops-ssh- tempdir behind"


def test_prepare_git_credentials_ssh_key_without_passphrase_sets_no_ssh_askpass():
    """Confirms the passphrase wiring is additive — the no-passphrase case
    (already covered above) still sets no SSH_ASKPASS at all."""
    auth = AuthenticationModel(method="ssh_key", ssh_key=SSHKeyAuthenticationModel(private_key="deploy-key-ref"))
    env, cleanup = prepare_git_credentials(auth, {"deploy-key-ref": "key-material"})
    assert "SSH_ASKPASS" not in env
    assert "SSH_ASKPASS_REQUIRE" not in env
    cleanup()


def test_prepare_git_credentials_token_sets_askpass_and_cleans_up_after():
    auth = AuthenticationModel(method="token", token=TokenAuthenticationModel(token="pat-ref"))
    env, cleanup = prepare_git_credentials(auth, {"pat-ref": "ghp_supersecrettoken"})

    assert "GIT_ASKPASS" in env
    script_path = Path(env["GIT_ASKPASS"])
    assert script_path.is_file()
    token_env_var = next(k for k in env if k != "GIT_ASKPASS")
    assert env[token_env_var] == "ghp_supersecrettoken"

    # Invoke the generated script exactly as git would (one positional "prompt"
    # argument, token delivered only via env) and confirm it prints the token.
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", str(script_path), "Password:"],
            env={**os.environ, token_env_var: "ghp_supersecrettoken"},
            capture_output=True,
            text=True,
            check=True,
        )
    else:
        result = subprocess.run(
            [str(script_path), "Password:"],
            env={**os.environ, token_env_var: "ghp_supersecrettoken"},
            capture_output=True,
            text=True,
            check=True,
        )
    assert result.stdout.strip() == "ghp_supersecrettoken"

    cleanup()
    assert not script_path.exists()


def test_prepare_git_credentials_token_missing_value_raises():
    auth = AuthenticationModel(method="token", token=TokenAuthenticationModel(token="pat-ref"))
    with pytest.raises(IntegrationError, match="did not resolve to a value"):
        prepare_git_credentials(auth, {})


def test_prepare_git_credentials_token_with_username_answers_each_prompt_correctly():
    """Regression test for the gap found on code review (2026-10-02):
    `TokenAuthenticationModel.username` was modelled but never actually
    consulted — the askpass script answered *every* prompt (including
    "Username for...") with the token value itself."""
    auth = AuthenticationModel(
        method="token", token=TokenAuthenticationModel(token="pat-ref", username="x-access-token")
    )
    env, cleanup = prepare_git_credentials(auth, {"pat-ref": "ghp_supersecrettoken"})

    script_path = Path(env["GIT_ASKPASS"])

    def _invoke(prompt: str) -> str:
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", str(script_path), prompt],
                env={**os.environ, **{k: v for k, v in env.items() if k not in ("GIT_ASKPASS",)}},
                capture_output=True,
                text=True,
                check=True,
            )
        else:
            result = subprocess.run(
                [str(script_path), prompt],
                env={**os.environ, **{k: v for k, v in env.items() if k not in ("GIT_ASKPASS",)}},
                capture_output=True,
                text=True,
                check=True,
            )
        return result.stdout.strip()

    assert _invoke("Username for 'https://github.com': ") == "x-access-token"
    assert _invoke("Password for 'https://x-access-token@github.com': ") == "ghp_supersecrettoken"

    cleanup()
    assert not script_path.exists()


def test_prepare_git_credentials_token_without_username_falls_back_to_token_for_every_prompt():
    """No `username` configured (today's existing behaviour, unchanged) —
    every prompt, including a "Username" one, still gets the token."""
    auth = AuthenticationModel(method="token", token=TokenAuthenticationModel(token="pat-ref"))
    env, cleanup = prepare_git_credentials(auth, {"pat-ref": "ghp_supersecrettoken"})

    assert "STRATA_GIT_ASKPASS_USERNAME" not in env
    script_path = Path(env["GIT_ASKPASS"])
    token_env_var = next(k for k in env if k != "GIT_ASKPASS")

    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", str(script_path), "Username for 'https://github.com': "],
            env={**os.environ, token_env_var: "ghp_supersecrettoken"},
            capture_output=True,
            text=True,
            check=True,
        )
    else:
        result = subprocess.run(
            [str(script_path), "Username for 'https://github.com': "],
            env={**os.environ, token_env_var: "ghp_supersecrettoken"},
            capture_output=True,
            text=True,
            check=True,
        )
    assert result.stdout.strip() == "ghp_supersecrettoken"

    cleanup()


def test_push_file_threads_ssh_key_credentials_through_every_git_call(tmp_path: Path, monkeypatch):
    seen_envs: list[dict | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        seen_envs.append(dict(env) if env is not None else None)
        if args[:2] == ["git", "status"]:
            return CommandResult(returncode=0, stdout=" M apps/forge/values.yaml\n", stderr="")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_push_module, "run_command", _fake_run_command)
    rendered = tmp_path / "values.yaml"
    rendered.write_text("replicas: 3")
    auth = AuthenticationModel(method="ssh_key", ssh_key=SSHKeyAuthenticationModel(private_key="deploy-key-ref"))

    result = push_file(
        tmp_path,
        _remote(),
        rendered,
        "apps/forge/values.yaml",
        "deploy: forge",
        auth=auth,
        resolved_values={"deploy-key-ref": "key-material"},
    )

    assert result.success, result.detail
    assert seen_envs  # at least one git call happened
    assert all(e is not None and "GIT_SSH_COMMAND" in e for e in seen_envs)
