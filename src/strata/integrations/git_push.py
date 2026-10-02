#!/usr/bin/env python3
"""Push a single rendered file into a GitOps config repo (docs/design/gitops-integration.md,
Implementation Plan Phase 2 — "The one real missing primitive" — plus Phase 3's
`remove_file()`/`ensure_checkout()`, added for `destroy()`/`plan()`/`output()`).

**Mirrors `controllers/audit_push.py`'s proven clone/fetch/reset/add/commit/push
chain almost exactly** — that module already solved this same problem (a
mutable branch tip that must be fresh before every write, never a pinned read
source like `remote_resolution.resolve_remote()`) for the audit trail's git
sink. Not reused directly: `audit_push.py` lives in `controllers/`, a layer
`ArgoCDIntegration`/`FluxIntegration` (`integrations/`) must never import
(ADR-0003 — a lower layer cannot import a higher one). A second, independent
implementation at this layer is the correct outcome of that constraint, not
duplication by oversight.

Differences from `audit_push.py`, each a direct consequence of the GitOps use
case rather than an arbitrary choice:

- Pushes exactly **one** file (`ProvisionerGitOpsModel.output_file`), not a
  `dict` of many files under a shared subdirectory — so the write step is a
  single copy, not a directory walk.
- The checkout lives under its own `gitops-push/` subdirectory
  (`layout.gitops_push_checkout_path()`) — never `audit-push/`'s or
  `remote_checkout_path()`'s, so the three checkout populations of the same
  remote can never collide.
- The commit message is caller-supplied (the GitOps Integration classes know
  which provisioner/stage triggered the push; this module does not), rather
  than a hardcoded template.
- No remote-name lookup against `solution.spec.remotes` happens here —
  `push_file()` takes an already-resolved `SolutionRemoteModel`, the same
  split `remote_resolution._git_clone(remote: SolutionRemoteModel, ...)`
  already draws between "resolve a name" (a controller/service concern) and
  "act on an already-resolved remote" (this layer's concern).

**Real credentials (Phase 4)** — `prepare_git_credentials()` turns a
resolved `AuthenticationModel` (`method: ssh_key`/`token`) into `env`
overrides, used identically by `push_file()`/`remove_file()`/
`ensure_checkout()` here and by `remote_resolution._git_clone()`
(`controllers/`, which may import this `integrations/`-layer module —
ADR-0003 allows a higher layer importing a lower one). `method: cli`/
`None`/unset stays today's ambient-only behaviour (`{}`), unchanged.
"""

import os
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from strata.integrations.errors import IntegrationError
from strata.models.auth_models import AuthenticationModel
from strata.models.solution_model import RemoteType, SolutionRemoteModel
from strata.utils import layout
from strata.utils.actor import resolve_actor
from strata.utils.transport import run_command

_GIT_TIMEOUT = 300


@dataclass(frozen=True)
class PushResult:
    """The outcome of a push attempt. Never raised for an ordinary git failure
    — a caller (the GitOps Integration classes, Phase 3) decides whether a
    failed push fails the deploy outright, matching `audit_push.PushResult`'s
    own reasoning."""

    success: bool
    detail: str = ""


#: Env var name the generated `GIT_ASKPASS` helper script reads the resolved
#: token from — never passed as an argument (git invokes the script with a
#: prompt string as its only arg), never logged.
_TOKEN_ENV_VAR = "STRATA_GIT_ASKPASS_TOKEN"
#: Set alongside `_TOKEN_ENV_VAR` only when `TokenAuthenticationModel.username`
#: is given — the askpass script answers a "Username for..." prompt with
#: this value instead of the token itself (found missing on code review,
#: 2026-10-02: `username` was modelled but never actually consulted).
_USERNAME_ENV_VAR = "STRATA_GIT_ASKPASS_USERNAME"
#: Read by the `SSH_ASKPASS` helper for an encrypted private key's passphrase
#: (found missing on code review, 2026-10-02: `SSHKeyAuthenticationModel.
#: passphrase` was modelled but never actually consulted).
_PASSPHRASE_ENV_VAR = "STRATA_SSH_ASKPASS_PASSPHRASE"


