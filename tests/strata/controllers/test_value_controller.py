#!/usr/bin/env python3
"""Tests for `value_controller.resolve_values` — dispatch, precedence, and merge order."""

from pathlib import Path

import pytest

from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import resolve_values
from strata.utils.errors import UsageError

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _deployment(root: Path, name: str, *, tenant: str | None = None, environments: list[str]) -> None:
    tenant_line = f"  tenant: {tenant}\n" if tenant else ""
    envs = "\n".join(f"    - {e}" for e in environments)
    _write(
        root,
        f"{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: {name}\n"
        f"spec:\n  partial: true\n{tenant_line}  environments:\n{envs}\n",
    )


def _environment(root: Path, name: str, *, variables=None, secrets=None, features=None) -> None:
    def _block(field: str, entries: list[dict] | None) -> str:
        if not entries:
            return ""
        lines = [f"  {field}:"]
        for entry in entries:
            lines.append(f"    - key: {entry['key']}")
            lines.append(f"      store: {entry['store']}")
            lines.append(f"      value: {entry['value']}")
        return "\n".join(lines) + "\n"

    body = _block("variables", variables) + _block("secrets", secrets) + _block("features", features)
    _write(
        root,
        f"environments/{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: {name}\nspec:\n{body}",
    )


def _solution(tmp_path: Path) -> Path:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    return root


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


def test_constant_store_resolves_directly(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    result = resolve_values(_context(root), "app", ["REGION"])
    assert result.values == {"REGION": "westeurope"}
    assert result.diagnostics.ok


def test_environment_store_reads_os_environ(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_VAR", "hello")
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "GREETING", "store": "environment", "value": "PROBE_VAR"}])
    _deployment(root, "app", environments=["prd"])

    result = resolve_values(_context(root), "app", ["GREETING"])
    assert result.values == {"GREETING": "hello"}


def test_unset_environment_variable_is_a_resolution_error(tmp_path, monkeypatch):
    monkeypatch.delenv("PROBE_MISSING_VAR", raising=False)
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "GREETING", "store": "environment", "value": "PROBE_MISSING_VAR"}])
    _deployment(root, "app", environments=["prd"])

    result = resolve_values(_context(root), "app", ["GREETING"])
    assert "GREETING" not in result.values
    assert not result.diagnostics.ok
    assert "PROBE_MISSING_VAR" in result.diagnostics.messages()[0]


def test_undeclared_key_is_not_an_error_stop_but_is_reported(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    result = resolve_values(_context(root), "app", ["REGION", "GHOST"])
    assert result.values == {"REGION": "westeurope"}
    assert not result.diagnostics.ok
    message = result.diagnostics.messages()[0]
    assert "GHOST" in message
    assert "not declared" in message


def test_feature_value_renders_as_lowercase_string(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", features=[{"key": "NEW_UI", "store": "constant", "value": "True"}])
    _deployment(root, "app", environments=["prd"])

    result = resolve_values(_context(root), "app", ["NEW_UI"])
    assert result.values == {"NEW_UI": "true"}


def test_secret_wins_over_variable_on_key_collision(tmp_path):
    root = _solution(tmp_path)
    _environment(
        root,
        "prd",
        variables=[{"key": "SHARED", "store": "constant", "value": "as-variable"}],
        secrets=[{"key": "SHARED", "store": "constant", "value": "as-secret"}],
    )
    _deployment(root, "app", environments=["prd"])

    result = resolve_values(_context(root), "app", ["SHARED"])
    assert result.values == {"SHARED": "as-secret"}


def test_deployment_environment_overrides_tenant_environment(tmp_path):
    """Tenant environments merge in BEFORE the deployment's own (TenantSpecModel's own rule)."""
    root = _solution(tmp_path)
    _environment(root, "base", variables=[{"key": "REGION", "store": "constant", "value": "from-tenant"}])
    _environment(root, "override", variables=[{"key": "REGION", "store": "constant", "value": "from-deployment"}])
    _write(
        root,
        "tenant.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: tenant\nmeta:\n  name: acme\nspec:\n"
        "  display_name: Acme\n  geographies: [europe]\n  environments:\n    - base\n",
    )
    _deployment(root, "app", tenant="acme", environments=["override"])

    result = resolve_values(_context(root), "app", ["REGION"])
    assert result.values == {"REGION": "from-deployment"}


def test_unimplemented_store_type_produces_a_clear_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "VAULT_SECRET", "store": "vault", "value": "kv/secret"}])
    _deployment(root, "app", environments=["prd"])

    result = resolve_values(_context(root), "app", ["VAULT_SECRET"])
    assert "VAULT_SECRET" not in result.values
    assert "no resolver implemented yet" in result.diagnostics.messages()[0]
    assert "vault" in result.diagnostics.messages()[0]


def test_unknown_deployment_name_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="No deployment named 'ghost'"):
        resolve_values(_context(root), "ghost", ["REGION"])
