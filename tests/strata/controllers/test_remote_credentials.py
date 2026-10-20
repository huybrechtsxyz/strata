#!/usr/bin/env python3
"""Tests for `RemoteCredentialResolver` (docs/work/remotes-at-scale.md, C5)."""

from pathlib import Path

import pytest

from strata.controllers import remote_credentials as remote_credentials_module
from strata.controllers.remote_credentials import (
    AMBIENT,
    FetchCredentials,
    RemoteCredentialError,
    RemoteCredentialResolver,
)
from strata.controllers.solution_context import open_solution
from strata.integrations.capabilities import StoreIntegration

MANIFEST = "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec: {}\n"

TOKEN_AUTH = (
    "  authentication:\n    method: token\n    token:\n      token: CUSTOMER_GIT_TOKEN\n      username: build-bot\n"
)
SSH_AUTH = (
    "  authentication:\n    method: ssh_key\n    ssh_key:\n      private_key: GIT_SSH_KEY\n"
    "      passphrase: GIT_SSH_PASSPHRASE\n      known_hosts: GIT_KNOWN_HOSTS\n"
)
OWNER = "asset 'c0062-api'"
TOKEN = "ghp_exampletoken123"


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _integration(
    root: Path, name: str = "customer-git", *, capabilities: str = "[sources]", enabled: bool = True, auth: str = ""
) -> None:
    _write(
        root,
        f"integrations/{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: {name}\nspec:\n"
        f"  type: git\n  capabilities: {capabilities}\n  enabled: {str(enabled).lower()}\n{auth}",
    )


def _environment(root: Path, secrets: dict[str, tuple[str, str]]) -> None:
    lines = "".join(
        f"    - key: {key}\n      store: {store}\n      value: {value}\n" for key, (store, value) in secrets.items()
    )
    _write(
        root,
        "environments/prd.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n  secrets:\n{lines}"
        if lines
        else "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )


def _resolver(
    tmp_path: Path, *, secrets: dict[str, tuple[str, str]] | None = None, allow_stores: bool = False
) -> RemoteCredentialResolver:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _environment(root, secrets or {})
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  partial: true\n  environments:\n    - prd\n",
    )
    return RemoteCredentialResolver(_context(root), "app", allow_stores=allow_stores)


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


def _with_integration(resolver: RemoteCredentialResolver, tmp_path: Path, **kwargs) -> RemoteCredentialResolver:
    """Add an integration document, then rebuild the resolver over the updated solution."""
    root = tmp_path / "sln"
    _integration(root, **kwargs)
    return RemoteCredentialResolver(_context(root), "app", allow_stores=resolver._allow_stores)


def _token_resolver(tmp_path: Path, monkeypatch, *, token: str = TOKEN, **kwargs) -> RemoteCredentialResolver:
    monkeypatch.setenv("CUSTOMER_REPOS_PAT", token)
    resolver = _resolver(tmp_path, secrets={"CUSTOMER_GIT_TOKEN": ("environment", "CUSTOMER_REPOS_PAT")}, **kwargs)
    return _with_integration(resolver, tmp_path, auth=TOKEN_AUTH)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_token_integration_resolves_from_an_environment_store(tmp_path, monkeypatch):
    resolver = _token_resolver(tmp_path, monkeypatch)

    credentials = resolver.for_integration("customer-git", owner=OWNER)

    assert credentials.auth is not None and credentials.auth.method == "token"
    assert credentials.values == {"CUSTOMER_GIT_TOKEN": TOKEN}


def test_ssh_key_integration_resolves_every_referenced_key(tmp_path, monkeypatch):
    for variable, value in (("PEM", "key-material"), ("PASS", "passphrase-value"), ("HOSTS", "host ssh-ed25519 AAAA")):
        monkeypatch.setenv(f"TEST_GIT_{variable}", value)
    resolver = _resolver(
        tmp_path,
        secrets={
            "GIT_SSH_KEY": ("environment", "TEST_GIT_PEM"),
            "GIT_SSH_PASSPHRASE": ("environment", "TEST_GIT_PASS"),
            "GIT_KNOWN_HOSTS": ("environment", "TEST_GIT_HOSTS"),
        },
    )
    resolver = _with_integration(resolver, tmp_path, name="customer-ssh", auth=SSH_AUTH)

    credentials = resolver.for_integration("customer-ssh", owner=OWNER)

    assert credentials.values == {
        "GIT_SSH_KEY": "key-material",
        "GIT_SSH_PASSPHRASE": "passphrase-value",
        "GIT_KNOWN_HOSTS": "host ssh-ed25519 AAAA",
    }


