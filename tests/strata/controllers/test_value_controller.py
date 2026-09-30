#!/usr/bin/env python3
"""Tests for `value_controller.resolve_values` — dispatch, precedence, and merge order."""

from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pytest

from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import (
    build_value_references,
    list_values,
    merge_workspace_environment_deployment_properties,
    reachable_environments,
    resolve_artifact,
    resolve_artifact_field,
    resolve_deployment,
    resolve_diagnostic,
    resolve_document_value_references,
    resolve_tenant,
    resolve_values,
    rotate_secret,
    secret_status,
    set_value,
)
from strata.integrations.capabilities import StoreIntegration
from strata.integrations.errors import IntegrationError, ValueResolutionError
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


def _deployment(
    root: Path, name: str, *, tenant: str | None = None, version: str | None = None, environments: list[str]
) -> None:
    tenant_line = f"  tenant: {tenant}\n" if tenant else ""
    version_line = f"  version: {version}\n" if version else ""
    envs = "\n".join(f"    - {e}" for e in environments)
    _write(
        root,
        f"{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: {name}\n"
        f"spec:\n  partial: true\n{tenant_line}{version_line}  environments:\n{envs}\n",
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


def test_resolve_tenant_returns_tenant_model_when_deployment_references_one(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _write(
        root,
        "tenant.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: tenant\nmeta:\n  name: acme\nspec:\n"
        "  display_name: Acme\n  geographies: [europe]\n",
    )
    _deployment(root, "app", tenant="acme", environments=["prd"])

    context = _context(root)
    deployment = resolve_deployment(context, "app")
    tenant = resolve_tenant(context, deployment)

    assert tenant is not None
    assert tenant.meta.name == "acme"
    assert tenant.spec.display_name == "Acme"
    assert tenant.spec.geographies == ["europe"]


def test_resolve_tenant_returns_none_when_deployment_has_no_tenant(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    context = _context(root)
    deployment = resolve_deployment(context, "app")

    assert resolve_tenant(context, deployment) is None


# ---------------------------------------------------------------------------
# resolve_artifact() / resolve_artifact_field() (docs/design/artifact-references.md)
# ---------------------------------------------------------------------------


def _artifact(root: Path, name: str, *, image_name: str, image_tag: str | None = None) -> None:
    tag_line = f"  image_tag: {image_tag!r}\n" if image_tag is not None else ""
    _write(
        root,
        f"{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: artifact\nmeta:\n  name: {name}\n"
        f"spec:\n  image_name: {image_name}\n{tag_line}",
    )


def _version_doc(root: Path, name: str, *, artifact_pins: dict[str, str] | None = None) -> None:
    pins_block = ""
    if artifact_pins:
        lines = [f"      {k}: {v!r}" for k, v in artifact_pins.items()]
        pins_block = "  pins:\n    artifacts:\n" + "\n".join(lines) + "\n"
    _write(
        root,
        f"{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: {name}\nspec:\n{pins_block}",
    )


def test_resolve_artifact_returns_model_when_it_exists(tmp_path):
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api")

    context = _context(root)
    artifact = resolve_artifact(context, "dspapi_container")

    assert artifact is not None
    assert artifact.spec.image_name == "int-docker-test/src/omp.dispatcher.api"


def test_resolve_artifact_returns_none_when_missing(tmp_path):
    root = _solution(tmp_path)
    context = _context(root)

    assert resolve_artifact(context, "ghost") is None


def test_resolve_artifact_field_image_name_is_never_pin_overlaid(tmp_path):
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api")
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    context = _context(root)
    deployment = resolve_deployment(context, "app")

    assert (
        resolve_artifact_field(context, deployment, "dspapi_container", "image_name")
        == "int-docker-test/src/omp.dispatcher.api"
    )


def test_resolve_artifact_field_image_tag_falls_back_to_declared_value(tmp_path):
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api", image_tag="1.0.0")
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    context = _context(root)
    deployment = resolve_deployment(context, "app")

    assert resolve_artifact_field(context, deployment, "dspapi_container", "image_tag") == "1.0.0"


def test_resolve_artifact_field_image_tag_uses_version_pin_when_present(tmp_path):
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api", image_tag="1.0.0")
    _version_doc(root, "prd", artifact_pins={"dspapi_container": "2.0.0"})
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", version="prd", environments=["prd"])

    context = _context(root)
    deployment = resolve_deployment(context, "app")

    assert resolve_artifact_field(context, deployment, "dspapi_container", "image_tag") == "2.0.0"


def test_resolve_artifact_field_image_ref_synthesises_name_and_tag(tmp_path):
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api", image_tag="1.0.0")
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    context = _context(root)
    deployment = resolve_deployment(context, "app")

    assert (
        resolve_artifact_field(context, deployment, "dspapi_container", "image_ref")
        == "int-docker-test/src/omp.dispatcher.api:1.0.0"
    )


def test_resolve_artifact_field_image_ref_is_bare_name_when_tag_blank(tmp_path):
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api")
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    context = _context(root)
    deployment = resolve_deployment(context, "app")

    assert (
        resolve_artifact_field(context, deployment, "dspapi_container", "image_ref")
        == "int-docker-test/src/omp.dispatcher.api"
    )


def test_resolve_artifact_field_returns_none_when_artifact_missing(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    context = _context(root)
    deployment = resolve_deployment(context, "app")

    assert resolve_artifact_field(context, deployment, "ghost", "image_tag") is None


def test_build_value_references_artifact_store_resolves_with_context_and_deployment(tmp_path):
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api", image_tag="1.0.0")
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: dspapi_container_image_tag\n      store: artifact\n"
        "      value: dspapi_container\n      field: image_tag\n",
    )
    _deployment(root, "app", environments=["prd"])

    context = _context(root)
    deployment = resolve_deployment(context, "app")
    environments = reachable_environments(context, deployment)

    variable_refs, _features, _secrets = build_value_references(environments, context=context, deployment=deployment)

    assert variable_refs[0].key == "dspapi_container_image_tag"
    assert variable_refs[0].value == "1.0.0"


def test_build_value_references_artifact_store_is_none_without_context(tmp_path):
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api", image_tag="1.0.0")
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: dspapi_container_image_tag\n      store: artifact\n"
        "      value: dspapi_container\n      field: image_tag\n",
    )
    _deployment(root, "app", environments=["prd"])

    context = _context(root)
    deployment = resolve_deployment(context, "app")
    environments = reachable_environments(context, deployment)

    variable_refs, _features, _secrets = build_value_references(environments)

    assert variable_refs[0].value is None


def test_unimplemented_store_type_produces_a_clear_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "VAULT_SECRET", "store": "vault", "value": "kv/secret"}])
    _deployment(root, "app", environments=["prd"])

    result = resolve_values(_context(root), "app", ["VAULT_SECRET"])
    assert "VAULT_SECRET" not in result.values
    assert "no resolver implemented yet" in result.diagnostics.messages()[0]
    assert "vault" in result.diagnostics.messages()[0]


