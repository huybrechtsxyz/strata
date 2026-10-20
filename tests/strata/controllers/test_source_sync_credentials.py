#!/usr/bin/env python3
"""`source_sync` clone sites ask the credential resolver, lazily (docs/work/remotes-at-scale.md C2)."""

from pathlib import Path

import pytest

from strata.controllers import remote_resolution as remote_resolution_module
from strata.controllers.remote_credentials import AMBIENT, FetchCredentials, RemoteCredentialError
from strata.controllers.remote_resolution import RemoteResolutionError
from strata.controllers.source_sync import sync_asset, sync_module_source, sync_source
from strata.models.asset_model import AssetModel
from strata.models.auth_models import AuthenticationModel, TokenAuthenticationModel
from strata.models.common_models import SourceModel
from strata.models.solution_model import RemoteFetch, RemoteType, SolutionRemoteModel
from strata.utils import layout
from strata.utils.transport import CommandResult

TOKEN = "ghp_secrettoken123"
_AUTH = AuthenticationModel(method="token", token=TokenAuthenticationModel(token="GIT_TOKEN", username="bot"))


class _StubResolver:
    """Stands in for `RemoteCredentialResolver`; records who asked for what."""

    def __init__(self, credentials: FetchCredentials | None = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._credentials = credentials or FetchCredentials(auth=_AUTH, values={"GIT_TOKEN": TOKEN})
        self._error = error

    def for_integration(self, name: str | None, *, owner: str) -> FetchCredentials:
        self.calls.append((str(name), owner))
        if self._error is not None:
            raise self._error
        return self._credentials

    def redact(self, text: str) -> str:
        return text.replace(TOKEN, "***")


def _remote(
    *,
    integration: str | None = "customer-git",
    fetch: RemoteFetch = RemoteFetch.STRATA,
    type_: RemoteType = RemoteType.GIT,
) -> SolutionRemoteModel:
    return SolutionRemoteModel(
        name="infra",
        type=type_,
        url="https://example.com/org/infra.git" if type_ == RemoteType.GIT else "vendor",
        reference="main" if type_ == RemoteType.GIT else None,
        fetch=fetch,
        integration=integration if type_ == RemoteType.GIT else None,  # the model rejects it on `local`
        description=None,
    )


def _fake_git(monkeypatch, *, stderr: str = "", returncode: int = 0) -> list[dict]:
    """Fake `git`: a successful clone creates `infra/main.tf`; records each call's argv and env."""
    calls: list[dict] = []

    def _run(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append({"args": list(args), "env": dict(env or {})})
        if args[:2] == ["git", "clone"] and returncode == 0:
            dest = Path(args[-1])
            (dest / "infra").mkdir(parents=True, exist_ok=True)
            (dest / "infra" / "main.tf").write_text("# root\n", encoding="utf-8")
        return CommandResult(returncode=returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(remote_resolution_module, "run_command", _run)
    return calls


def _source() -> SourceModel:
    return SourceModel(remote="infra", source_path="infra")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "sln"
    root.mkdir()
    return root


def _asset(remote: str | None = None) -> AssetModel:
    spec: dict = {"source_path": "infra"}
    if remote is not None:
        spec["remote"] = remote
    return AssetModel.model_validate({"meta": {"name": "c0062-api"}, "spec": spec})


# ---------------------------------------------------------------------------
# A clone authenticates with the resolved credentials
# ---------------------------------------------------------------------------


def test_sync_source_clone_uses_the_resolved_credentials(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch)
    resolver = _StubResolver()

    sync_source(_root(tmp_path), tmp_path / "build", _source(), {"infra": _remote()}, credentials=resolver)

    assert resolver.calls == [("customer-git", "remote 'infra'")]
    clone = calls[0]
    assert "GIT_ASKPASS" in clone["env"] and clone["env"]["STRATA_GIT_ASKPASS_TOKEN"] == TOKEN
    assert not any(TOKEN in arg for call in calls for arg in call["args"])  # never on a command line
    assert (tmp_path / "build" / "infra" / "main.tf").is_file()


def test_sync_module_source_clone_uses_the_resolved_credentials(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch)
    resolver = _StubResolver()

    sync_module_source(_root(tmp_path), tmp_path / "mod", _source(), {"infra": _remote()}, credentials=resolver)

    assert resolver.calls == [("customer-git", "remote 'infra'")]
    assert calls[0]["env"]["STRATA_GIT_ASKPASS_TOKEN"] == TOKEN


def test_sync_asset_through_a_declared_remote_uses_the_resolved_credentials(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch)
    resolver = _StubResolver()

    sync_asset(
        _root(tmp_path), tmp_path / "build", _asset("infra"), {}, {"infra": _remote()}, slot="api", credentials=resolver
    )

    assert resolver.calls == [("customer-git", "asset 'c0062-api'")]
    assert calls[0]["env"]["STRATA_GIT_ASKPASS_TOKEN"] == TOKEN


def test_sync_asset_location_uses_the_resolved_credentials(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch)
    resolver = _StubResolver()
    asset = AssetModel.model_validate(
        {
            "meta": {"name": "c0062-api"},
            "spec": {
                "source_path": "infra",
                "location": {
                    "url": "https://example.com/org/customer.git",
                    "reference": "v1",
                    "integration": "customer-git",
                },
            },
        }
    )

    sync_asset(_root(tmp_path), tmp_path / "build", asset, {}, {}, slot="api", credentials=resolver)

    assert resolver.calls == [("customer-git", "asset 'c0062-api'")]
    assert calls[0]["env"]["STRATA_GIT_ASKPASS_TOKEN"] == TOKEN


# ---------------------------------------------------------------------------
# Lazy: only a clone that will actually run asks
# ---------------------------------------------------------------------------


def test_an_existing_checkout_resolves_no_credentials(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch)
    root = _root(tmp_path)
    checkout = layout.remote_checkout_path(root, "infra", "main")
    (checkout / "infra").mkdir(parents=True)
    (checkout / "infra" / "main.tf").write_text("# cached\n", encoding="utf-8")
    resolver = _StubResolver()

    sync_source(root, tmp_path / "build", _source(), {"infra": _remote()}, credentials=resolver)

    assert resolver.calls == [] and calls == []


def test_fetch_external_resolves_no_credentials(tmp_path, monkeypatch):
    _fake_git(monkeypatch)
    root = _root(tmp_path)
    checkout = layout.remote_checkout_path(root, "infra", "main")
    (checkout / "infra").mkdir(parents=True)
    (checkout / "infra" / "main.tf").write_text("# placed by CI\n", encoding="utf-8")
    resolver = _StubResolver()

    sync_source(
        root, tmp_path / "build", _source(), {"infra": _remote(fetch=RemoteFetch.EXTERNAL)}, credentials=resolver
    )

    assert resolver.calls == []


def test_a_local_remote_resolves_no_credentials(tmp_path, monkeypatch):
    _fake_git(monkeypatch)
    root = _root(tmp_path)
    (root / "vendor" / "infra").mkdir(parents=True)
    (root / "vendor" / "infra" / "main.tf").write_text("# local\n", encoding="utf-8")
    resolver = _StubResolver()

    sync_source(root, tmp_path / "build", _source(), {"infra": _remote(type_=RemoteType.LOCAL)}, credentials=resolver)

    assert resolver.calls == []


def test_a_remote_without_an_integration_resolves_no_credentials_and_clones_ambient(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch)
    resolver = _StubResolver()

    sync_source(
        _root(tmp_path), tmp_path / "build", _source(), {"infra": _remote(integration=None)}, credentials=resolver
    )

    assert resolver.calls == []
    assert "GIT_ASKPASS" not in calls[0]["env"]


def test_without_a_resolver_the_clone_is_ambient(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch)

    sync_source(_root(tmp_path), tmp_path / "build", _source(), {"infra": _remote()})

    assert "GIT_ASKPASS" not in calls[0]["env"]


def test_an_ambient_integration_clones_without_askpass(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch)
    resolver = _StubResolver(credentials=AMBIENT)

    sync_source(_root(tmp_path), tmp_path / "build", _source(), {"infra": _remote()}, credentials=resolver)

    assert resolver.calls == [("customer-git", "remote 'infra'")]
    assert "GIT_ASKPASS" not in calls[0]["env"]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def test_a_credential_error_stops_the_build_before_any_git_call(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch)
    resolver = _StubResolver(error=RemoteCredentialError("remote 'infra': integration 'customer-git' is disabled"))

    with pytest.raises(RemoteCredentialError, match="is disabled"):
        sync_source(_root(tmp_path), tmp_path / "build", _source(), {"infra": _remote()}, credentials=resolver)

    assert calls == []


def test_a_failed_clone_never_shows_the_credential_in_its_error(tmp_path, monkeypatch):
    _fake_git(monkeypatch, returncode=128, stderr=f"fatal: Authentication failed for 'https://bot:{TOKEN}@host/'")

    with pytest.raises(RemoteResolutionError) as excinfo:
        sync_source(_root(tmp_path), tmp_path / "build", _source(), {"infra": _remote()}, credentials=_StubResolver())

    assert TOKEN not in str(excinfo.value)
    assert "***" in str(excinfo.value)
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__
