#!/usr/bin/env python3
"""Tests for `strata values get`."""

import json

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""

ENVIRONMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  variables:
    - key: REGION
      store: constant
      value: westeurope
  secrets:
    - key: DB_PASSWORD
      store: constant
      value: hunter2
"""

DEPLOYMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: app
spec:
  partial: true
  environments: [prd]
"""

ENVIRONMENT_FULL = """apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  variables:
    - key: REGION
      store: constant
      value: westeurope
  secrets:
    - key: DB_PASSWORD
      store: infisical
      value: db/password
      generate:
        type: password
        length: 24
      rotate:
        max_age: 90
        policy: warn
  features:
    - key: ENABLE_BETA
      store: constant
      value: "true"
"""


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def solution(tmp_path):
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "environment.yaml").write_text(ENVIRONMENT, encoding="utf-8")
    (root / "deployment.yaml").write_text(DEPLOYMENT, encoding="utf-8")
    return root


@pytest.fixture
def solution_full(tmp_path):
    """A solution whose environment declares one of each kind — a secret
    with `generate:`/`rotate:` specs, for `values list --declared-only`."""
    root = tmp_path / "sln-full"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "environment.yaml").write_text(ENVIRONMENT_FULL, encoding="utf-8")
    (root / "deployment.yaml").write_text(DEPLOYMENT, encoding="utf-8")
    return root


def _run(runner, *args):
    return runner.invoke(cli, ["values", "get", *[str(a) for a in args]])