def test_resolve_values_resolves_artifact_store_directly_not_via_a_store_integration(tmp_path):
    """docs/design/deploy-command.md's Remaining Work item 8, resolved
    2026-09-27: `store: artifact` used to fall into the generic
    `StoreIntegration` dispatch (like `vault` above) and fail with
    'no resolver implemented yet for store artifact' \u2014 it now resolves
    directly against the real ArtifactModel + kind: version pins, same
    mechanism `build_value_references()` already uses at build time."""
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api", image_tag="1.0.0")
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: dspapi_container_image_tag\n      store: artifact\n"
        "      value: dspapi_container\n      field: image_tag\n",
    )
    _deployment(root, "app", environments=["prd"])

    result = resolve_values(_context(root), "app", ["dspapi_container_image_tag"])

    assert result.diagnostics.ok, result.diagnostics.messages()
    assert result.values == {"dspapi_container_image_tag": "1.0.0"}


def test_resolve_values_artifact_store_reports_a_missing_artifact(tmp_path):
    root = _solution(tmp_path)
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: dspapi_container_image_tag\n      store: artifact\n"
        "      value: ghost_artifact\n      field: image_tag\n",
    )
    _deployment(root, "app", environments=["prd"])

    result = resolve_values(_context(root), "app", ["dspapi_container_image_tag"])

    assert not result.diagnostics.ok
    assert "dspapi_container_image_tag" not in result.values
    assert "ghost_artifact" in result.diagnostics.messages()[0]


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
    base = _env_model(
        "base", variables=[VariableStoreModel(key="REGION", store=VariableStoreType.CONSTANT, value="from-base")]
    )
    override = _env_model(
        "override",
        variables=[VariableStoreModel(key="REGION", store=VariableStoreType.CONSTANT, value="from-override")],
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


# ---------------------------------------------------------------------------
# resolve_document_value_references() — docs/design/cross-document-value-
# references.md's Phase 3, the `${value:kind.name.path}` resolution
# primitive. Solution-wide (not deployment-scoped), so every test here
# loads a real solution via `open_solution()` and calls the function
# directly against `context.controller.index` — no deployment involved.
# ---------------------------------------------------------------------------


def _tenant_doc(root: Path, name: str, *, display_name: str = "GSK", geographies: list[str] | None = None) -> None:
    zones = "\n".join(f"    - {g}" for g in (geographies or ["europe"]))
    _write(
        root,
        f"{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: tenant\nmeta:\n  name: {name}\n"
        f'spec:\n  display_name: "{display_name}"\n  geographies:\n{zones}\n',
    )


def _probe(root: Path, token: str, *, name: str = "probe") -> None:
    """A minimal Environment whose `spec.properties.probe` holds one
    `${value:...}` token — `properties` is a free-form `dict[str, Any]`,
    so any string survives Pydantic loading unexamined (the malformed/
    unsupported-kind checks that inspect it are Phase 2 checks, run by
    `.resolve()`, never by `open_solution()` alone — irrelevant here since
    Phase 3's resolver is being called directly, not through validate)."""
    _write(
        root,
        f"environments/{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: {name}\nspec:\n"
        f'  properties:\n    probe: "{token}"\n',
    )


def test_resolve_document_value_references_happy_path_matches_design_example(tmp_path):
    """The exact `tenant.c0062.meta.name` example from this design's own
    "Problem" section."""
    root = _solution(tmp_path)
    _tenant_doc(root, "c0062")
    _probe(root, "${value:tenant.c0062.meta.name}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert diagnostics.ok, diagnostics.messages()
    assert values == {"tenant.c0062.meta.name": "c0062"}


def test_resolve_document_value_references_resolves_a_nested_spec_field(tmp_path):
    root = _solution(tmp_path)
    _tenant_doc(root, "c0062", display_name="GSK plc")
    _probe(root, "${value:tenant.c0062.spec.display_name}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert diagnostics.ok, diagnostics.messages()
    assert values == {"tenant.c0062.spec.display_name": "GSK plc"}


def test_resolve_document_value_references_rejects_unknown_kind(tmp_path):
    root = _solution(tmp_path)
    _probe(root, "${value:bogus.c0062.meta.name}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert not diagnostics.ok
    assert values == {}
    assert diagnostics.items[0].code == "value_reference_unknown_kind"
    assert "bogus" in diagnostics.messages()[0]


def test_resolve_document_value_references_bare_single_segment_is_also_unknown_kind(tmp_path):
    """`${value:onlyonesegment}` — 'onlyonesegment' genuinely isn't a real
    `PlatformKind` value either, so this is reported as the more
    fundamental, more useful `value_reference_unknown_kind` rather than
    `value_reference_invalid_path`. See `resolve_document_value_references()`'s
    own docstring point 2 for why this differs from the design's own,
    looser Phase 2 framing."""
    root = _solution(tmp_path)
    _probe(root, "${value:onlyonesegment}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert not diagnostics.ok
    assert diagnostics.items[0].code == "value_reference_unknown_kind"


def test_resolve_document_value_references_rejects_a_bare_kind_with_no_name(tmp_path):
    """`${value:tenant}` — 'tenant' IS a real kind, but there is nothing to
    look up: no name segment at all. This is the clean, distinct
    `value_reference_invalid_path` case (as opposed to the unknown-kind
    case above)."""
    root = _solution(tmp_path)
    _probe(root, "${value:tenant}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert not diagnostics.ok
    assert diagnostics.items[0].code == "value_reference_invalid_path"


def test_resolve_document_value_references_rejects_unknown_document(tmp_path):
    root = _solution(tmp_path)
    _probe(root, "${value:tenant.doesnotexist.meta.name}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert not diagnostics.ok
    assert values == {}
    assert diagnostics.items[0].code == "value_reference_unknown_document"
    assert "tenant/doesnotexist" in diagnostics.messages()[0]


def test_resolve_document_value_references_kind_solution_is_always_unknown_document(tmp_path):
    """Edge case confirmed in this design's own "Resolution model" section:
    the solution manifest's own kind is deliberately never indexed
    (`SolutionController._load_manifest()`), so `${value:solution....}`
    always fails existence, never a crash — no special-case code needed."""
    root = _solution(tmp_path)
    _probe(root, "${value:solution.test-solution.meta.name}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert not diagnostics.ok
    assert diagnostics.items[0].code == "value_reference_unknown_document"


def test_resolve_document_value_references_rejects_a_nonexistent_field_path(tmp_path):
    root = _solution(tmp_path)
    _tenant_doc(root, "c0062")
    _probe(root, "${value:tenant.c0062.spec.nonexistent_field}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert not diagnostics.ok
    assert values == {}
    assert diagnostics.items[0].code == "value_reference_invalid_path"


def test_resolve_document_value_references_rejects_a_whole_sub_object(tmp_path):
    """`${value:tenant.c0062.spec}` — a real path, but it resolves to the
    whole `spec` dict, not a scalar. "Referencing a whole sub-object is
    out of scope" (this design's own "Deliberately out of scope" section)."""
    root = _solution(tmp_path)
    _tenant_doc(root, "c0062")
    _probe(root, "${value:tenant.c0062.spec}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert not diagnostics.ok
    assert values == {}
    assert diagnostics.items[0].code == "value_reference_not_scalar"


def test_resolve_document_value_references_empty_path_is_also_not_scalar(tmp_path):
    """`${value:tenant.c0062}` — no path segments at all (unlike the bare
    `${value:tenant}` case above, this one DOES have a name). Resolves to
    the whole document dict via a zero-length path walk, which then fails
    the same scalar check as any other whole-sub-object reference — no
    dedicated empty-path special case exists, by design."""
    root = _solution(tmp_path)
    _tenant_doc(root, "c0062")
    _probe(root, "${value:tenant.c0062}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert not diagnostics.ok
    assert diagnostics.items[0].code == "value_reference_not_scalar"


def test_resolve_document_value_references_rejects_a_non_literal_target(tmp_path):
    """The target field itself contains an unresolved `${var:...}` token —
    not yet a pure literal, so referencing it is rejected. This is also
    the exact mechanism that makes chained/transitive `${value:}`
    resolution and cycles both impossible by construction (this design's
    own "cycles are impossible by construction" section)."""
    root = _solution(tmp_path)
    _tenant_doc(root, "c0062", display_name="${var:tenant_display_name}")
    _probe(root, "${value:tenant.c0062.spec.display_name}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert not diagnostics.ok
    assert values == {}
    assert diagnostics.items[0].code == "value_reference_target_not_literal"


def test_resolve_document_value_references_rejects_chaining_through_another_value_token(tmp_path):
    """Same rejection, but the target field's own unresolved token is
    itself a `${value:...}` rather than `${var:...}` — proves the "no
    transitive/chained resolution" rule holds for this kind referencing
    itself, not just for a mix of kinds."""
    root = _solution(tmp_path)
    _tenant_doc(root, "c0062", display_name="${value:tenant.other.meta.name}")
    _tenant_doc(root, "other", display_name="Other Co")
    _probe(root, "${value:tenant.c0062.spec.display_name}")
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert not diagnostics.ok
    assert diagnostics.items[0].code == "value_reference_target_not_literal"


def test_resolve_document_value_references_reports_source_and_location(tmp_path):
    """Diagnostics are attributed to the document the token was *found
    in* (not the target) — matching `unresolved_value_tokens()`'s own
    convention every other Value-token diagnostic already follows."""
    root = _solution(tmp_path)
    _probe(root, "${value:tenant.doesnotexist.meta.name}")
    context = _context(root)

    _values, diagnostics = resolve_document_value_references(context.controller.index)

    diagnostic = diagnostics.items[0]
    assert diagnostic.source is not None and "probe.yaml" in diagnostic.source
    assert diagnostic.location == "spec.properties.probe"


def test_resolve_document_value_references_ignores_documents_with_no_value_tokens(tmp_path):
    """A solution with real documents but zero `${value:...}` tokens
    anywhere resolves cleanly to an empty map — confirms the walk doesn't
    misfire on ordinary `${var:}`/plain-literal content."""
    root = _solution(tmp_path)
    _tenant_doc(root, "c0062")
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    context = _context(root)

    values, diagnostics = resolve_document_value_references(context.controller.index)

    assert diagnostics.ok, diagnostics.messages()
    assert values == {}


# ---------------------------------------------------------------------------
# resolve_values() merging in resolve_document_value_references() — Phase 4.
# Solution-wide, so a `${value:...}` entry appears in `.values` regardless
# of the `keys` list passed in (it is never a declared var/secret/feature
# key an Environment could name).
# ---------------------------------------------------------------------------


def test_resolve_values_merges_value_references_into_values(tmp_path):
    root = _solution(tmp_path)
    _tenant_doc(root, "c0062")
    _probe(root, "${value:tenant.c0062.meta.name}", name="prd")
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    result = resolve_values(context, "app", [])

    assert result.diagnostics.ok, result.diagnostics.messages()
    assert result.values["tenant.c0062.meta.name"] == "c0062"


def test_resolve_values_still_resolves_declared_keys_alongside_value_references(tmp_path):
    """The merge is additive, not a replacement — a real declared `keys`
    entry still resolves exactly as before."""
    root = _solution(tmp_path)
    _tenant_doc(root, "c0062")
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: REGION\n      store: constant\n      value: westeurope\n"
        '  properties:\n    probe: "${value:tenant.c0062.meta.name}"\n',
    )
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    result = resolve_values(context, "app", ["REGION"])

    assert result.diagnostics.ok, result.diagnostics.messages()
    assert result.values["REGION"] == "westeurope"
    assert result.values["tenant.c0062.meta.name"] == "c0062"


def test_resolve_values_reports_an_unresolvable_value_reference_without_raising(tmp_path):
    """A `${value:...}` failure surfaces in `.diagnostics`, exactly like an
    unresolvable declared key already does — it does not raise, and does
    not prevent the rest of `keys` from resolving."""
    root = _solution(tmp_path)
    _probe(root, "${value:tenant.doesnotexist.meta.name}", name="prd")
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    result = resolve_values(context, "app", [])

    assert not result.diagnostics.ok
    assert "tenant.doesnotexist.meta.name" not in result.values
    message = result.diagnostics.messages()[0]
    assert "does not exist" in message


# ---------------------------------------------------------------------------
# Store resolution binds a real `kind: integration` document by type —
# docs/design/store-integration-configuration.md's Phase 1.
# `get_integration()` is monkeypatched with a spy returning a lightweight
# fake `StoreIntegration` (never a real network call) so these tests only
# verify *wiring* — what `config` reached the registry call — matching
# `test_integration_resolution.py`'s own `integration.config`-inspection
# style for the equivalent `InfraIntegration` binding.
# ---------------------------------------------------------------------------


class _FakeStore(StoreIntegration):
    """Minimal concrete `StoreIntegration` — never talks to a real backend."""

    TYPE = "infisical"
    CAPABILITIES = frozenset({"secrets"})
    TRANSPORTS: frozenset[str] = frozenset()

    def resolve(self, key: str) -> str:
        return f"resolved-{key}"


def _integration_doc(root: Path, name: str, *, type: str = "infisical", enabled: bool = True) -> None:
    _write(
        root,
        f"integrations/{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: {name}\nspec:\n"
        f"  type: {type}\n  enabled: {str(enabled).lower()}\n",
    )


def _spy_get_integration(monkeypatch, captured: list):
    def _fake(integration_type, config=None):
        captured.append(config)
        return _FakeStore(config)

    monkeypatch.setattr("strata.controllers.value_controller.get_integration", _fake)


def test_resolve_values_binds_the_sole_enabled_integration_document_to_the_store(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _integration_doc(root, "infisical-prod")
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db-password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    captured: list = []
    _spy_get_integration(monkeypatch, captured)

    result = resolve_values(context, "app", ["DB_PASSWORD"])

    assert result.diagnostics.ok, result.diagnostics.messages()
    assert result.values["DB_PASSWORD"] == "resolved-db-password"
    assert len(captured) == 1
    assert captured[0] is not None
    assert captured[0].meta.name == "infisical-prod"


def test_resolve_values_passes_none_when_no_integration_document_declared(tmp_path, monkeypatch):
    """Zero candidates — today's behaviour, unchanged: `config=None`, same
    as before this design existed (falls back to env-var-only inside the
    real resolver classes)."""
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db-password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    captured: list = []
    _spy_get_integration(monkeypatch, captured)

    result = resolve_values(context, "app", ["DB_PASSWORD"])

    assert result.diagnostics.ok, result.diagnostics.messages()
    assert captured == [None]


def test_resolve_values_ignores_a_disabled_integration_document(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _integration_doc(root, "infisical-disabled", enabled=False)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db-password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    captured: list = []
    _spy_get_integration(monkeypatch, captured)

    resolve_values(context, "app", ["DB_PASSWORD"])

    assert captured == [None]


def test_resolve_values_raises_on_multiple_enabled_integration_documents(tmp_path, monkeypatch):
    """More than one enabled candidate is a `UsageError` naming every one —
    the same rule `bind_integration_config()` already enforces for
    `InfraIntegration`, reused unchanged for a store type."""
    root = _solution(tmp_path)
    _integration_doc(root, "infisical-a")
    _integration_doc(root, "infisical-b")
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db-password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    captured: list = []
    _spy_get_integration(monkeypatch, captured)

    with pytest.raises(UsageError, match="infisical-a.*infisical-b|infisical-b.*infisical-a"):
        resolve_values(context, "app", ["DB_PASSWORD"])


# ---------------------------------------------------------------------------
# list_values() — docs/design/values-secrets-command.md
# ---------------------------------------------------------------------------


def _full_environment(root: Path, name: str = "prd") -> None:
    """One of each kind, plus a secret with `generate:`/`rotate:` specs —
    everything `list_values()`'s declared-only rows need to surface."""
    _write(
        root,
        f"environments/{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: {name}\nspec:\n"
        "  variables:\n"
        "    - key: REGION\n      store: constant\n      value: westeurope\n"
        "  secrets:\n"
        "    - key: DB_PASSWORD\n      store: infisical\n      value: db/password\n"
        "      generate:\n        type: password\n        length: 24\n"
        "      rotate:\n        max_age: 90\n        policy: warn\n"
        "  features:\n"
        '    - key: ENABLE_BETA\n      store: constant\n      value: "true"\n',
    )


def test_list_values_declared_only_returns_one_row_per_kind(tmp_path):
    root = _solution(tmp_path)
    _full_environment(root)
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = list_values(_context(root), "app", declared_only=True)

    assert diagnostics.ok
    by_key = {row.key: row for row in rows}
    assert set(by_key) == {"REGION", "DB_PASSWORD", "ENABLE_BETA"}
    assert by_key["REGION"].kind == "variable"
    assert by_key["DB_PASSWORD"].kind == "secret"
    assert by_key["ENABLE_BETA"].kind == "feature"


def test_list_values_declared_only_never_touches_a_store(tmp_path, monkeypatch):
    """No `StoreIntegration` is ever constructed — declared-only listing is
    pure YAML introspection."""
    root = _solution(tmp_path)
    _full_environment(root)
    _deployment(root, "app", environments=["prd"])

    def _boom(*_args, **_kwargs):
        raise AssertionError("declared-only listing must never construct an integration")

    monkeypatch.setattr("strata.controllers.value_controller.get_integration", _boom)

    rows, _diagnostics = list_values(_context(root), "app", declared_only=True)
    assert len(rows) == 3


def test_list_values_declared_only_carries_the_declared_value_ref_not_a_resolved_value(tmp_path):
    root = _solution(tmp_path)
    _full_environment(root)
    _deployment(root, "app", environments=["prd"])

    rows, _diagnostics = list_values(_context(root), "app", declared_only=True)

    db_password = next(row for row in rows if row.key == "DB_PASSWORD")
    assert db_password.value_ref == "db/password"  # the store path/ID, never the actual secret
    assert db_password.store == "infisical"


def test_list_values_declared_only_summarises_generate_and_rotate_specs(tmp_path):
    root = _solution(tmp_path)
    _full_environment(root)
    _deployment(root, "app", environments=["prd"])

    rows, _diagnostics = list_values(_context(root), "app", declared_only=True)

    db_password = next(row for row in rows if row.key == "DB_PASSWORD")
    assert db_password.generate == "password/24"
    assert db_password.rotate == "90d/warn"

    region = next(row for row in rows if row.key == "REGION")
    assert region.generate is None
    assert region.rotate is None


def test_list_values_declared_only_type_filter_restricts_to_one_kind(tmp_path):
    root = _solution(tmp_path)
    _full_environment(root)
    _deployment(root, "app", environments=["prd"])

    rows, _diagnostics = list_values(_context(root), "app", type_filter="secrets", declared_only=True)

    assert [row.key for row in rows] == ["DB_PASSWORD"]


def test_list_values_declared_only_rows_are_sorted_by_kind_then_key(tmp_path):
    root = _solution(tmp_path)
    _full_environment(root)
    _deployment(root, "app", environments=["prd"])

    rows, _diagnostics = list_values(_context(root), "app", declared_only=True)

    assert [(row.kind, row.key) for row in rows] == [
        ("feature", "ENABLE_BETA"),
        ("secret", "DB_PASSWORD"),
        ("variable", "REGION"),
    ]


def test_list_values_declared_only_unknown_deployment_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _full_environment(root)
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="No deployment named 'ghost'"):
        list_values(_context(root), "ghost", declared_only=True)


def test_list_values_declared_only_empty_environment_returns_no_rows(tmp_path):
    root = _solution(tmp_path)
    _deployment(root, "app", environments=[])

    rows, diagnostics = list_values(_context(root), "app", declared_only=True)

    assert rows == []
    assert diagnostics.ok


# ---------------------------------------------------------------------------
# list_values() live mode (declared_only=False, the default) —
# docs/design/values-secrets-command.md Phase 4.
# ---------------------------------------------------------------------------


def test_list_values_live_resolves_variables_and_features_in_full(tmp_path):
    root = _solution(tmp_path)
    _environment(
        root,
        "prd",
        variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}],
        features=[{"key": "ENABLE_BETA", "store": "constant", "value": "true"}],
    )
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = list_values(_context(root), "app")

    assert diagnostics.ok
    by_key = {row.key: row for row in rows}
    assert by_key["REGION"].value == "westeurope"
    assert by_key["REGION"].ok is True
    assert by_key["ENABLE_BETA"].value == "true"


def test_list_values_live_masks_secret_values(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration", lambda integration_type, config=None: _FakeStore()
    )

    rows, diagnostics = list_values(context, "app")

    assert diagnostics.ok
    row = rows[0]
    assert row.ok is True
    # _FakeStore.resolve() returns f"resolved-{key}" — mask_secret()'s default show=4 keeps "reso".
    assert row.value == "reso" + "*" * len("resolved-db/password"[4:])
    assert "resolved-db/password" not in (row.value or "")


def test_list_values_live_reports_a_failed_key_without_stopping_the_batch(tmp_path, monkeypatch):
    monkeypatch.delenv("PROBE_MISSING_VAR", raising=False)
    root = _solution(tmp_path)
    _environment(
        root,
        "prd",
        variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}],
        secrets=[{"key": "GREETING", "store": "environment", "value": "PROBE_MISSING_VAR"}],
    )
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = list_values(_context(root), "app")

    assert not diagnostics.ok
    by_key = {row.key: row for row in rows}
    assert by_key["REGION"].ok is True  # unaffected by the other key's failure
    assert by_key["GREETING"].ok is False
    assert by_key["GREETING"].value is None
    assert "PROBE_MISSING_VAR" in (by_key["GREETING"].reason or "")


def test_list_values_live_unresolved_only_filters_to_failures(tmp_path, monkeypatch):
    monkeypatch.delenv("PROBE_MISSING_VAR", raising=False)
    root = _solution(tmp_path)
    _environment(
        root,
        "prd",
        variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}],
        secrets=[{"key": "GREETING", "store": "environment", "value": "PROBE_MISSING_VAR"}],
    )
    _deployment(root, "app", environments=["prd"])

    rows, _diagnostics = list_values(_context(root), "app", unresolved_only=True)

    assert [row.key for row in rows] == ["GREETING"]


def test_list_values_live_type_filter_restricts_to_one_kind(tmp_path):
    root = _solution(tmp_path)
    _environment(
        root,
        "prd",
        variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}],
        features=[{"key": "ENABLE_BETA", "store": "constant", "value": "true"}],
    )
    _deployment(root, "app", environments=["prd"])

    rows, _diagnostics = list_values(_context(root), "app", type_filter="variables")

    assert [row.key for row in rows] == ["REGION"]


def test_list_values_live_rows_are_sorted_by_kind_then_key(tmp_path):
    root = _solution(tmp_path)
    _environment(
        root,
        "prd",
        variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}],
        secrets=[{"key": "DB_PASSWORD", "store": "constant", "value": "hunter2"}],
        features=[{"key": "ENABLE_BETA", "store": "constant", "value": "true"}],
    )
    _deployment(root, "app", environments=["prd"])

    rows, _diagnostics = list_values(_context(root), "app")

    assert [(row.kind, row.key) for row in rows] == [
        ("feature", "ENABLE_BETA"),
        ("secret", "DB_PASSWORD"),
        ("variable", "REGION"),
    ]


def test_list_values_live_unknown_deployment_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="No deployment named 'ghost'"):
        list_values(_context(root), "ghost")


# ---------------------------------------------------------------------------
# resolve_diagnostic() — docs/design/values-secrets-command.md Phase 3
# (`values resolve`): resolution-path diagnostics, never revealing a value.
# ---------------------------------------------------------------------------


class _FakeStoreThatFails(StoreIntegration):
    """A `StoreIntegration` whose `resolve()` always fails — for `--probe` tests."""

    TYPE = "infisical"
    CAPABILITIES = frozenset({"secrets"})
    TRANSPORTS: frozenset[str] = frozenset()

    def resolve(self, key: str) -> str:
        raise ValueResolutionError(f"backend unreachable for '{key}'")


def test_resolve_diagnostic_constant_store_is_ok_without_registration_or_probe(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = resolve_diagnostic(_context(root), "app")

    assert diagnostics.ok
    row = rows[0]
    assert row.key == "REGION"
    assert row.kind == "variable"
    assert row.store == "constant"
    assert row.registered is True
    assert row.probed is False
    assert row.ok is True
    assert row.reason is None


def test_resolve_diagnostic_environment_store_set_is_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_VAR", "hello")
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "GREETING", "store": "environment", "value": "PROBE_VAR"}])
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = resolve_diagnostic(_context(root), "app")

    assert diagnostics.ok
    assert rows[0].ok is True


