#!/usr/bin/env python3
"""`extends`, `partial` and `meta.params` on environments and tenants (`base-parameters-v2.md` (work doc), phase 2).

Environments and tenants are resolved when the solution loads, and the resolved document replaces the one in the
index, so every reader sees the merged document. The merge tables row by row, templates filled from parameter
sets, and the checks that keep a `partial: true` base from being used.
"""

from pathlib import Path
from typing import Any

import pytest

from strata.controllers.solution_context import SolutionContext, open_solution
from strata.models.common_models import PlatformKind
from strata.models.environment_model import EnvironmentModel, EnvironmentTemplateModel
from strata.models.tenant_model import TenantModel, TenantTemplateModel
from strata.services.environment_service import merge_environment_specs
from strata.services.tenant_service import merge_tenant_specs

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\n{meta}spec:\n"


def _write(root: Path, kind: str, name: str, spec: str, meta: str = "") -> None:
    (root / f"{kind}-{name}.yaml").write_text(_HEADER.format(kind=kind, name=name, meta=meta) + spec, "utf-8")


def _solution(tmp_path: Path, documents: list[tuple[str, str, str, str]]) -> Path:
    """A solution with a workspace and the `(kind, name, spec, meta)` documents given."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s", meta="") + "  {}\n", "utf-8")
    _write(root, "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "workspace",
        "main",
        "  providers:\n    - p1\n  provisioners:\n    - name: tf\n      tool: terraform\n"
        "      source:\n        source_path: infra\n",
    )
    for kind, name, spec, meta in documents:
        _write(root, kind, name, spec, meta)
    return root


def _open(tmp_path: Path, documents: list[tuple[str, str, str, str]]) -> SolutionContext:
    return open_solution(_solution(tmp_path, documents))


def _model(context: SolutionContext, kind: PlatformKind, name: str) -> Any:
    entry = context.controller.index.get(kind, name)
    assert entry is not None
    return entry.model


def _codes(context: SolutionContext) -> list[str]:
    return [finding.code or "" for finding in context.diagnostics.errors]


ENV_BASE = (
    "environment",
    "env_base",
    "  partial: true\n"
    "  properties:\n    network: { cidr: 10.0.0.0/16, dns: azure }\n    owner: platform\n"
    "  custom:\n    tags: { cost: shared }\n"
    "  variables:\n"
    "    - { key: region, store: constant, value: westeurope }\n"
    "    - { key: sku, store: constant, value: S1, type: string }\n"
    "  features:\n    - { key: enable_nsp, store: constant, value: true }\n",
    "",
)


# ---------------------------------------------------------------------------
# The merge tables, as functions
# ---------------------------------------------------------------------------


def test_environment_stores_merge_by_key_and_a_child_entry_replaces_the_whole_entry():
    base = {
        "partial": True,
        "variables": [
            {"key": "region", "store": "constant", "value": "westeurope"},
            {"key": "sku", "store": "constant", "value": "S1"},
        ],
        "secrets": [{"key": "db", "store": "constant", "value": "x"}],
    }
    child = {
        "extends": "env_base",
        "variables": [
            {"key": "sku", "store": "environment", "ref": "SKU"},  # moved store: no stale `value`
            {"key": "zone", "store": "constant", "value": "1"},
        ],
    }
    merged = merge_environment_specs(base, child)
    assert merged["variables"] == [
        {"key": "region", "store": "constant", "value": "westeurope"},
        {"key": "sku", "store": "environment", "ref": "SKU"},
        {"key": "zone", "store": "constant", "value": "1"},
    ]
    assert merged["secrets"] == base["secrets"]
    assert "partial" not in merged and "extends" not in merged


def test_environment_properties_and_custom_deep_merge_child_wins():
    merged = merge_environment_specs(
        {"properties": {"net": {"cidr": "a", "dns": "azure"}}, "custom": {"x": 1}},
        {"properties": {"net": {"cidr": "b"}}, "custom": {"y": 2}},
    )
    assert merged["properties"] == {"net": {"cidr": "b", "dns": "azure"}}
    assert merged["custom"] == {"x": 1, "y": 2}


def test_tenant_merge_table():
    base = {
        "partial": True,
        "display_name": "Base",
        "onboarded": "2026-01-01",
        "geographies": ["europe"],
        "environments": ["solution-env", "tenant-env"],
        "properties": {"profile": {"tenancy": "isolated", "audience": "customer"}},
        "configuration": {"sso": {"enabled": False, "provider": "entra"}},
        "custom": {"a": 1},
    }
    child = {
        "extends": "base",
        "display_name": "Initech",
        "geographies": ["us"],
        "environments": ["tenant-env", "initech-env"],
        "properties": {"profile": {"audience": "internal"}},
        "configuration": {"sso": {"enabled": True}},
        "custom": {"b": 2},
    }
    merged = merge_tenant_specs(base, child)
    assert merged["display_name"] == "Initech"
    assert merged["onboarded"] == "2026-01-01"  # the child sets none: the base's stays
    assert merged["geographies"] == ["us"]  # replaced, never joined: a data-residency limit
    assert merged["environments"] == ["solution-env", "tenant-env", "initech-env"]  # base first, still unique
    assert merged["properties"] == {"profile": {"tenancy": "isolated", "audience": "internal"}}
    assert merged["configuration"] == {"sso": {"enabled": True, "provider": "entra"}}
    assert merged["custom"] == {"a": 1, "b": 2}
    assert "partial" not in merged and "extends" not in merged


def test_a_child_without_geographies_keeps_the_bases():
    assert merge_tenant_specs({"geographies": ["europe"]}, {"display_name": "x"})["geographies"] == ["europe"]


# ---------------------------------------------------------------------------
# Loading: the resolved document replaces the one as written
# ---------------------------------------------------------------------------


def test_an_environment_leaf_is_resolved_into_the_index(tmp_path):
    leaf = (
        "environment",
        "prd",
        "  extends: env_base\n"
        "  properties:\n    network: { cidr: 10.1.0.0/16 }\n"
        "  variables:\n    - { key: sku, store: environment, value: SKU }\n",  # moved store: no stale `type`
        "",
    )
    context = _open(tmp_path, [ENV_BASE, leaf])
    assert context.ok, context.diagnostics
    prd = _model(context, PlatformKind.ENVIRONMENT, "prd")
    assert isinstance(prd, EnvironmentModel)
    assert prd.spec.extends is None and not prd.spec.partial
    assert prd.spec.properties == {"network": {"cidr": "10.1.0.0/16", "dns": "azure"}, "owner": "platform"}
    assert [(v.key, v.store.value, v.value, v.type) for v in prd.spec.variables or []] == [
        ("region", "constant", "westeurope", None),
        ("sku", "environment", "SKU", None),
    ]
    assert [f.key for f in prd.spec.features or []] == ["enable_nsp"]
    entry = context.controller.index.get(PlatformKind.ENVIRONMENT, "prd")
    assert entry is not None and entry.as_written.spec.extends == "env_base"  # type: ignore[attr-defined]
    # The base stays as written, so extends can find it.
    assert _model(context, PlatformKind.ENVIRONMENT, "env_base").spec.partial


def test_an_environment_base_reads_the_leafs_parameter_set(tmp_path):
    documents = [
        ("parameterset", "customer-acmep01", "  values: { code: acmep01, apim: true }\n", ""),
        (
            "environment",
            "deploy_env_base",
            "  partial: true\n  properties:\n    deploy_code: ${param:customer.code}\n"
            "  features:\n    - { key: enable_apim, store: constant, value: '${param:customer.apim}' }\n",
            "",
        ),
        (
            "environment",
            "deploy-acmep01-env",
            "  extends: deploy_env_base\n",
            "  params: { customer: customer-acmep01 }\n  name_rule: deploy-${param:customer.code}-env\n",
        ),
    ]
    context = _open(tmp_path, documents)
    assert context.ok, context.diagnostics
    assert isinstance(_model(context, PlatformKind.ENVIRONMENT, "deploy_env_base"), EnvironmentTemplateModel)
    leaf = _model(context, PlatformKind.ENVIRONMENT, "deploy-acmep01-env")
    assert isinstance(leaf, EnvironmentModel)
    assert leaf.spec.properties == {"deploy_code": "acmep01"}
    assert (leaf.spec.features or [])[0].value is True  # a whole-value token keeps its type


def test_a_tenant_built_on_a_template_base(tmp_path):
    """The tenant example of the work doc: display_name, a whole-value list and a configuration override."""
    documents = [
        ("parameterset", "tenant-initech", "  values: { display_name: Initech, dns_slug: initech, geo: [us] }\n", ""),
        (
            "tenant",
            "customer_tenant_base",
            "  partial: true\n  display_name: ${param:tenant.display_name}\n  geographies: ${param:tenant.geo}\n"
            "  properties:\n    deploy_profile: { tenancy: isolated, audience: customer }\n"
            "  configuration:\n    dns_slug: ${param:tenant.dns_slug}\n    sso: { enabled: false }\n",
            "",
        ),
        (
            "tenant",
            "initech",
            "  extends: customer_tenant_base\n  configuration:\n    sso: { enabled: true }\n",
            "  params: { tenant: tenant-initech }\n",
        ),
    ]
    context = _open(tmp_path, documents)
    # `us` is not a geography any provider region declares, which is checked later; loading is clean.
    assert not context.diagnostics.errors, context.diagnostics
    assert isinstance(_model(context, PlatformKind.TENANT, "customer_tenant_base"), TenantTemplateModel)
    initech = _model(context, PlatformKind.TENANT, "initech")
    assert isinstance(initech, TenantModel)
    assert initech.spec.display_name == "Initech"
    assert initech.spec.geographies == ["us"]
    assert initech.spec.configuration == {"dns_slug": "initech", "sso": {"enabled": True}}


def test_a_tenant_needs_a_display_name(tmp_path):
    context = _open(tmp_path, [("tenant", "acme", "  properties: { a: 1 }\n", "")])
    assert any("display_name" in finding.message for finding in context.diagnostics.errors)


def test_a_tenant_built_on_a_base_needs_a_display_name_once_merged(tmp_path):
    context = _open(
        tmp_path, [("tenant", "base", "  partial: true\n", ""), ("tenant", "acme", "  extends: base\n", "")]
    )
    finding = next(f for f in context.diagnostics.errors if "display_name" in f.message)
    assert "Tenant 'acme' as resolved (acme -> base)" in finding.message
    assert finding.source is not None and finding.source.endswith("tenant-acme.yaml")


def test_a_missing_value_names_the_base_field_and_the_leaf(tmp_path):
    documents = [
        ("parameterset", "c1", "  values: { code: c1 }\n", ""),
        ("environment", "base", "  partial: true\n  properties: { code: '${param:customer.nope}' }\n", ""),
        ("environment", "leaf", "  extends: base\n", "  params: { customer: c1 }\n"),
    ]
    context = _open(tmp_path, documents)
    finding = next(f for f in context.diagnostics.errors if f.code == "param_missing")
    assert finding.message == "base spec.properties.code: ${param:customer.nope} not found in customer: [c1] (for leaf)"
    assert finding.source is not None and finding.source.endswith("environment-base.yaml")


def test_an_environment_extends_cycle_is_a_load_error(tmp_path):
    documents = [("environment", "a", "  extends: b\n", ""), ("environment", "b", "  extends: a\n", "")]
    context = _open(tmp_path, documents)
    assert "circular_extends" in _codes(context)
    assert not context.ok


def test_an_environment_name_rule(tmp_path):
    documents = [
        ("parameterset", "c1", "  values: { code: c1 }\n", ""),
        ("environment", "base", "  partial: true\n", "  name_rule: deploy-${param:customer.code}-env\n"),
        ("environment", "deploy-c2-env", "  extends: base\n", "  params: { customer: c1 }\n"),
    ]
    context = _open(tmp_path, documents)
    finding = next(f for f in context.diagnostics.errors if f.code == "name_rule_mismatch")
    assert "Environment 'deploy-c2-env' must be named 'deploy-c1-env'" in finding.message


def test_an_unknown_base_is_reported_on_extends_once_references_are_checked(tmp_path):
    context = _open(tmp_path, [("environment", "leaf", "  extends: nope\n", "")])
    assert context.ok  # loading leaves the leaf as written
    context.resolve()
    finding = next(f for f in context.diagnostics.errors if f.code == "unknown_reference")
    assert finding.location == "spec.extends"


# ---------------------------------------------------------------------------
# Bases cannot be used
# ---------------------------------------------------------------------------

_DEPLOYMENT = "  workspace: main\n  environments: [{envs}]\n{tenant}"


def _deployment(envs: str, tenant: str = "") -> tuple[str, str, str, str]:
    return ("deployment", "d1", _DEPLOYMENT.format(envs=envs, tenant=f"  tenant: {tenant}\n" if tenant else ""), "")


def test_a_deployment_cannot_list_a_partial_environment(tmp_path):
    context = _open(tmp_path, [ENV_BASE, _deployment("env_base")])
    context.resolve()
    finding = next(f for f in context.diagnostics.errors if f.code == "environment_not_listable")
    assert finding.location == "spec.environments.0"
    assert "Deployment 'd1' lists environment 'env_base'" in finding.message


def test_a_tenant_cannot_list_a_partial_environment(tmp_path):
    tenant = ("tenant", "acme", "  display_name: Acme\n  environments: [env_base]\n", "")
    context = _open(tmp_path, [ENV_BASE, tenant])
    context.resolve()
    assert "environment_not_listable" in _codes(context)


def test_a_deployment_cannot_name_a_partial_tenant(tmp_path):
    documents = [
        ("environment", "prd", "  {}\n", ""),
        ("tenant", "tenant_base", "  partial: true\n", ""),
        _deployment("prd", tenant="tenant_base"),
    ]
    context = _open(tmp_path, documents)
    context.resolve()
    finding = next(f for f in context.diagnostics.errors if f.code == "tenant_not_selectable")
    assert finding.location == "spec.tenant"


def test_a_value_token_cannot_read_a_base(tmp_path):
    documents = [
        ENV_BASE,
        ("environment", "prd", "  extends: env_base\n", ""),
        ("environment", "reader", "  properties:\n    a: ${value:environment.env_base.spec.properties.owner}\n", ""),
        ("environment", "ok", "  properties:\n    a: ${value:environment.prd.spec.properties.owner}\n", ""),
    ]
    context = _open(tmp_path, documents)
    context.resolve()
    findings = [f for f in context.diagnostics.errors if f.code == "value_reference_to_base"]
    assert len(findings) == 1 and findings[0].source is not None
    assert findings[0].source.endswith("environment-reader.yaml")


# ---------------------------------------------------------------------------
# Every reader sees the resolved document
# ---------------------------------------------------------------------------


def test_a_deployment_reads_inherited_variables_and_tenant_defaults(tmp_path):
    documents = [
        ENV_BASE,
        ("environment", "prd", "  extends: env_base\n", ""),
        ("tenant", "tenant_base", "  partial: true\n  properties: { owner: tenant-team }\n", ""),
        ("tenant", "acme", "  extends: tenant_base\n  display_name: Acme\n", ""),
        (
            "deployment",
            "d1",
            "  workspace: main\n  tenant: acme\n  environments: [prd]\n"
            "  properties:\n    where: ${var:region}\n    flag: ${feature:enable_nsp}\n",
            "",
        ),
    ]
    context = _open(tmp_path, documents)
    context.resolve()
    assert context.ok, context.diagnostics
    tenant = _model(context, PlatformKind.TENANT, "acme")
    assert tenant.spec.properties == {"owner": "tenant-team"}


def test_a_set_read_by_an_environment_or_tenant_is_not_unused(tmp_path):
    documents = [
        ("parameterset", "c1", "  values: { code: c1, extra: x }\n", ""),
        ("environment", "base", "  partial: true\n  properties: { code: '${param:customer.code}' }\n", ""),
        ("environment", "leaf", "  extends: base\n", "  params: { customer: c1 }\n"),
    ]
    context = _open(tmp_path, documents)
    context.resolve()
    unused = [f.message for f in context.diagnostics.infos if f.code == "param_unused"]
    assert len(unused) == 1 and "value 'extra'" in unused[0]


@pytest.mark.parametrize("kind", ["environment", "tenant"])
def test_a_document_cannot_extend_itself(tmp_path, kind):
    spec = "  extends: self\n" + ("  display_name: x\n" if kind == "tenant" else "")
    context = _open(tmp_path, [(kind, "self", spec, "")])
    assert any("cannot extend itself" in finding.message for finding in context.diagnostics.errors)