def _run_list(runner, *args):
    return runner.invoke(cli, ["values", "list", *[str(a) for a in args]])


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def test_all_keys_resolved_exits_zero(runner, solution):
    result = _run(runner, "app", "REGION", "DB_PASSWORD", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS


def test_unresolved_key_exits_three(runner, solution):
    result = _run(runner, "app", "REGION", "GHOST", "--path", solution)
    assert result.exit_code == EXIT_VALIDATION


def test_unknown_deployment_exits_two(runner, solution):
    result = _run(runner, "ghost-deployment", "REGION", "--path", solution)
    assert result.exit_code == EXIT_USAGE


def test_outside_a_solution_exits_two(runner, tmp_path):
    result = _run(runner, "app", "REGION", "--path", tmp_path)
    assert result.exit_code == EXIT_USAGE


def test_raw_format_requires_exactly_one_key(runner, solution):
    result = _run(runner, "app", "REGION", "DB_PASSWORD", "--path", solution, "--format", "raw")
    assert result.exit_code == EXIT_USAGE


# ---------------------------------------------------------------------------
# Console rendering
# ---------------------------------------------------------------------------


def test_table_format_shows_every_requested_key(runner, solution):
    output = _run(runner, "app", "REGION", "DB_PASSWORD", "--path", solution).output
    assert "westeurope" in output
    assert "hunter2" in output


def test_raw_format_prints_the_bare_value(runner, solution):
    result = _run(runner, "app", "REGION", "--path", solution, "--format", "raw")
    assert "westeurope" in result.output
    # Bare value — no key name, no report chrome, on its own line.
    assert "REGION" not in [line.strip() for line in result.output.splitlines()]


def test_env_format_renders_key_equals_value(runner, solution):
    output = _run(runner, "app", "REGION", "--path", solution, "--format", "env").output
    assert "REGION=westeurope" in output


def test_export_format_quotes_the_value(runner, solution):
    output = _run(runner, "app", "REGION", "--path", solution, "--format", "export").output
    assert "export REGION=westeurope" in output


def test_raw_env_export_emit_nothing_when_a_key_fails(runner, solution):
    """A script must never mistake a partial result for a complete one."""
    output = _run(runner, "app", "REGION", "GHOST", "--path", solution, "--format", "env").output
    assert "REGION=" not in output


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_emits_the_resolved_values(runner, solution):
    payload = json.loads(_run(runner, "app", "REGION", "DB_PASSWORD", "--path", solution, "--output", "json").output)
    assert payload["ok"] is True
    assert payload["data"]["deployment"] == "app"
    assert payload["data"]["results"] == {"REGION": "westeurope", "DB_PASSWORD": "hunter2"}


def test_json_reports_a_failed_key_as_null_and_a_diagnostic(runner, solution):
    payload = json.loads(_run(runner, "app", "REGION", "GHOST", "--path", solution, "--output", "json").output)
    assert payload["ok"] is False
    assert payload["data"]["results"] == {"REGION": "westeurope", "GHOST": None}
    assert any("GHOST" in d["message"] for d in payload["diagnostics"])


# ---------------------------------------------------------------------------
# `values list` — live mode (default)
# ---------------------------------------------------------------------------


def test_list_live_exits_zero_when_everything_resolves(runner, solution):
    result = _run_list(runner, "app", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS


def test_list_live_shows_variables_in_full(runner, solution):
    output = _run_list(runner, "app", "--path", solution).output
    assert "westeurope" in output


def test_list_live_masks_secret_values(runner, solution):
    output = _run_list(runner, "app", "--path", solution).output
    assert "hunt" in output  # first 4 chars kept
    assert "hunter2" not in output  # never revealed in full


def test_list_live_show_store_adds_the_store_column(runner, solution):
    without = _run_list(runner, "app", "--path", solution).output
    with_store = _run_list(runner, "app", "--path", solution, "--show-store").output
    assert "constant" not in without
    assert "constant" in with_store


def test_list_live_exits_three_when_a_key_fails(runner, solution_full):
    """`solution_full`'s DB_PASSWORD is `store: infisical` with no
    credentials configured in the test environment — a real failure."""
    result = _run_list(runner, "app", "--path", solution_full)
    assert result.exit_code == EXIT_VALIDATION


def test_list_live_unresolved_filters_to_failures_only(runner, solution_full):
    output = _run_list(runner, "app", "--path", solution_full, "--unresolved").output
    assert "DB_PASSWORD" in output
    assert "REGION" not in output
    assert "ENABLE_BETA" not in output


def test_list_live_type_filter_restricts_to_one_kind(runner, solution):
    output = _run_list(runner, "app", "--path", solution, "--type", "variables").output
    assert "REGION" in output
    assert "DB_PASSWORD" not in output


def test_list_live_json_shape(runner, solution):
    payload = json.loads(_run_list(runner, "app", "--path", solution, "--output", "json").output)
    assert payload["ok"] is True
    assert payload["data"]["declared_only"] is False
    by_key = {row["key"]: row for row in payload["data"]["values"]}
    assert by_key["REGION"] == {
        "key": "REGION",
        "kind": "variable",
        "store": "constant",
        "value": "westeurope",
        "ok": True,
        "reason": None,
    }
    assert by_key["DB_PASSWORD"]["value"] == "hunt***"  # masked, never the full "hunter2"


def test_list_live_unknown_deployment_exits_two(runner, solution):
    result = _run_list(runner, "ghost-deployment", "--path", solution)
    assert result.exit_code == EXIT_USAGE


def test_list_live_with_no_environment_exits_zero_with_empty_list(runner, tmp_path):
    root = tmp_path / "sln-empty"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "deployment.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n  partial: true\n",
        encoding="utf-8",
    )
    result = _run_list(runner, "app", "--path", root)
    assert result.exit_code == EXIT_SUCCESS
    assert "no variables, secrets, or features declared" in result.output


# ---------------------------------------------------------------------------
# `values list --declared-only`
# ---------------------------------------------------------------------------


def test_list_declared_only_exits_zero(runner, solution_full):
    result = _run_list(runner, "app", "--path", solution_full, "--declared-only")
    assert result.exit_code == EXIT_SUCCESS


def test_list_declared_only_shows_every_kind(runner, solution_full):
    output = _run_list(runner, "app", "--path", solution_full, "--declared-only").output
    assert "REGION" in output
    assert "DB_PASSWORD" in output
    assert "ENABLE_BETA" in output


def test_list_declared_only_never_resolves_or_reveals_a_secret_value(runner, solution_full):
    """No store I/O at all — never resolves/reveals the actual secret value."""
    output = _run_list(runner, "app", "--path", solution_full, "--declared-only").output
    assert "hunter2" not in output  # never resolved, so never revealed


def test_list_declared_only_json_includes_the_declared_value_ref(runner, solution_full):
    """Console rendering omits `value_ref` (matches v1's `secret list`); JSON carries it."""
    payload = json.loads(
        _run_list(runner, "app", "--path", solution_full, "--declared-only", "--output", "json").output
    )
    by_key = {row["key"]: row for row in payload["data"]["values"]}
    assert by_key["DB_PASSWORD"]["value_ref"] == "db/password"


def test_list_declared_only_shows_generate_and_rotate_specs(runner, solution_full):
    output = _run_list(runner, "app", "--path", solution_full, "--declared-only").output
    assert "generate:password/24" in output
    assert "rotate:90d/warn" in output


def test_list_declared_only_type_filter_restricts_to_one_kind(runner, solution_full):
    output = _run_list(runner, "app", "--path", solution_full, "--declared-only", "--type", "secrets").output
    assert "DB_PASSWORD" in output
    assert "REGION" not in output
    assert "ENABLE_BETA" not in output


def test_list_unknown_deployment_exits_two(runner, solution_full):
    result = _run_list(runner, "ghost-deployment", "--path", solution_full, "--declared-only")
    assert result.exit_code == EXIT_USAGE


def test_list_declared_only_json_shape(runner, solution_full):
    payload = json.loads(
        _run_list(runner, "app", "--path", solution_full, "--declared-only", "--output", "json").output
    )
    assert payload["ok"] is True
    assert payload["data"]["deployment"] == "app"
    assert payload["data"]["declared_only"] is True
    by_key = {row["key"]: row for row in payload["data"]["values"]}
    assert by_key["REGION"] == {
        "key": "REGION",
        "kind": "variable",
        "store": "constant",
        "value_ref": "westeurope",
        "generate": None,
        "rotate": None,
    }
    assert by_key["DB_PASSWORD"]["generate"] == "password/24"
    assert by_key["DB_PASSWORD"]["rotate"] == "90d/warn"
    assert by_key["ENABLE_BETA"]["kind"] == "feature"


def test_list_declared_only_with_no_environment_exits_zero_with_empty_list(runner, tmp_path):
    root = tmp_path / "sln-empty"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "deployment.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n  partial: true\n",
        encoding="utf-8",
    )
    result = _run_list(runner, "app", "--path", root, "--declared-only")
    assert result.exit_code == EXIT_SUCCESS
    assert "no variables, secrets, or features declared" in result.output


