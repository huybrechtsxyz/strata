#!/usr/bin/env python3
"""Tests for `value_controller.resolve_values` — dispatch, precedence, and merge order."""

from pathlib import Path

import pytest

from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import (
    build_value_references,
    merge_workspace_environment_deployment_properties,
    resolve_values,
)
from strata.integrations.resolved_context import ValueReference
from strata.models.common_models import SourceModel
from strata.models.deployment_model import DeploymentMetaModel, DeploymentModel, DeploymentSpecModel
from strata.models.environment_model import EnvironmentMetaModel, EnvironmentModel, EnvironmentSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.store_model import (
    FeatureStoreModel,
    FeatureStoreType,
    SecretStoreModel,
    SecretStoreType,
    VariableStoreModel,
    VariableStoreType,
)
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel
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


# ---------------------------------------------------------------------------
# build_value_references() (docs/design/build-time-value-categories.md, Q1/Q4)
# ---------------------------------------------------------------------------


def _env_model(name: str, *, variables=None, secrets=None, features=None, properties=None, custom=None):
    return EnvironmentModel(
        meta=EnvironmentMetaModel(name=name),
        spec=EnvironmentSpecModel(
            variables=variables, secrets=secrets, features=features, properties=properties, custom=custom
        ),
    )


def test_build_value_references_constant_store_passes_value_through():
    env = _env_model(
        "prd",
        variables=[VariableStoreModel(key="REGION", store=VariableStoreType.CONSTANT, value="westeurope")],
        features=[FeatureStoreModel(key="NEW_UI", store=FeatureStoreType.CONSTANT, value="True")],
    )
    variable_refs, feature_refs, secret_refs = build_value_references([env])

    assert variable_refs == [ValueReference(key="REGION", store="constant", value="westeurope")]
    # features get v1's truthiness coercion — a real bool, not the raw string.
    assert feature_refs == [ValueReference(key="NEW_UI", store="constant", value=True)]
    assert secret_refs == []


def test_build_value_references_environment_store_reads_os_environ(monkeypatch):
    monkeypatch.setenv("PROBE_VAR", "hello")
    env = _env_model(
        "prd", variables=[VariableStoreModel(key="GREETING", store=VariableStoreType.ENVIRONMENT, value="PROBE_VAR")]
    )
    variable_refs, _features, _secrets = build_value_references([env])
    assert variable_refs[0].value == "hello"


def test_build_value_references_unset_environment_var_is_none(monkeypatch):
    monkeypatch.delenv("PROBE_MISSING_VAR", raising=False)
    env = _env_model(
        "prd",
        variables=[VariableStoreModel(key="GREETING", store=VariableStoreType.ENVIRONMENT, value="PROBE_MISSING_VAR")],
    )
    variable_refs, _features, _secrets = build_value_references([env])
    assert variable_refs[0].value is None


def test_build_value_references_integration_backed_and_secrets_are_always_none():
    env = _env_model(
        "prd",
        variables=[VariableStoreModel(key="DB_HOST", store=VariableStoreType.HASHICORP_VAULT, value="kv/db")],
        secrets=[SecretStoreModel(key="DB_PASSWORD", store=SecretStoreType.CONSTANT, value="hunter2")],
    )
    variable_refs, _features, secret_refs = build_value_references([env])

    assert variable_refs[0].value is None
    # Secrets never get a value here, even for a constant store — never resolved by this function.
    assert secret_refs[0].value is None


def test_build_value_references_merges_multiple_environments_later_wins():
    """Multiple reachable environments — later one wins on a key collision,
    same convention as `merge_environment_models()`/`resolve_values()`."""
    base = _env_model("base", variables=[VariableStoreModel(key="REGION", store=VariableStoreType.CONSTANT, value="from-base")])
    override = _env_model(
        "override", variables=[VariableStoreModel(key="REGION", store=VariableStoreType.CONSTANT, value="from-override")]
    )
    variable_refs, _features, _secrets = build_value_references([base, override])

    assert len(variable_refs) == 1
    assert variable_refs[0].value == "from-override"


# ---------------------------------------------------------------------------
# merge_workspace_environment_deployment_properties() (Q3)
# ---------------------------------------------------------------------------


def _workspace_for_properties(properties=None, custom=None) -> WorkspaceModel:
    return WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p1"],
            provisioners=[ProvisionerModel(name="tf_main", tool="terraform", source=SourceModel(source_path="infra"))],
            properties=properties,
            custom=custom,
        ),
    )


def _deployment_for_properties(properties=None, custom=None) -> DeploymentModel:
    return DeploymentModel(
        meta=DeploymentMetaModel(name="app"),
        spec=DeploymentSpecModel(workspace="ws", environments=["prd"], properties=properties, custom=custom),
    )


def test_merge_properties_later_layer_wins_per_key_deep_merge():
    workspace = _workspace_for_properties(properties={"region": "westeurope", "tier": "standard"})
    environment = _env_model("prd", properties={"tier": "premium"})
    deployment = _deployment_for_properties(properties={"region": "northeurope"})

    merged = merge_workspace_environment_deployment_properties(workspace, [environment], deployment, "properties")

    # deployment's own "region" wins over workspace's; environment's "tier" wins over workspace's.
    assert merged == {"region": "northeurope", "tier": "premium"}


def test_merge_properties_empty_everywhere_is_an_empty_dict():
    workspace = _workspace_for_properties()
    deployment = _deployment_for_properties()
    assert merge_workspace_environment_deployment_properties(workspace, [], deployment, "custom") == {}


def test_merge_properties_multiple_environments_merge_in_order():
    """Two reachable environments — later one wins per key, matching
    `build_value_references()`'s own multi-environment convention above."""
    workspace = _workspace_for_properties(properties={"region": "westeurope", "tier": "standard", "workspace_only": 1})
    base = _env_model("base", properties={"tier": "premium", "base_only": 2})
    override = _env_model("override", properties={"tier": "gold"})
    deployment = _deployment_for_properties()

    merged = merge_workspace_environment_deployment_properties(workspace, [base, override], deployment, "properties")

    assert merged == {"region": "westeurope", "tier": "gold", "workspace_only": 1, "base_only": 2}

