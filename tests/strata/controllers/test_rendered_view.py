#!/usr/bin/env python3
"""The rendered view (`base-parameters-v2.md` (work doc), phase 4): a resolved document and the origin of each value.

The plan's test is "the origins of a two-base chain with two sets"; around it, each way a value can arrive: a scalar
from a base, a named list item overridden by the leaf, plain list items appended by two bases, a whole-value token
inserting a list, a deployment's tenant defaults, and an environment's stores, with secrets always masked.
"""

from pathlib import Path
from typing import Any

import pytest

from strata.controllers.rendered_view import SECRET_MASK, FieldOrigin, RenderedDocument, render_document
from strata.controllers.solution_context import open_solution
from strata.models.common_models import PlatformKind

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\n{meta}spec:\n"

#: Two bases and a leaf; the root base names a defaults set for `hub`, the leaf names `spoke` and overrides `hub`.
DOCUMENTS = [
    ("provider", "p1", "  type: local\n  region: local\n", ""),
    ("provider", "p2", "  type: local\n  region: local\n", ""),
    (
        "workspace",
        "main",
        "  providers:\n    - p1\n  provisioners:\n    - name: tf\n      tool: terraform\n"
        "      source:\n        source_path: infra\n",
        "",
    ),
    ("environment", "solution-env", "  {}\n", ""),
    ("environment", "hub-hub01-env", "  {}\n", ""),
    ("environment", "bootstrap-acme-001-env", "  {}\n", ""),
    ("tenant", "acme", "  display_name: Acme\n  properties: { owner: acme-team, tier: gold }\n", ""),
    ("parameterset", "stack-defaults", "  values: { provider: p1, region: westeurope }\n", ""),
    ("parameterset", "hub-hub01", "  values: { provider: p2 }\n", ""),
    (
        "parameterset",
        "spoke-acme-001",
        "  values: { name: acme-001, hub: hub01, tenant: acme, customers: [acmep01, acmep02] }\n",
        "",
    ),
    (
        "deployment",
        "stack_base",
        "  partial: true\n  workspace: main\n  tenant: ${param:spoke.tenant}\n"
        "  providers:\n    - { slot: p1, provider: p1 }\n"
        "  environments:\n    - solution-env\n    - hub-${param:spoke.hub}-env\n"
        "  properties:\n    region: ${param:hub.region}\n    tier: silver\n",
        "  params: { hub: stack-defaults }\n",
    ),
    (
        "deployment",
        "bootstrap_base",
        "  partial: true\n  extends: stack_base\n"
        "  environments:\n    - bootstrap-${param:spoke.name}-env\n"
        "  properties:\n    customers: ${param:spoke.customers}\n",
        "",
    ),
    (
        "deployment",
        "bootstrap-acme-001",
        "  extends: bootstrap_base\n  providers:\n    - { slot: p1, provider: '${param:hub.provider}' }\n",
        "  params: { hub: hub-hub01, spoke: spoke-acme-001 }\n",
    ),
]


def _open(tmp_path: Path, documents: list[tuple[str, str, str, str]]) -> Any:
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s", meta="") + "  {}\n", "utf-8")
    for kind, name, spec, meta in documents:
        (root / f"{kind}-{name}.yaml").write_text(_HEADER.format(kind=kind, name=name, meta=meta) + spec, "utf-8")
    return open_solution(root)


def _render(tmp_path: Path, kind: PlatformKind, name: str, documents: list[Any] = DOCUMENTS) -> RenderedDocument:
    context = _open(tmp_path, documents)
    assert context.ok, context.diagnostics
    rendered, diagnostics = render_document(context.controller.index, kind, name)
    assert rendered is not None, diagnostics
    return rendered


def _by_path(rendered: RenderedDocument) -> dict[str, FieldOrigin]:
    return {origin.path: origin for origin in rendered.fields}


def _reads(origin: FieldOrigin) -> list[tuple[str, str, Any, str]]:
    return [(read.role, read.key, read.value, read.parameter_set) for read in origin.reads]