# ---------------------------------------------------------------------------
# `values resolve` — diagnostic only, never reveals a value
# ---------------------------------------------------------------------------


def _run_resolve(runner, *args):
    return runner.invoke(cli, ["values", "resolve", *[str(a) for a in args]])


def test_resolve_all_keys_would_resolve_exits_zero(runner, solution):
    result = _run_resolve(runner, "app", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS


def test_resolve_shows_every_key(runner, solution):
    output = _run_resolve(runner, "app", "--path", solution).output
    assert "REGION" in output
    assert "DB_PASSWORD" in output


def test_resolve_never_reveals_a_value(runner, solution):
    output = _run_resolve(runner, "app", "--path", solution).output
    assert "westeurope" not in output
    assert "hunter2" not in output


def test_resolve_key_filters_to_one_key(runner, solution):
    output = _run_resolve(runner, "app", "--path", solution, "--key", "REGION").output
    assert "REGION" in output
    assert "DB_PASSWORD" not in output


def test_resolve_unknown_key_exits_three(runner, solution):
    result = _run_resolve(runner, "app", "--path", solution, "--key", "GHOST")
    assert result.exit_code == EXIT_VALIDATION


def test_resolve_unset_environment_variable_exits_three(runner, tmp_path, monkeypatch):
    monkeypatch.delenv("PROBE_MISSING_VAR", raising=False)
    root = tmp_path / "sln-env"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "environment.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: GREETING\n      store: environment\n      value: PROBE_MISSING_VAR\n",
        encoding="utf-8",
    )
    (root / "deployment.yaml").write_text(DEPLOYMENT, encoding="utf-8")

    result = _run_resolve(runner, "app", "--path", root)
    assert result.exit_code == EXIT_VALIDATION


