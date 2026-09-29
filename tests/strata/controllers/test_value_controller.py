#!/usr/bin/env python3
"""Tests for `value_controller.resolve_values` — dispatch, precedence, and merge order."""

from pathlib import Path

import pytest

from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import (
    build_value_references,
    merge_workspace_environment_deployment_properties,
    reachable_environments,
    resolve_artifact,
    resolve_artifact_field,
    resolve_deployment,
    resolve_document_value_references,
    resolve_tenant,
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