def test_resolve_diagnostic_environment_store_unset_is_not_ok(tmp_path, monkeypatch):
    monkeypatch.delenv("PROBE_MISSING_VAR", raising=False)
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "GREETING", "store": "environment", "value": "PROBE_MISSING_VAR"}])
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = resolve_diagnostic(_context(root), "app")

    assert not diagnostics.ok
    assert rows[0].ok is False
    assert "PROBE_MISSING_VAR" in (rows[0].reason or "")
    assert "GREETING" in diagnostics.messages()[0]


def test_resolve_diagnostic_never_reveals_a_value(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_VAR", "super-secret-value")
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "environment", "value": "PROBE_VAR"}])
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = resolve_diagnostic(_context(root), "app")

    assert "super-secret-value" not in repr(rows)
    assert "super-secret-value" not in " ".join(diagnostics.messages())


def test_resolve_diagnostic_artifact_store_found_is_ok(tmp_path):
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api", image_tag="1.0.0")
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: dspapi_container_image_tag\n      store: artifact\n"
        "      value: dspapi_container\n      field: image_tag\n",
    )
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = resolve_diagnostic(_context(root), "app")

    assert diagnostics.ok
    assert rows[0].store == "artifact"
    assert rows[0].ok is True


def test_resolve_diagnostic_artifact_store_missing_is_not_ok(tmp_path):
    root = _solution(tmp_path)
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: dspapi_container_image_tag\n      store: artifact\n"
        "      value: ghost_artifact\n      field: image_tag\n",
    )
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = resolve_diagnostic(_context(root), "app")

    assert not diagnostics.ok
    assert rows[0].ok is False
    assert "ghost_artifact" in (rows[0].reason or "")