def prepare_git_credentials(
    auth: AuthenticationModel | None, resolved_values: Mapping[str, str] | None = None
) -> tuple[dict[str, str], Callable[[], None]]:
    """Turn a resolved `AuthenticationModel` into `env` overrides for the git
    subprocess calls in this module (and `remote_resolution._git_clone()`) —
    docs/design/gitops-integration.md's "Credential delivery mechanics".

    Never embeds a secret in `argv` (`ps`/Task Manager-visible, the exact
    leak class `run_command()`'s own docstring already warns about) — both
    real methods below go through `env=` only, same as every other
    integration's secret delivery.

    Args:
        auth: `remote.integration`'s resolved `AuthenticationModel`, or
            `None`. Only `method: ssh_key`/`token` produce any override;
            every other method (including `cli`, and `None`) returns `({},
            no-op)` — today's actual ambient behaviour, unchanged.
        resolved_values: Fully-resolved deploy-time values, keyed by
            declaration name — resolves `auth`'s key-reference fields
            (`ssh_key.private_key`, `token.token`, ...) to their real value.
            Required whenever `auth.method` is `ssh_key`/`token`.

    Returns:
        `(env, cleanup)` — `env` to merge into every git subprocess call;
        `cleanup` removes any temp file created (a private key, a known_hosts
        pin, the askpass helper script) and must be called exactly once,
        always, even on failure — callers use it as a `with`-block
        (`_credential_env()` below).

    Raises:
        IntegrationError: `auth.method` is `ssh_key`/`token` but its key
            reference doesn't resolve against `resolved_values` — a
            misconfigured remote should fail loudly, not silently fall back
            to ambient auth (which would likely fail anyway, confusingly).
    """
    if auth is None or auth.method not in ("ssh_key", "token"):
        return {}, lambda: None

    values = resolved_values or {}

    if auth.method == "ssh_key":
        assert auth.ssh_key is not None  # guaranteed by AuthenticationModel's own validator
        private_key = values.get(auth.ssh_key.private_key)
        if private_key is None:
            raise IntegrationError(
                f"SSH key reference '{auth.ssh_key.private_key}' did not resolve to a value — is it declared?"
            )
        # Resolve (but don't yet act on) the passphrase *before* touching the
        # filesystem — found on review (2026-10-02): raising after
        # `tempfile.mkdtemp()`/writing the key would leak that tempdir (with
        # the private key still in it) forever, since a raise here means
        # this function never returns a `cleanup` callback for anyone to
        # call. Every raise in this function must happen before any
        # filesystem side effect, no exceptions.
        passphrase_ref = auth.ssh_key.passphrase
        passphrase_value: str | None = None
        if passphrase_ref:
            passphrase_value = values.get(passphrase_ref)
            if passphrase_value is None:
                raise IntegrationError(
                    f"SSH passphrase reference '{passphrase_ref}' did not resolve to a value — is it declared?"
                )

        tmp_dir = Path(tempfile.mkdtemp(prefix="strata-gitops-ssh-"))
        key_path = tmp_dir / "key"
        key_path.write_text(private_key)
        key_path.chmod(0o600)

        env: dict[str, str] = {}
        if passphrase_value is not None:
            # SSH_ASKPASS_REQUIRE=force (OpenSSH 8.4+) makes ssh use
            # SSH_ASKPASS unconditionally — without it, ssh only consults
            # SSH_ASKPASS when it detects no controlling terminal, which is
            # not a safe assumption to rely on for every real caller.
            askpass_path = _write_askpass_script(tmp_dir, _PASSPHRASE_ENV_VAR)
            env["SSH_ASKPASS"] = str(askpass_path)
            env["SSH_ASKPASS_REQUIRE"] = "force"
            env[_PASSPHRASE_ENV_VAR] = passphrase_value

        ssh_command = f'ssh -i "{key_path}" -o IdentitiesOnly=yes'
        known_hosts_ref = auth.ssh_key.known_hosts
        known_hosts_value = values.get(known_hosts_ref) if known_hosts_ref else None
        if known_hosts_value is not None:
            known_hosts_path = tmp_dir / "known_hosts"
            known_hosts_path.write_text(known_hosts_value)
            ssh_command += f' -o UserKnownHostsFile="{known_hosts_path}"'
        else:
            # No pinned host key supplied — accept-and-trust-on-first-use
            # rather than hang on an interactive prompt (this always runs
            # non-interactively) or fail outright for an otherwise-valid key.
            ssh_command += " -o StrictHostKeyChecking=accept-new"
        env["GIT_SSH_COMMAND"] = ssh_command

        return env, lambda: shutil.rmtree(tmp_dir, ignore_errors=True)

    assert auth.token is not None  # guaranteed by AuthenticationModel's own validator
    token_value = values.get(auth.token.token)
    if token_value is None:
        raise IntegrationError(f"Token reference '{auth.token.token}' did not resolve to a value — is it declared?")
    tmp_dir = Path(tempfile.mkdtemp(prefix="strata-gitops-token-"))
    env = {_TOKEN_ENV_VAR: token_value}
    username_env_var: str | None = None
    if auth.token.username:
        # `username` is a *literal* string, not a key reference (its own
        # docstring) — used as-is, nothing to resolve.
        username_env_var = _USERNAME_ENV_VAR
        env[_USERNAME_ENV_VAR] = auth.token.username
    askpass_path = _write_askpass_script(tmp_dir, _TOKEN_ENV_VAR, username_env_var=username_env_var)
    env["GIT_ASKPASS"] = str(askpass_path)
    return env, lambda: shutil.rmtree(tmp_dir, ignore_errors=True)