def test_resolve_unknown_deployment_exits_two(runner, solution):
    result = _run_resolve(runner, "ghost-deployment", "--path", solution)
    assert result.exit_code == EXIT_USAGE


def test_resolve_json_shape(runner, solution):
    payload = json.loads(_run_resolve(runner, "app", "--path", solution, "--output", "json").output)
    assert payload["ok"] is True
    assert payload["data"]["deployment"] == "app"
    assert payload["data"]["mode"] == "path"
    by_key = {row["key"]: row for row in payload["data"]["results"]}
    assert by_key["REGION"] == {
        "key": "REGION",
        "kind": "variable",
        "store": "constant",
        "registered": True,
        "probed": False,
        "ok": True,
        "reason": None,
    }


def test_resolve_probe_flag_is_reflected_in_json_mode(runner, solution):
    payload = json.loads(_run_resolve(runner, "app", "--path", solution, "--probe", "--output", "json").output)
    assert payload["data"]["mode"] == "probe"
    # constant stores are never "probed" — they're checked directly, cheaply, always.
    assert all(not row["probed"] for row in payload["data"]["results"])


# ---------------------------------------------------------------------------
# `values set`
# ---------------------------------------------------------------------------


def _run_set(runner, *args):
    return runner.invoke(cli, ["values", "set", *[str(a) for a in args]])


def _patch_writable_store(monkeypatch):
    """A `StoreIntegration` whose `set()` records what was written, bound
    in place of the real registry lookup — no network I/O."""
    from strata.integrations.capabilities import StoreIntegration

    class _FakeWritableStore(StoreIntegration):
        TYPE = "infisical"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS: frozenset = frozenset()

        def __init__(self) -> None:
            super().__init__(None)
            self.written: tuple[str, str] | None = None

        def resolve(self, key: str) -> str:
            return f"resolved-{key}"

        def set(self, key: str, value: str) -> None:
            self.written = (key, value)

    store = _FakeWritableStore()
    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: store)
    return store


def test_set_writes_an_explicit_value_and_exits_zero(runner, solution_full, monkeypatch):
    store = _patch_writable_store(monkeypatch)
    result = _run_set(runner, "app", "--path", solution_full, "--key", "DB_PASSWORD", "--value", "new-value")
    assert result.exit_code == EXIT_SUCCESS
    assert store.written == ("db/password", "new-value")


def test_set_console_confirmation_never_shows_the_value(runner, solution_full, monkeypatch):
    _patch_writable_store(monkeypatch)
    output = _run_set(runner, "app", "--path", solution_full, "--key", "DB_PASSWORD", "--value", "top-secret").output
    assert "top-secret" not in output
    assert "DB_PASSWORD" in output
    assert "infisical" in output


def test_set_generate_shows_generated_in_the_confirmation(runner, solution_full, monkeypatch):
    _patch_writable_store(monkeypatch)
    output = _run_set(runner, "app", "--path", solution_full, "--key", "DB_PASSWORD", "--generate").output
    assert "generated" in output


def test_set_from_file_reads_the_file(runner, solution_full, monkeypatch, tmp_path):
    store = _patch_writable_store(monkeypatch)
    cert = tmp_path / "cert.pem"
    cert.write_text("multi\nline\ncontent\n", encoding="utf-8")

    result = _run_set(runner, "app", "--path", solution_full, "--key", "DB_PASSWORD", "--from-file", cert)
    assert result.exit_code == EXIT_SUCCESS
    assert store.written == ("db/password", "multi\nline\ncontent\n")


def test_set_requires_exactly_one_value_source(runner, solution_full):
    result = _run_set(runner, "app", "--path", solution_full, "--key", "DB_PASSWORD")
    assert result.exit_code == EXIT_USAGE