def test_resolve_diagnostic_unregistered_store_type_is_not_ok(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "VAULT_SECRET", "store": "vault", "value": "kv/secret"}])
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = resolve_diagnostic(_context(root), "app")

    assert rows[0].registered is False
    assert rows[0].probed is False
    assert rows[0].ok is False
    assert "no resolver implemented yet" in (rows[0].reason or "")


def test_resolve_diagnostic_registered_store_without_probe_is_ok_and_not_probed(tmp_path, monkeypatch):
    """Without `--probe`, construction alone (never a real `resolve()` call) is enough."""
    root = _solution(tmp_path)
    _integration_doc(root, "infisical-prod")
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    captured: list = []
    _spy_get_integration(monkeypatch, captured)

    rows, diagnostics = resolve_diagnostic(context, "app", probe=False)

    assert diagnostics.ok
    assert rows[0].registered is True
    assert rows[0].probed is False
    assert rows[0].ok is True


def test_resolve_diagnostic_probe_success(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration", lambda integration_type, config=None: _FakeStore()
    )

    rows, diagnostics = resolve_diagnostic(context, "app", probe=True)

    assert diagnostics.ok
    assert rows[0].probed is True
    assert rows[0].ok is True


def test_resolve_diagnostic_probe_failure_is_reported_without_revealing_anything(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration",
        lambda integration_type, config=None: _FakeStoreThatFails(),
    )

    rows, diagnostics = resolve_diagnostic(context, "app", probe=True)

    assert not diagnostics.ok
    assert rows[0].probed is True
    assert rows[0].ok is False
    assert "backend unreachable" in (rows[0].reason or "")