def _write_askpass_script(tmp_dir: Path, primary_env_var: str, *, username_env_var: str | None = None) -> Path:
    """A tiny helper script `GIT_ASKPASS`/`SSH_ASKPASS` invokes to obtain a
    credential — echoes `primary_env_var`'s value to stdout, ignoring its own
    argument (git/ssh call it with a human-readable prompt string). When
    `username_env_var` is given (the `token` method's optional `username`),
    the script instead inspects that prompt: a "Username for..." prompt gets
    `username_env_var`'s value, anything else (the password/token prompt)
    still gets `primary_env_var`'s — found missing entirely on code review,
    2026-10-02 (every prompt used to get `primary_env_var`'s value,
    regardless of which credential component git was actually asking for).

    One real script per platform, since `GIT_ASKPASS`/`SSH_ASKPASS` must be
    directly executable and Windows has no shebang support. Windows gets a
    small `.ps1` (PowerShell can branch on the prompt/read an env var without
    `cmd.exe`'s own command-line metacharacter parsing risk — found on
    review that `@echo %VAR%`, the original approach, would mis-parse a
    credential value containing `&`/`|`/`<`/`>`/`^`) wrapped by a `.bat`
    launcher (the value `GIT_ASKPASS`/`SSH_ASKPASS` actually point at).
    """
    if os.name == "nt":
        return _write_windows_askpass(tmp_dir, primary_env_var, username_env_var)
    return _write_posix_askpass(tmp_dir, primary_env_var, username_env_var)


def _write_posix_askpass(tmp_dir: Path, primary_env_var: str, username_env_var: str | None) -> Path:
    script_path = tmp_dir / "askpass.sh"
    if username_env_var is not None:
        # `${username_env_var:-$primary_env_var}` — the shell's own
        # default-value expansion: falls back to the primary value if the
        # username var happens to be unset/empty, so this one script
        # correctly answers either prompt even if a caller only ever sets
        # `primary_env_var`.
        body = (
            'case "$1" in\n'
            f'  *sername*) echo "${{{username_env_var}:-${primary_env_var}}}" ;;\n'
            f'  *) echo "${primary_env_var}" ;;\n'
            "esac\n"
        )
    else:
        body = f'echo "${primary_env_var}"\n'
    script_path.write_text(f"#!/bin/sh\n{body}")
    script_path.chmod(0o700)
    return script_path