def test_set_rejects_a_constant_store(runner, solution):
    result = _run_set(runner, "app", "--path", solution, "--key", "REGION", "--value", "northeurope")
    assert result.exit_code == EXIT_USAGE


def test_set_generate_on_a_non_secret_exits_two(runner, solution):
    result = _run_set(runner, "app", "--path", solution, "--key", "REGION", "--generate")
    assert result.exit_code == EXIT_USAGE


def test_set_unknown_key_exits_two(runner, solution):
    result = _run_set(runner, "app", "--path", solution, "--key", "GHOST", "--value", "x")
    assert result.exit_code == EXIT_USAGE


def test_set_unknown_deployment_exits_two(runner, solution):
    result = _run_set(runner, "ghost-deployment", "--path", solution, "--key", "REGION", "--value", "x")
    assert result.exit_code == EXIT_USAGE


def test_set_json_shape(runner, solution_full, monkeypatch):
    _patch_writable_store(monkeypatch)
    payload = json.loads(
        _run_set(
            runner, "app", "--path", solution_full, "--key", "DB_PASSWORD", "--value", "x", "--output", "json"
        ).output
    )
    assert payload["ok"] is True
    assert payload["data"] == {
        "deployment": "app",
        "key": "DB_PASSWORD",
        "written": True,
        "kind": "secret",
        "store": "infisical",
        "generated": False,
    }


def test_set_write_failure_exits_three(runner, solution_full, monkeypatch):
    from strata.integrations.capabilities import StoreIntegration
    from strata.integrations.errors import IntegrationError

    class _FailingStore(StoreIntegration):
        TYPE = "infisical"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS: frozenset = frozenset()

        def resolve(self, key: str) -> str:
            return f"resolved-{key}"

        def set(self, key: str, value: str) -> None:
            raise IntegrationError("unreachable")

    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: _FailingStore())

    result = _run_set(runner, "app", "--path", solution_full, "--key", "DB_PASSWORD", "--value", "x")
    assert result.exit_code == EXIT_VALIDATION


# ---------------------------------------------------------------------------
# `values rotate`
# ---------------------------------------------------------------------------


def _run_rotate(runner, *args, **kwargs):
    return runner.invoke(cli, ["values", "rotate", *[str(a) for a in args]], **kwargs)


def test_rotate_with_force_writes_without_prompting(runner, solution_full, monkeypatch):
    store = _patch_writable_store(monkeypatch)
    result = _run_rotate(runner, "app", "DB_PASSWORD", "--path", solution_full, "--force")
    assert result.exit_code == EXIT_SUCCESS
    assert store.written is not None
    assert store.written[0] == "db/password"
    assert len(store.written[1]) == 24  # solution_full's DB_PASSWORD: generate type=password, length=24


def test_rotate_confirmation_prompt_declined_does_not_write(runner, solution_full, monkeypatch):
    store = _patch_writable_store(monkeypatch)
    result = _run_rotate(runner, "app", "DB_PASSWORD", "--path", solution_full, input="n\n")
    assert result.exit_code != EXIT_SUCCESS
    assert store.written is None


def test_rotate_confirmation_prompt_accepted_writes(runner, solution_full, monkeypatch):
    store = _patch_writable_store(monkeypatch)
    result = _run_rotate(runner, "app", "DB_PASSWORD", "--path", solution_full, input="y\n")
    assert result.exit_code == EXIT_SUCCESS
    assert store.written is not None


def test_rotate_console_confirmation_never_shows_the_value(runner, solution_full, monkeypatch):
    _patch_writable_store(monkeypatch)
    output = _run_rotate(runner, "app", "DB_PASSWORD", "--path", solution_full, "--force").output
    assert "DB_PASSWORD" in output
    assert "infisical" in output


def test_rotate_without_generate_spec_exits_two(runner, solution):
    """`solution`'s DB_PASSWORD is `store: constant` — no generate: spec possible."""
    result = _run_rotate(runner, "app", "DB_PASSWORD", "--path", solution, "--force")
    assert result.exit_code == EXIT_USAGE