def test_resolve_diagnostic_key_filters_to_one_row(tmp_path):
    root = _solution(tmp_path)
    _environment(
        root,
        "prd",
        variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}],
        secrets=[{"key": "DB_PASSWORD", "store": "constant", "value": "hunter2"}],
    )
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = resolve_diagnostic(_context(root), "app", key="REGION")

    assert diagnostics.ok
    assert [row.key for row in rows] == ["REGION"]


def test_resolve_diagnostic_unknown_key_reports_a_diagnostic_and_no_rows(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = resolve_diagnostic(_context(root), "app", key="GHOST")

    assert rows == []
    assert not diagnostics.ok
    assert diagnostics.items[0].code == "unknown_value_key"


def test_resolve_diagnostic_rows_are_sorted_by_kind_then_key(tmp_path):
    root = _solution(tmp_path)
    _environment(
        root,
        "prd",
        variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}],
        secrets=[{"key": "DB_PASSWORD", "store": "constant", "value": "hunter2"}],
        features=[{"key": "ENABLE_BETA", "store": "constant", "value": "true"}],
    )
    _deployment(root, "app", environments=["prd"])

    rows, _diagnostics = resolve_diagnostic(_context(root), "app")

    assert [(row.kind, row.key) for row in rows] == [
        ("feature", "ENABLE_BETA"),
        ("secret", "DB_PASSWORD"),
        ("variable", "REGION"),
    ]