def test_the_origins_of_a_two_base_chain_with_two_sets(tmp_path):
    rendered = _render(tmp_path, PlatformKind.DEPLOYMENT, "bootstrap-acme-001")
    assert rendered.chain == ("bootstrap-acme-001", "bootstrap_base", "stack_base")
    assert rendered.param_sets == {"hub": ("stack-defaults", "hub-hub01"), "spoke": ("spoke-acme-001",)}
    assert rendered.params["hub"] == {"provider": "p2", "region": "westeurope"}  # defaults, then the leaf's set
    fields = _by_path(rendered)

    workspace = fields["spec.workspace"]  # a plain value of the root base
    assert (workspace.value, workspace.document, workspace.written) == ("main", "stack_base", None)

    region = fields["spec.properties.region"]  # filled in from the root base's defaults set
    assert (region.value, region.document, region.written) == ("westeurope", "stack_base", "${param:hub.region}")
    assert _reads(region) == [("hub", "region", "westeurope", "stack-defaults")]

    provider = fields["spec.providers[0].provider"]  # a named item the leaf overrides, read from the leaf's set
    assert (provider.value, provider.document) == ("p2", "bootstrap-acme-001")
    assert _reads(provider) == [("hub", "provider", "p2", "hub-hub01")]
    assert fields["spec.providers[0].slot"].document == "bootstrap-acme-001"

    # Plain list items appended by two bases, each found in the base that wrote it.
    environments = {
        origin.value: (origin.document, origin.written)
        for path, origin in fields.items()
        if path.startswith("spec.environments")
    }
    assert environments["hub-hub01-env"] == ("stack_base", "hub-${param:spoke.hub}-env")
    assert environments["bootstrap-acme-001-env"] == ("bootstrap_base", "bootstrap-${param:spoke.name}-env")

    # A whole-value token inserting a list: each item carries the token that inserted it.
    customers = [fields[f"spec.properties.customers[{i}]"] for i in (0, 1)]
    assert [c.value for c in customers] == ["acmep01", "acmep02"]
    assert {c.written for c in customers} == {"${param:spoke.customers}"}


def test_tenant_defaults_are_the_bottom_layer(tmp_path):
    fields = _by_path(_render(tmp_path, PlatformKind.DEPLOYMENT, "bootstrap-acme-001"))
    owner = fields["spec.properties.owner"]
    assert (owner.value, owner.document_kind, owner.document) == ("acme-team", "tenant", "acme")
    tier = fields["spec.properties.tier"]  # the deployment chain wins over the tenant's default
    assert (tier.value, tier.document_kind, tier.document) == ("silver", "deployment", "stack_base")


ENVIRONMENTS = [
    ("parameterset", "c1", "  values: { code: c1 }\n", ""),
    (
        "environment",
        "env_base",
        "  partial: true\n"
        "  properties: { code: '${param:customer.code}', owner: platform }\n"
        "  variables:\n    - { key: sku, store: constant, value: S1, type: string }\n"
        "  secrets:\n    - { key: db, store: constant, value: hunter2 }\n",
        "",
    ),
    (
        "environment",
        "env-c1",
        "  extends: env_base\n"
        "  variables:\n    - { key: sku, store: environment, value: SKU }\n"
        "  secrets:\n    - { key: api, store: constant, value: '' }\n",
        "  params: { customer: c1 }\n",
    ),
]


def test_an_environment_with_secrets_always_masked(tmp_path):
    rendered = _render(tmp_path, PlatformKind.ENVIRONMENT, "env-c1", ENVIRONMENTS)
    fields = _by_path(rendered)
    assert fields["spec.properties.code"].written == "${param:customer.code}"
    assert fields["spec.properties.owner"].document == "env_base"
    sku = {path: origin for path, origin in fields.items() if path.startswith("spec.variables[0]")}
    assert {origin.document for origin in sku.values()} == {"env-c1"}  # the whole entry replaced: no stale type
    assert "spec.variables[0].type" not in fields
    secrets = [origin for path, origin in fields.items() if path.endswith(".value") and "secrets" in path]
    assert [origin.value for origin in secrets] == [SECRET_MASK, SECRET_MASK]  # empty or not
    assert [item["value"] for item in rendered.spec["secrets"]] == [SECRET_MASK, SECRET_MASK]
    assert "hunter2" not in repr(rendered)


def test_a_tenant(tmp_path):
    documents = [
        ("parameterset", "t1", "  values: { display_name: Initech, geo: [us] }\n", ""),
        (
            "tenant",
            "tenant_base",
            "  partial: true\n  display_name: ${param:tenant.display_name}\n  geographies: ${param:tenant.geo}\n",
            "",
        ),
        ("tenant", "initech", "  extends: tenant_base\n", "  params: { tenant: t1 }\n"),
    ]
    fields = _by_path(_render(tmp_path, PlatformKind.TENANT, "initech", documents))
    assert fields["spec.display_name"].value == "Initech"
    assert _reads(fields["spec.geographies[0]"]) == [("tenant", "geo", ["us"], "t1")]


@pytest.mark.parametrize(
    ("name", "code"),
    [("stack_base", "render_partial_base"), ("nope", "render_not_found")],
)
def test_nothing_to_render(tmp_path, name, code):
    context = _open(tmp_path, DOCUMENTS)
    rendered, diagnostics = render_document(context.controller.index, PlatformKind.DEPLOYMENT, name)
    assert rendered is None
    assert [finding.code for finding in diagnostics.errors] == [code]


def test_a_deployment_that_does_not_resolve(tmp_path):
    documents = [*DOCUMENTS[:-1], ("deployment", "broken", "  extends: bootstrap_base\n", "")]  # no sets named
    context = _open(tmp_path, documents)
    rendered, diagnostics = render_document(context.controller.index, PlatformKind.DEPLOYMENT, "broken")
    assert rendered is None
    codes = [finding.code for finding in diagnostics.errors]
    assert "param_missing" in codes and codes[-1] == "render_not_resolved"