def test_rotate_on_a_variable_exits_two(runner, solution):
    result = _run_rotate(runner, "app", "REGION", "--path", solution, "--force")
    assert result.exit_code == EXIT_USAGE


def test_rotate_unknown_key_exits_two(runner, solution_full):
    result = _run_rotate(runner, "app", "GHOST", "--path", solution_full, "--force")
    assert result.exit_code == EXIT_USAGE


def test_rotate_unknown_deployment_exits_two(runner, solution_full):
    result = _run_rotate(runner, "ghost-deployment", "DB_PASSWORD", "--path", solution_full, "--force")
    assert result.exit_code == EXIT_USAGE


def test_rotate_json_shape(runner, solution_full, monkeypatch):
    _patch_writable_store(monkeypatch)
    payload = json.loads(
        _run_rotate(runner, "app", "DB_PASSWORD", "--path", solution_full, "--force", "--output", "json").output
    )
    assert payload["ok"] is True
    assert payload["data"] == {
        "deployment": "app",
        "key": "DB_PASSWORD",
        "rotated": True,
        "store": "infisical",
    }


def test_rotate_write_failure_exits_three(runner, solution_full, monkeypatch):
    from strata.integrations.capabilities import StoreIntegration
    from strata.integrations.errors import IntegrationError

    class _FailingStore(StoreIntegration):
        TYPE = "infisical"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS: frozenset = frozenset()

        def resolve(self, key: str) -> str:
            return f"resolved-{key}"

        def set(self, key: str, value: str) -> None:
            raise IntegrationError("unreachable")

    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: _FailingStore())

    result = _run_rotate(runner, "app", "DB_PASSWORD", "--path", solution_full, "--force")
    assert result.exit_code == EXIT_VALIDATION


# ---------------------------------------------------------------------------
# `values status`
# ---------------------------------------------------------------------------


def _run_status(runner, *args):
    return runner.invoke(cli, ["values", "status", *[str(a) for a in args]])


def _patch_metadata_store(monkeypatch, metadata):
    from strata.integrations.capabilities import StoreIntegration

    class _FakeStoreWithMetadata(StoreIntegration):
        TYPE = "infisical"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS: frozenset = frozenset()

        def resolve(self, key: str) -> str:
            return f"resolved-{key}"

        def metadata(self, key: str):
            return metadata

    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeStoreWithMetadata())