def _write_windows_askpass(tmp_dir: Path, primary_env_var: str, username_env_var: str | None) -> Path:
    ps1_path = tmp_dir / "askpass.ps1"
    if username_env_var is not None:
        ps1_body = (
            "param([string]$Prompt)\n"
            f'if ($Prompt -match "sername" -and $env:{username_env_var}) {{\n'
            f"    [Console]::Out.Write($env:{username_env_var})\n"
            "} else {\n"
            f"    [Console]::Out.Write($env:{primary_env_var})\n"
            "}\n"
        )
    else:
        ps1_body = f"[Console]::Out.Write($env:{primary_env_var})\n"
    ps1_path.write_text(ps1_body)

    # `GIT_ASKPASS`/`SSH_ASKPASS` must be a directly executable path, not a
    # command line — a thin `.bat` launcher is the actual value those env
    # vars point at; it forwards git/ssh's own prompt argument (`%*`)
    # through to the real logic in the `.ps1` above.
    bat_path = tmp_dir / "askpass.bat"
    bat_path.write_text(
        f'@echo off\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{ps1_path}" %*\r\n'
    )
    return bat_path


@contextmanager
def credential_env(
    auth: AuthenticationModel | None, resolved_values: Mapping[str, str] | None, caller_env: Mapping[str, str] | None
) -> Iterator[dict[str, str]]:
    """`with`-scoped merge of `caller_env` with `prepare_git_credentials()`'s
    output — guarantees `cleanup()` always runs, success or failure, without
    every one of `push_file()`/`remove_file()`/`ensure_checkout()` repeating
    its own try/finally."""
    cred_env, cleanup = prepare_git_credentials(auth, resolved_values)
    try:
        yield {**(caller_env or {}), **cred_env}
    finally:
        cleanup()


def push_file(
    root: Path,
    remote: SolutionRemoteModel,
    rendered_file: Path,
    output_file: str,
    message: str,
    *,
    env: Mapping[str, str] | None = None,
    auth: AuthenticationModel | None = None,
    resolved_values: Mapping[str, str] | None = None,
) -> PushResult:
    """Push `rendered_file` into `remote`'s repo at `output_file`, on branch
    `remote.reference`.

    Ensures the local checkout exists and is fresh (clone if absent, then
    always fetch + reset to `origin/<remote.reference>`) before writing —
    never trusts a stale local checkout the way a read-only resolution would.

    Args:
        root: Solution root — used only to key the checkout path
            (`layout.gitops_push_checkout_path()`).
        remote: The already-resolved GitOps config repo. Must be `type: git`;
            `remote.reference` is the branch to push to (required for git
            remotes already, by `SolutionRemoteModel.validate_reference_for_type()`).
        rendered_file: The file `build run` already rendered — copied in
            verbatim, never re-rendered here.
        output_file: Path, relative to the repo root, to write `rendered_file`
            to (`ProvisionerGitOpsModel.output_file`).
        message: Commit message. Caller's responsibility — this module has no
            notion of which provisioner/stage triggered the push.
        env: Variables to add to/override in the environment of every git
            subprocess call. `None`/`{}` for today's ambient-auth-only
            behaviour (ssh-agent, credential helper).
        auth: `remote.integration`'s resolved `AuthenticationModel`, when the
            remote declares one with a git-native `method` (`ssh_key`/
            `token`) — turned into additional `env` overrides via
            `prepare_git_credentials()`. `None` (default) preserves today's
            ambient-only behaviour exactly.
        resolved_values: Fully-resolved deploy-time values, keyed by
            declaration name — resolves `auth`'s key references (e.g.
            `auth.ssh_key.private_key`) to their real secret value. Required
            whenever `auth` names a `ssh_key`/`token` method; ignored
            otherwise.

    Returns:
        Never raises. `success=False` with a human-readable `detail` for any
        git failure (unreachable remote, a non-git remote type, a failed
        clone/fetch/reset/add/commit/push).
    """
    invalid = _validate_remote(remote)
    if invalid is not None:
        return invalid
    branch = remote.reference
    assert branch is not None  # guaranteed by _validate_remote()
    checkout_path = layout.gitops_push_checkout_path(root, remote.name, branch)

    with credential_env(auth, resolved_values, env) as merged_env:
        ensured = _ensure_checkout(remote.url, branch, checkout_path, env=merged_env)
        if not ensured.success:
            return ensured

        configured = _configure_identity(checkout_path, resolve_actor(), env=merged_env)
        if not configured.success:
            return configured

        dest_path = checkout_path / output_file
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(rendered_file.read_bytes())

        add_result = run_command(
            ["git", "add", "--", output_file], cwd=checkout_path, env=merged_env, timeout=_GIT_TIMEOUT
        )
        if not add_result.is_successful:
            return PushResult(False, f"'git add' failed: {add_result.stderr.strip()}")

        return _commit_and_push(checkout_path, branch, message, output_file, env=merged_env)