def test_resolve_diagnostic_unknown_deployment_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="No deployment named 'ghost'"):
        resolve_diagnostic(_context(root), "ghost")


def test_resolve_diagnostic_raises_on_multiple_enabled_integration_documents(tmp_path, monkeypatch):
    """Same `bind_integration_config()` rule `resolve_values()` enforces — a real
    configuration error, not a per-key finding."""
    root = _solution(tmp_path)
    _integration_doc(root, "infisical-a")
    _integration_doc(root, "infisical-b")
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    captured: list = []
    _spy_get_integration(monkeypatch, captured)

    with pytest.raises(UsageError, match="infisical-a.*infisical-b|infisical-b.*infisical-a"):
        resolve_diagnostic(context, "app")


# ---------------------------------------------------------------------------
# set_value() — docs/design/values-secrets-command.md Phase 6 (`values set`).
# ---------------------------------------------------------------------------


class _FakeWritableStore(StoreIntegration):
    """A `StoreIntegration` whose `set()` records what was written —
    for `values set` success-path tests."""

    TYPE = "infisical"
    CAPABILITIES = frozenset({"secrets"})
    TRANSPORTS: frozenset[str] = frozenset()

    def __init__(self) -> None:
        super().__init__(None)
        self.written: tuple[str, str] | None = None

    def resolve(self, key: str) -> str:
        return f"resolved-{key}"

    def set(self, key: str, value: str) -> None:
        self.written = (key, value)


class _FakeStoreThatRejectsWrites(StoreIntegration):
    """A `StoreIntegration` whose `set()` always fails — for `values set`
    write-failure tests. Unlike the base default (`IntegrationError` for
    "does not support writes"), this simulates a real, configured backend
    rejecting the specific write (unreachable/unauthenticated)."""

    TYPE = "infisical"
    CAPABILITIES = frozenset({"secrets"})
    TRANSPORTS: frozenset[str] = frozenset()

    def resolve(self, key: str) -> str:
        return f"resolved-{key}"

    def set(self, key: str, value: str) -> None:
        raise IntegrationError(f"backend rejected the write for '{key}'")


def test_set_value_writes_an_explicit_value(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)

    store = _FakeWritableStore()
    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: store)

    result, diagnostics = set_value(context, "app", "DB_PASSWORD", value="new-secret-value")

    assert diagnostics.ok
    assert result is not None
    assert result.key == "DB_PASSWORD"
    assert result.kind == "secret"
    assert result.store == "infisical"
    assert result.generated is False
    assert store.written == ("db/password", "new-secret-value")


def test_set_value_never_carries_the_written_value_on_the_result(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeWritableStore())

    result, _diagnostics = set_value(context, "app", "DB_PASSWORD", value="super-secret")

    assert result is not None
    assert "super-secret" not in repr(result)
    assert not hasattr(result, "value")


def test_set_value_from_file_reads_the_files_content(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "TLS_CERT", "store": "infisical", "value": "certs/tls"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    store = _FakeWritableStore()
    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: store)

    cert_file = tmp_path / "cert.pem"
    cert_file.write_text("-----BEGIN CERTIFICATE-----\nmulti\nline\n-----END CERTIFICATE-----\n", encoding="utf-8")

    result, diagnostics = set_value(context, "app", "TLS_CERT", from_file=str(cert_file))

    assert diagnostics.ok
    assert result is not None
    assert store.written == ("certs/tls", cert_file.read_text(encoding="utf-8"))


def test_set_value_from_file_missing_file_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "TLS_CERT", "store": "infisical", "value": "certs/tls"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="Cannot read --from-file"):
        set_value(_context(root), "app", "TLS_CERT", from_file=str(tmp_path / "ghost.pem"))


def test_set_value_from_stdin_reads_stdin(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    store = _FakeWritableStore()
    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: store)
    monkeypatch.setattr("strata.controllers.value_controller.stdin", StringIO("piped-value"))

    result, diagnostics = set_value(context, "app", "DB_PASSWORD", from_stdin=True)

    assert diagnostics.ok
    assert result is not None
    assert store.written == ("db/password", "piped-value")


def test_set_value_generate_uses_the_keys_own_generate_spec(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  secrets:\n    - key: DB_PASSWORD\n      store: infisical\n      value: db/password\n"
        "      generate:\n        type: password\n        length: 20\n",
    )
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    store = _FakeWritableStore()
    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: store)

    result, diagnostics = set_value(context, "app", "DB_PASSWORD", generate=True)

    assert diagnostics.ok
    assert result is not None
    assert result.generated is True
    assert store.written is not None
    written_key, written_value = store.written
    assert written_key == "db/password"
    assert len(written_value) == 20


def test_set_value_generate_without_a_generate_spec_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="has no generate: spec"):
        set_value(_context(root), "app", "DB_PASSWORD", generate=True)


def test_set_value_generate_on_a_variable_raises_usage_error(tmp_path):
    """Uses an integration-backed (non-builtin) variable store, isolating
    the "--generate is secrets-only" check from the separate "builtin
    store, nothing to write to" rejection — a `constant`/`environment`
    variable would raise for the builtin-store reason first, which is a
    different, equally valid rejection, not what this test is about."""
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "azure-appconfig", "value": "region"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="only valid for secrets"):
        set_value(_context(root), "app", "REGION", generate=True)