def test_status_ok_when_within_max_age_exits_zero(runner, solution_full, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from strata.integrations.resolved_context import SecretMetadata

    _patch_metadata_store(monkeypatch, SecretMetadata(updated_at=datetime.now(timezone.utc) - timedelta(days=10)))
    result = _run_status(runner, "app", "--path", solution_full)
    assert result.exit_code == EXIT_SUCCESS
    assert "DB_PASSWORD" in result.output


def test_status_overdue_exits_three(runner, solution_full, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from strata.integrations.resolved_context import SecretMetadata

    _patch_metadata_store(monkeypatch, SecretMetadata(updated_at=datetime.now(timezone.utc) - timedelta(days=100)))
    result = _run_status(runner, "app", "--path", solution_full)
    assert result.exit_code == EXIT_VALIDATION
    assert "OVERDUE" in result.output


def test_status_no_metadata_does_not_fail(runner, solution_full, monkeypatch):
    _patch_metadata_store(monkeypatch, None)
    result = _run_status(runner, "app", "--path", solution_full)
    assert result.exit_code == EXIT_SUCCESS
    assert "no_metadata" in result.output


def test_status_never_reveals_the_secret_value(runner, solution_full, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from strata.integrations.resolved_context import SecretMetadata

    _patch_metadata_store(monkeypatch, SecretMetadata(updated_at=datetime.now(timezone.utc) - timedelta(days=10)))
    output = _run_status(runner, "app", "--path", solution_full).output
    assert "hunter2" not in output


def test_status_no_secrets_with_rotate_spec_exits_zero(runner, solution):
    """`solution`'s DB_PASSWORD has no `rotate:` spec — nothing to report."""
    result = _run_status(runner, "app", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS
    assert "no secrets with a rotation policy" in result.output


def test_status_unknown_deployment_exits_two(runner, solution_full):
    result = _run_status(runner, "ghost-deployment", "--path", solution_full)
    assert result.exit_code == EXIT_USAGE


def test_status_json_shape(runner, solution_full, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from strata.integrations.resolved_context import SecretMetadata

    _patch_metadata_store(monkeypatch, SecretMetadata(updated_at=datetime.now(timezone.utc) - timedelta(days=10)))
    payload = json.loads(_run_status(runner, "app", "--path", solution_full, "--output", "json").output)
    assert payload["ok"] is True
    assert payload["data"]["deployment"] == "app"
    assert payload["data"]["overdue"] == 0
    row = payload["data"]["secrets"][0]
    assert row["key"] == "DB_PASSWORD"
    assert row["status"] == "ok"
    assert row["age_days"] == 10
    assert row["days_remaining"] == 80


def test_status_json_overdue_count(runner, solution_full, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from strata.integrations.resolved_context import SecretMetadata

    _patch_metadata_store(monkeypatch, SecretMetadata(updated_at=datetime.now(timezone.utc) - timedelta(days=100)))
    payload = json.loads(_run_status(runner, "app", "--path", solution_full, "--output", "json").output)
    assert payload["ok"] is False
    assert payload["data"]["overdue"] == 1


# ---------------------------------------------------------------------------
# `values generate` / `values mask` — pure utilities, no solution needed
# ---------------------------------------------------------------------------


def test_generate_prints_a_bare_value_by_default(runner):
    result = runner.invoke(cli, ["values", "generate", "--format", "hex", "--length", "8"])
    assert result.exit_code == EXIT_SUCCESS
    assert len(result.output.strip()) == 16  # 8 bytes -> 16 hex chars, plus trailing newline


def test_generate_needs_no_solution_at_all(runner, tmp_path, monkeypatch):
    """Unlike every other `values` subcommand, `generate` never calls `open_solution()`."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["values", "generate", "--format", "uuid4"])
    assert result.exit_code == EXIT_SUCCESS


def test_generate_json_includes_format_and_length(runner):
    payload = json.loads(
        runner.invoke(cli, ["values", "generate", "--format", "numeric", "--length", "6", "--output", "json"]).output
    )
    assert payload["format"] == "numeric"
    assert payload["length"] == 6
    assert len(payload["secret"]) == 6


def test_generate_json_omits_length_for_uuid_formats(runner):
    payload = json.loads(runner.invoke(cli, ["values", "generate", "--format", "uuid7", "--output", "json"]).output)
    assert "length" not in payload


def test_generate_rejects_unknown_format(runner):
    result = runner.invoke(cli, ["values", "generate", "--format", "rot13"])
    assert result.exit_code == EXIT_USAGE


def test_generate_rejects_too_short_a_password(runner):
    result = runner.invoke(cli, ["values", "generate", "--format", "password", "--length", "3"])
    assert result.exit_code == EXIT_USAGE


def test_mask_prints_the_bare_masked_value_by_default(runner):
    result = runner.invoke(cli, ["values", "mask", "hunter2-supersecret", "--show", "4"])
    assert result.exit_code == EXIT_SUCCESS
    assert result.output.strip() == "hunt***************"


def test_mask_needs_no_solution_at_all(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["values", "mask", "abcdef"])
    assert result.exit_code == EXIT_SUCCESS


def test_mask_uses_a_custom_char(runner):
    result = runner.invoke(cli, ["values", "mask", "abcdefgh", "--show", "2", "--char", "#"])
    assert result.output.strip() == "ab######"


def test_mask_json_includes_show_and_char(runner):
    payload = json.loads(runner.invoke(cli, ["values", "mask", "abcdefgh", "--output", "json"]).output)
    assert payload == {"masked": "abcd****", "show": 4, "char": "*"}


def test_mask_rejects_a_multi_character_replacement(runner):
    result = runner.invoke(cli, ["values", "mask", "abcdefgh", "--char", "**"])
    assert result.exit_code == EXIT_USAGE