def remove_file(
    root: Path,
    remote: SolutionRemoteModel,
    output_file: str,
    message: str,
    *,
    env: Mapping[str, str] | None = None,
    auth: AuthenticationModel | None = None,
    resolved_values: Mapping[str, str] | None = None,
) -> PushResult:
    """Remove `output_file` from `remote`'s repo and push the removal —
    `BaseGitOpsIntegration.destroy()`'s primitive (docs/design/
    gitops-integration.md Implementation Plan Phase 3), the mirror image of
    `push_file()`. Same freshness/identity/never-raises guarantees, and the
    same `auth`/`resolved_values` credential wiring (Phase 4).

    Returns:
        `success=True` with detail `"nothing to remove — file already
        absent"` when `output_file` wasn't present (not an error — matches
        `push_file()`'s own "nothing to commit" no-op shape).
    """
    invalid = _validate_remote(remote)
    if invalid is not None:
        return invalid
    branch = remote.reference
    assert branch is not None  # guaranteed by _validate_remote()
    checkout_path = layout.gitops_push_checkout_path(root, remote.name, branch)

    with credential_env(auth, resolved_values, env) as merged_env:
        ensured = _ensure_checkout(remote.url, branch, checkout_path, env=merged_env)
        if not ensured.success:
            return ensured

        configured = _configure_identity(checkout_path, resolve_actor(), env=merged_env)
        if not configured.success:
            return configured

        rm_result = run_command(
            ["git", "rm", "--ignore-unmatch", "--", output_file],
            cwd=checkout_path,
            env=merged_env,
            timeout=_GIT_TIMEOUT,
        )
        if not rm_result.is_successful:
            return PushResult(False, f"'git rm' failed: {rm_result.stderr.strip()}")

        return _commit_and_push(
            checkout_path,
            branch,
            message,
            output_file,
            env=merged_env,
            noop_detail="nothing to remove — file already absent",
        )


def ensure_checkout(
    root: Path,
    remote: SolutionRemoteModel,
    *,
    env: Mapping[str, str] | None = None,
    auth: AuthenticationModel | None = None,
    resolved_values: Mapping[str, str] | None = None,
) -> PushResult:
    """Ensure a fresh, read-only-use checkout of `remote` exists at its
    dedicated push-checkout path, without writing anything — `plan()`'s
    (and `output()`'s) primitive, exposed separately from `push_file()` so a
    preview/read can get a fresh checkout without a push.

    Same clone-if-absent/fetch/reset guarantee `push_file()`/`remove_file()`
    get internally, and the same `auth`/`resolved_values` credential wiring
    (Phase 4).
    """
    invalid = _validate_remote(remote)
    if invalid is not None:
        return invalid
    branch = remote.reference
    assert branch is not None  # guaranteed by _validate_remote()
    checkout_path = layout.gitops_push_checkout_path(root, remote.name, branch)
    with credential_env(auth, resolved_values, env) as merged_env:
        return _ensure_checkout(remote.url, branch, checkout_path, env=merged_env)