@pytest.mark.parametrize(
    ("kwargs", "expected_match"),
    [
        ({}, "exactly one"),
        ({"value": "a", "from_stdin": True}, "exactly one"),
        ({"value": "a", "generate": True}, "exactly one"),
    ],
)
def test_set_value_requires_exactly_one_value_source(tmp_path, kwargs, expected_match):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match=expected_match):
        set_value(_context(root), "app", "DB_PASSWORD", **kwargs)


def test_set_value_rejects_a_constant_store(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="constant value"):
        set_value(_context(root), "app", "REGION", value="northeurope")


def test_set_value_rejects_an_environment_store(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "environment", "value": "REGION_VAR"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="environment variable 'REGION_VAR'"):
        set_value(_context(root), "app", "REGION", value="northeurope")


def test_set_value_rejects_a_github_secret_store(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DEPLOY_TOKEN", "store": "github", "value": "DEPLOY_TOKEN"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="GitHub Actions secret"):
        set_value(_context(root), "app", "DEPLOY_TOKEN", value="new-token")


def test_set_value_rejects_an_artifact_store(tmp_path):
    root = _solution(tmp_path)
    _artifact(root, "dspapi_container", image_name="int-docker-test/src/omp.dispatcher.api", image_tag="1.0.0")
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: dspapi_container_image_tag\n      store: artifact\n"
        "      value: dspapi_container\n      field: image_tag\n",
    )
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="in-solution artifact reference"):
        set_value(_context(root), "app", "dspapi_container_image_tag", value="2.0.0")


def test_set_value_unknown_key_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="'GHOST' is not declared"):
        set_value(_context(root), "app", "GHOST", value="x")


def test_set_value_unknown_deployment_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="No deployment named 'ghost'"):
        set_value(_context(root), "ghost", "REGION", value="x")


def test_set_value_write_failure_is_reported_not_raised(tmp_path, monkeypatch):
    """A real write failure (IntegrationError from `.set()`) is not a
    `StrataError` — must be converted to a Diagnostic, not left to
    propagate uncaught past `command_run()`'s StrataError-only handler."""
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeStoreThatRejectsWrites()
    )

    result, diagnostics = set_value(context, "app", "DB_PASSWORD", value="x")

    assert result is None
    assert not diagnostics.ok
    assert "backend rejected the write" in diagnostics.messages()[0]


def test_set_value_unregistered_store_type_is_reported_not_raised(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "VAULT_SECRET", "store": "vault", "value": "kv/secret"}])
    _deployment(root, "app", environments=["prd"])

    result, diagnostics = set_value(_context(root), "app", "VAULT_SECRET", value="x")

    assert result is None
    assert not diagnostics.ok


# ---------------------------------------------------------------------------
# rotate_secret() — docs/design/values-secrets-command.md Phase 7 (`values rotate`).
# ---------------------------------------------------------------------------


def _rotatable_environment(root: Path, name: str = "prd") -> None:
    """A secret with a `generate:` spec — the only kind `rotate_secret()`
    will act on."""
    _write(
        root,
        f"environments/{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: {name}\nspec:\n"
        "  secrets:\n    - key: DB_PASSWORD\n      store: infisical\n      value: db/password\n"
        "      generate:\n        type: password\n        length: 20\n",
    )


def test_rotate_secret_generates_and_writes_a_new_value(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _rotatable_environment(root)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    store = _FakeWritableStore()
    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: store)

    result, diagnostics = rotate_secret(context, "app", "DB_PASSWORD")

    assert diagnostics.ok
    assert result is not None
    assert result.key == "DB_PASSWORD"
    assert result.kind == "secret"
    assert result.store == "infisical"
    assert result.generated is True
    assert store.written is not None
    written_key, written_value = store.written
    assert written_key == "db/password"
    assert len(written_value) == 20


def test_rotate_secret_never_carries_the_new_value_on_the_result(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _rotatable_environment(root)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeWritableStore())

    result, _diagnostics = rotate_secret(context, "app", "DB_PASSWORD")

    assert result is not None
    assert not hasattr(result, "value")


def test_rotate_secret_two_rotations_generate_different_values(tmp_path, monkeypatch):
    """A weak sanity check that rotation actually re-generates rather than
    reusing a cached/fixed value."""
    root = _solution(tmp_path)
    _rotatable_environment(root)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    store = _FakeWritableStore()
    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: store)

    rotate_secret(context, "app", "DB_PASSWORD")
    assert store.written is not None
    first_value = store.written[1]

    rotate_secret(context, "app", "DB_PASSWORD")
    assert store.written is not None
    second_value = store.written[1]

    assert first_value != second_value


def test_rotate_secret_without_a_generate_spec_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "infisical", "value": "db/password"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="has no generate: spec"):
        rotate_secret(_context(root), "app", "DB_PASSWORD")


def test_rotate_secret_on_a_constant_secret_raises_usage_error(tmp_path):
    """A `constant`/`environment`/`github` secret can never carry a
    `generate:` spec (`validate_generate_not_on_builtin`) — the "no
    generate: spec" check alone already rejects every built-in store, with
    no separate builtin-store check needed (unlike `set_value()`)."""
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "constant", "value": "hunter2"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="has no generate: spec"):
        rotate_secret(_context(root), "app", "DB_PASSWORD")


def test_rotate_secret_on_a_feature_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", features=[{"key": "ENABLE_BETA", "store": "constant", "value": "true"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="rotation only applies to secrets"):
        rotate_secret(_context(root), "app", "ENABLE_BETA")


def test_rotate_secret_on_a_variable_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", variables=[{"key": "REGION", "store": "constant", "value": "westeurope"}])
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="rotation only applies to secrets"):
        rotate_secret(_context(root), "app", "REGION")


def test_rotate_secret_unknown_key_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _rotatable_environment(root)
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="'GHOST' is not declared"):
        rotate_secret(_context(root), "app", "GHOST")


def test_rotate_secret_unknown_deployment_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _rotatable_environment(root)
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="No deployment named 'ghost'"):
        rotate_secret(_context(root), "ghost", "DB_PASSWORD")


def test_rotate_secret_write_failure_is_reported_not_raised(tmp_path, monkeypatch):
    """Same D4 reasoning as `set_value()` — `IntegrationError` is not a
    `StrataError`, so a real write failure must be reported as a
    Diagnostic, not left to propagate uncaught."""
    root = _solution(tmp_path)
    _rotatable_environment(root)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeStoreThatRejectsWrites()
    )

    result, diagnostics = rotate_secret(context, "app", "DB_PASSWORD")

    assert result is None
    assert not diagnostics.ok
    assert "backend rejected the write" in diagnostics.messages()[0]


# ---------------------------------------------------------------------------
# secret_status() — docs/design/values-secrets-command.md Phase 8 (`values status`).
# ---------------------------------------------------------------------------