def test_no_integration_name_is_ambient_and_resolves_nothing(tmp_path, monkeypatch):
    resolver = _resolver(tmp_path)

    def _fail(*args, **kwargs):
        raise AssertionError("nothing should be resolved")

    monkeypatch.setattr(remote_credentials_module, "resolve_values", _fail)
    assert resolver.for_integration(None, owner=OWNER) is AMBIENT
    assert AMBIENT == FetchCredentials(auth=None, values=None)


@pytest.mark.parametrize("auth", ["", "  authentication:\n    method: cli\n    cli: {}\n"])
def test_integration_with_no_authentication_or_cli_is_ambient(tmp_path, auth):
    resolver = _with_integration(_resolver(tmp_path), tmp_path, auth=auth)
    assert resolver.for_integration("customer-git", owner=OWNER) is AMBIENT


def test_the_same_integration_is_resolved_once_per_build(tmp_path, monkeypatch):
    resolver = _token_resolver(tmp_path, monkeypatch)
    calls: list[list[str]] = []
    real = remote_credentials_module.resolve_values

    def _counting(context, deployment_name, keys, **kwargs):
        calls.append(list(keys))
        return real(context, deployment_name, keys, **kwargs)

    monkeypatch.setattr(remote_credentials_module, "resolve_values", _counting)

    first = resolver.for_integration("customer-git", owner="asset 'a'")
    second = resolver.for_integration("customer-git", owner="asset 'b'")

    assert first is second
    assert calls == [["CUSTOMER_GIT_TOKEN"]]  # only the keys the authentication references


# ---------------------------------------------------------------------------
# Strict integration lookup
# ---------------------------------------------------------------------------


def test_unknown_integration_suggests_the_closest_name(tmp_path):
    resolver = _with_integration(_resolver(tmp_path), tmp_path, auth=TOKEN_AUTH)
    with pytest.raises(
        RemoteCredentialError, match=r"integration 'customer-gti' not found \(did you mean 'customer-git'"
    ):
        resolver.for_integration("customer-gti", owner=OWNER)


def test_unknown_integration_without_a_close_name_has_no_hint(tmp_path):
    with pytest.raises(RemoteCredentialError, match="integration 'zzz' not found$"):
        _resolver(tmp_path).for_integration("zzz", owner=OWNER)


def test_disabled_integration_is_an_error(tmp_path):
    resolver = _with_integration(_resolver(tmp_path), tmp_path, enabled=False, auth=TOKEN_AUTH)
    with pytest.raises(RemoteCredentialError, match="is disabled"):
        resolver.for_integration("customer-git", owner=OWNER)


def test_integration_without_the_sources_capability_is_an_error(tmp_path):
    resolver = _with_integration(_resolver(tmp_path), tmp_path, capabilities="[secrets]", auth=TOKEN_AUTH)
    with pytest.raises(RemoteCredentialError, match=r"does not declare the 'sources' capability \(declares: secrets\)"):
        resolver.for_integration("customer-git", owner=OWNER)


def test_authentication_method_that_cannot_authenticate_git_is_an_error(tmp_path):
    auth = "  authentication:\n    method: oauth2\n    oauth2:\n      client_id: A\n      client_secret: B\n"
    resolver = _with_integration(_resolver(tmp_path), tmp_path, auth=auth)
    with pytest.raises(RemoteCredentialError, match=r"method 'oauth2'.*supported: token, ssh_key, cli"):
        resolver.for_integration("customer-git", owner=OWNER)


# ---------------------------------------------------------------------------
# Keys: declared, readable, resolvable
# ---------------------------------------------------------------------------


def test_undeclared_credential_key_names_the_key_and_the_deployment(tmp_path):
    resolver = _with_integration(_resolver(tmp_path), tmp_path, auth=TOKEN_AUTH)
    with pytest.raises(RemoteCredentialError, match="'CUSTOMER_GIT_TOKEN'.*not declared.*deployment 'app'"):
        resolver.for_integration("customer-git", owner=OWNER)