def _validate_remote(remote: SolutionRemoteModel) -> PushResult | None:
    """`None` when `remote` is push-able; a failed `PushResult` naming why otherwise."""
    if remote.type != RemoteType.GIT:
        return PushResult(
            False, f"remote '{remote.name}' has type '{remote.type.value}', but a GitOps push needs 'git'"
        )
    if remote.reference is None:
        # Unreachable in practice — git remotes already require `reference`
        # (`SolutionRemoteModel.validate_reference_for_type()`) — guarded here
        # anyway so a future schema change fails loudly, not with a confusing
        # git error three steps down.
        return PushResult(False, f"remote '{remote.name}' has no reference (branch) to push to")
    return None


def _commit_and_push(
    checkout_path: Path,
    branch: str,
    message: str,
    changed_path: str,
    *,
    env: Mapping[str, str] | None,
    noop_detail: str = "nothing to commit — file already matches the last pushed copy",
) -> PushResult:
    """Shared tail of `push_file()`/`remove_file()`: status-check (short-circuits
    as a no-op when nothing actually changed), commit, push."""
    status_result = run_command(
        ["git", "status", "--porcelain", "--", changed_path], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT
    )
    if not status_result.stdout.strip():
        return PushResult(True, noop_detail)

    commit_result = run_command(["git", "commit", "-m", message], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT)
    if not commit_result.is_successful:
        return PushResult(False, f"'git commit' failed: {commit_result.stderr.strip()}")

    push_result = run_command(
        ["git", "push", "origin", f"HEAD:{branch}"], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT
    )
    if not push_result.is_successful:
        return PushResult(False, f"'git push' failed: {push_result.stderr.strip()}")

    return PushResult(True)


def _ensure_checkout(url: str, branch: str, checkout_path: Path, *, env: Mapping[str, str] | None) -> PushResult:
    """Clone if absent, then always fetch + reset to a known-good remote ref —
    `origin/<branch>` when it already exists, else the remote's default branch.

    Identical reasoning to `audit_push._ensure_checkout()`: never leaves stale
    local-only commits from a previous failed attempt sitting on HEAD.
    """
    if not checkout_path.exists():
        checkout_path.parent.mkdir(parents=True, exist_ok=True)
        clone_result = run_command(["git", "clone", url, str(checkout_path)], env=env, timeout=_GIT_TIMEOUT)
        if not clone_result.is_successful:
            return PushResult(False, f"'git clone {url}' failed: {clone_result.stderr.strip()}")

    fetch_result = run_command(["git", "fetch", "origin"], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT)
    if not fetch_result.is_successful:
        return PushResult(False, f"'git fetch origin' failed: {fetch_result.stderr.strip()}")

    reset_ref = f"origin/{branch}"
    verify_result = run_command(
        ["git", "rev-parse", "--verify", reset_ref], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT
    )
    if not verify_result.is_successful:
        # branch doesn't exist upstream yet — fall back to the remote's default branch
        default_verify = run_command(
            ["git", "rev-parse", "--verify", "origin/HEAD"], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT
        )
        if not default_verify.is_successful:
            # a brand-new, completely empty remote (no commits at all) — nothing to
            # reset to; the fresh clone/existing checkout is already the correct
            # starting state.
            return PushResult(True)
        reset_ref = "origin/HEAD"

    reset_result = run_command(["git", "reset", "--hard", reset_ref], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT)
    if not reset_result.is_successful:
        return PushResult(False, f"'git reset --hard {reset_ref}' failed: {reset_result.stderr.strip()}")
    return PushResult(True)


def _configure_identity(checkout_path: Path, actor: str, *, env: Mapping[str, str] | None) -> PushResult:
    """Set a local (not global) git identity so `git commit` never fails on a
    CI runner with no git identity configured — `actor` is `resolve_actor()`'s
    output, so the commit author reflects who actually triggered the run."""
    for key, value in (("user.name", actor), ("user.email", f"{actor}@strata-gitops.local")):
        result = run_command(["git", "config", key, value], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT)
        if not result.is_successful:
            return PushResult(False, f"'git config {key}' failed: {result.stderr.strip()}")
    return PushResult(True)