class _FakeStoreWithMetadata(StoreIntegration):
    """A `StoreIntegration` whose `.metadata()` returns a fixed, injected
    `SecretMetadata` (or `None`) — for `secret_status()` tests. Never
    implements `.set()` — `secret_status()` must never call it (D2)."""

    TYPE = "infisical"
    CAPABILITIES = frozenset({"secrets"})
    TRANSPORTS: frozenset[str] = frozenset()

    def __init__(self, metadata) -> None:
        super().__init__(None)
        self._metadata = metadata

    def resolve(self, key: str) -> str:
        return f"resolved-{key}"

    def metadata(self, key: str):
        return self._metadata


def _secret_with_rotate(root: Path, *, max_age: int = 90, policy: str = "warn", name: str = "prd") -> None:
    _write(
        root,
        f"environments/{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: {name}\nspec:\n"
        "  secrets:\n    - key: DB_PASSWORD\n      store: infisical\n      value: db/password\n"
        "      generate:\n        type: password\n        length: 20\n"
        f"      rotate:\n        max_age: {max_age}\n        policy: {policy}\n",
    )


def test_secret_status_ok_when_within_max_age(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _secret_with_rotate(root, max_age=90)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    from strata.integrations.resolved_context import SecretMetadata

    meta = SecretMetadata(updated_at=datetime.now(timezone.utc) - timedelta(days=10))
    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeStoreWithMetadata(meta)
    )

    rows, diagnostics = secret_status(context, "app")

    assert diagnostics.ok
    row = rows[0]
    assert row.key == "DB_PASSWORD"
    assert row.status == "ok"
    assert row.age_days == 10
    assert row.days_remaining == 80
    assert row.max_age == 90
    assert row.policy == "warn"


def test_secret_status_overdue_when_age_exceeds_max_age(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _secret_with_rotate(root, max_age=90)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    from strata.integrations.resolved_context import SecretMetadata

    meta = SecretMetadata(updated_at=datetime.now(timezone.utc) - timedelta(days=100))
    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeStoreWithMetadata(meta)
    )

    rows, diagnostics = secret_status(context, "app")

    assert not diagnostics.ok
    row = rows[0]
    assert row.status == "overdue"
    assert row.age_days == 100
    assert row.days_remaining is None
    assert "overdue" in diagnostics.messages()[0]


def test_secret_status_prefers_updated_at_over_created_at(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _secret_with_rotate(root, max_age=90)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    from strata.integrations.resolved_context import SecretMetadata

    meta = SecretMetadata(
        created_at=datetime.now(timezone.utc) - timedelta(days=200),
        updated_at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeStoreWithMetadata(meta)
    )

    rows, _diagnostics = secret_status(context, "app")

    assert rows[0].status == "ok"
    assert rows[0].age_days == 5


def test_secret_status_no_metadata_when_metadata_returns_none(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _secret_with_rotate(root)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeStoreWithMetadata(None)
    )

    rows, diagnostics = secret_status(context, "app")

    assert rows[0].status == "no_metadata"
    assert rows[0].age_days is None
    assert diagnostics.warnings
    assert diagnostics.ok  # a warning, not an error — doesn't fail the command


def test_secret_status_no_metadata_when_metadata_raises_integration_error(tmp_path, monkeypatch):
    """Uses the base `StoreIntegration.metadata()` default (raises
    `IntegrationError`, since `_FakeWritableStore` never overrides it)."""
    root = _solution(tmp_path)
    _secret_with_rotate(root)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    monkeypatch.setattr("strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeWritableStore())

    rows, diagnostics = secret_status(context, "app")

    assert rows[0].status == "no_metadata"
    assert diagnostics.warnings
    assert diagnostics.ok


def test_secret_status_no_timestamp_when_metadata_has_neither_field(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _secret_with_rotate(root)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    from strata.integrations.resolved_context import SecretMetadata

    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration", lambda *a, **k: _FakeStoreWithMetadata(SecretMetadata())
    )

    rows, diagnostics = secret_status(context, "app")

    assert rows[0].status == "no_timestamp"
    assert diagnostics.ok


def test_secret_status_no_integration_for_unregistered_store_type(tmp_path):
    root = _solution(tmp_path)
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  secrets:\n    - key: VAULT_SECRET\n      store: vault\n      value: kv/secret\n"
        "      generate:\n        type: password\n        length: 20\n"
        "      rotate:\n        max_age: 90\n        policy: warn\n",
    )
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = secret_status(_context(root), "app")

    assert rows[0].status == "no_integration"
    assert diagnostics.ok


def test_secret_status_skips_secrets_without_a_rotate_spec(tmp_path):
    root = _solution(tmp_path)
    _environment(root, "prd", secrets=[{"key": "DB_PASSWORD", "store": "constant", "value": "hunter2"}])
    _deployment(root, "app", environments=["prd"])

    rows, diagnostics = secret_status(_context(root), "app")

    assert rows == []
    assert diagnostics.ok


def test_secret_status_never_writes_anything(tmp_path, monkeypatch):
    """D2 — `values status` is a pure read; confirm the fake store's `set()`
    is never invoked (it isn't even implemented on `_FakeStoreWithMetadata`,
    so calling it would raise `AttributeError`/`TypeError`, not silently no-op)."""
    root = _solution(tmp_path)
    _secret_with_rotate(root)
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    from strata.integrations.resolved_context import SecretMetadata

    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration",
        lambda *a, **k: _FakeStoreWithMetadata(SecretMetadata(updated_at=datetime.now(timezone.utc))),
    )

    rows, _diagnostics = secret_status(context, "app")
    assert rows[0].status == "ok"  # reached metadata() successfully, never touched set()


def test_secret_status_rows_sorted_by_key(tmp_path, monkeypatch):
    root = _solution(tmp_path)
    _write(
        root,
        "environments/prd.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  secrets:\n"
        "    - key: ZEBRA\n      store: infisical\n      value: zebra\n"
        "      generate:\n        type: password\n        length: 20\n"
        "      rotate:\n        max_age: 90\n        policy: warn\n"
        "    - key: ALPHA\n      store: infisical\n      value: alpha\n"
        "      generate:\n        type: password\n        length: 20\n"
        "      rotate:\n        max_age: 90\n        policy: warn\n",
    )
    _deployment(root, "app", environments=["prd"])
    context = _context(root)
    from strata.integrations.resolved_context import SecretMetadata

    monkeypatch.setattr(
        "strata.controllers.value_controller.get_integration",
        lambda *a, **k: _FakeStoreWithMetadata(SecretMetadata(updated_at=datetime.now(timezone.utc))),
    )

    rows, _diagnostics = secret_status(context, "app")

    assert [row.key for row in rows] == ["ALPHA", "ZEBRA"]


def test_secret_status_unknown_deployment_raises_usage_error(tmp_path):
    root = _solution(tmp_path)
    _secret_with_rotate(root)
    _deployment(root, "app", environments=["prd"])

    with pytest.raises(UsageError, match="No deployment named 'ghost'"):
        secret_status(_context(root), "ghost")