def test_unset_environment_variable_is_reported_with_its_name(tmp_path, monkeypatch):
    monkeypatch.delenv("CUSTOMER_REPOS_PAT", raising=False)
    resolver = _resolver(tmp_path, secrets={"CUSTOMER_GIT_TOKEN": ("environment", "CUSTOMER_REPOS_PAT")})
    resolver = _with_integration(resolver, tmp_path, auth=TOKEN_AUTH)
    with pytest.raises(RemoteCredentialError, match="CUSTOMER_REPOS_PAT.*not set"):
        resolver.for_integration("customer-git", owner=OWNER)


def test_declared_known_hosts_that_does_not_resolve_is_an_error_not_trust_on_first_use(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_GIT_PEM", "key-material")
    monkeypatch.setenv("TEST_GIT_PASS", "passphrase-value")
    monkeypatch.delenv("TEST_GIT_HOSTS", raising=False)
    resolver = _resolver(
        tmp_path,
        secrets={
            "GIT_SSH_KEY": ("environment", "TEST_GIT_PEM"),
            "GIT_SSH_PASSPHRASE": ("environment", "TEST_GIT_PASS"),
            "GIT_KNOWN_HOSTS": ("environment", "TEST_GIT_HOSTS"),
        },
    )
    resolver = _with_integration(resolver, tmp_path, name="customer-ssh", auth=SSH_AUTH)
    with pytest.raises(RemoteCredentialError, match="TEST_GIT_HOSTS"):
        resolver.for_integration("customer-ssh", owner=OWNER)


class _FakeStore(StoreIntegration):
    TYPE = "infisical"
    CAPABILITIES = frozenset({"secrets"})
    TRANSPORTS: frozenset[str] = frozenset()

    def resolve(self, key: str) -> str:
        return f"vault-value-for-{key}"


def _vault_backed(tmp_path, monkeypatch, *, allow_stores: bool) -> RemoteCredentialResolver:
    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration", lambda integration_type, config=None: _FakeStore(config)
    )
    resolver = _resolver(
        tmp_path, secrets={"CUSTOMER_GIT_TOKEN": ("infisical", "customer-git-token")}, allow_stores=allow_stores
    )
    return _with_integration(resolver, tmp_path, auth=TOKEN_AUTH)


def test_integration_backed_credential_needs_resolve_for_a_plain_build(tmp_path, monkeypatch):
    resolver = _vault_backed(tmp_path, monkeypatch, allow_stores=False)
    with pytest.raises(RemoteCredentialError, match=r"stored in 'infisical'.*unless you pass --resolve"):
        resolver.for_integration("customer-git", owner=OWNER)


def test_integration_backed_credential_resolves_with_resolve(tmp_path, monkeypatch):
    resolver = _vault_backed(tmp_path, monkeypatch, allow_stores=True)
    credentials = resolver.for_integration("customer-git", owner=OWNER)
    assert credentials.values == {"CUSTOMER_GIT_TOKEN": "vault-value-for-customer-git-token"}


def test_an_unrelated_broken_value_reference_does_not_block_the_credentials(tmp_path, monkeypatch):
    """`resolve_values()` always merges every `${value:...}` reference in the solution; only the
    credential keys' own failures may stop a fetch."""
    resolver = _token_resolver(tmp_path, monkeypatch)
    root = tmp_path / "sln"
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n  type: local\n"
        "  region: '${value:provider.ghost.spec.region}'\n",
    )
    resolver = RemoteCredentialResolver(_context(root), "app", allow_stores=False)

    assert resolver.for_integration("customer-git", owner=OWNER).values == {"CUSTOMER_GIT_TOKEN": TOKEN}


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redact_masks_every_resolved_credential_value(tmp_path, monkeypatch):
    resolver = _token_resolver(tmp_path, monkeypatch)
    resolver.for_integration("customer-git", owner=OWNER)

    assert (
        resolver.redact(f"fatal: could not read from https://{TOKEN}@host")
        == "fatal: could not read from https://***@host"
    )


def test_redact_before_anything_is_resolved_changes_nothing(tmp_path):
    assert _resolver(tmp_path).redact("fatal: nothing to hide") == "fatal: nothing to hide"


def test_redact_leaves_a_value_too_short_to_be_a_secret(tmp_path, monkeypatch):
    resolver = _token_resolver(tmp_path, monkeypatch, token="abc")
    resolver.for_integration("customer-git", owner=OWNER)
    assert resolver.redact("abc abc") == "abc abc"
