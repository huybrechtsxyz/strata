#!/usr/bin/env python3
"""The rendered view (docs/design/parameter-sets.md, "Rendered view"): a resolved document and the origin of each value.

The plan's test is "the origins of a two-base chain with two sets"; around it, each way a value can arrive: a scalar
from a base, a named list item overridden by the leaf, plain list items appended by two bases, a whole-value token
inserting a list, a deployment's tenant defaults, and an environment's stores, with secrets always masked.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from strata.controllers.deployment_resolution import resolve_deployments
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


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------


def test_tenant_defaults_name_the_tenant_document_that_wrote_them(tmp_path):
    """A value a tenant inherited through its own `extends`, or filled in from its own parameter set, names that
    document and set in the deployment's render, not the tenant the deployment selects."""
    documents = [
        *[doc for doc in DOCUMENTS if doc[:2] != ("tenant", "acme")],
        ("parameterset", "tenant-acme", "  values: { crm: crm-42 }\n", ""),
        (
            "tenant",
            "tenant_base",
            "  partial: true\n  environments: [solution-env]\n"
            "  properties: { owner: platform-team, crm: '${param:tenant.crm}' }\n",
            "",
        ),
        (
            "tenant",
            "acme",
            "  extends: tenant_base\n  display_name: Acme\n  properties: { tier: gold }\n",
            "  params: { tenant: tenant-acme }\n",
        ),
    ]
    fields = _by_path(_render(tmp_path, PlatformKind.DEPLOYMENT, "bootstrap-acme-001", documents))
    owner = fields["spec.properties.owner"]
    assert (owner.document_kind, owner.document) == ("tenant", "tenant_base")
    assert owner.source is not None and owner.source.endswith("tenant-tenant_base.yaml")
    crm = fields["spec.properties.crm"]
    assert (crm.document, crm.written) == ("tenant_base", "${param:tenant.crm}")
    assert _reads(crm) == [("tenant", "crm", "crm-42", "tenant-acme")]  # the tenant's set, not the deployment's
    assert fields["spec.properties.tier"].document == "stack_base"  # the deployment still wins over the tenant
    solution_env = next(origin for origin in fields.values() if origin.value == "solution-env")
    assert solution_env.document == "stack_base"  # listed by both: the later layer, the deployment's base, wins


def test_rendering_one_deployment_resolves_only_its_chain(tmp_path):
    """Another deployment's problems are neither reported nor paid for, and every finding about this one is."""
    documents = [
        *DOCUMENTS,
        ("deployment", "other", "  extends: bootstrap_base\n", "  params: { spoke: nope }\n"),  # an unknown set
        ("deployment", "broken", "  extends: bootstrap_base\n", ""),  # no sets named: param_missing
    ]
    context = _open(tmp_path, documents)
    index = context.controller.index
    resolved, found = resolve_deployments(index, names={"bootstrap-acme-001"})
    assert list(resolved) == ["bootstrap-acme-001"]
    assert found.items == []  # no param_unused either: that is about the whole solution

    rendered, diagnostics = render_document(index, PlatformKind.DEPLOYMENT, "broken")
    assert rendered is None
    messages = [finding.message for finding in diagnostics.errors]
    assert messages[:-1] and all("(for broken)" in message for message in messages[:-1])
    assert diagnostics.errors[-1].code == "render_not_resolved"


def test_an_environment_that_did_not_resolve_is_not_rendered_as_written(tmp_path):
    """A leaf with no token of its own stays as written in the index when its base's value is missing."""
    documents = [
        ("parameterset", "c1", "  values: { code: c1 }\n", ""),
        ("environment", "base", "  partial: true\n  properties: { code: '${param:customer.nope}' }\n", ""),
        ("environment", "leaf", "  extends: base\n  properties: { own: 1 }\n", "  params: { customer: c1 }\n"),
    ]
    context = _open(tmp_path, documents)
    assert "param_missing" in [finding.code for finding in context.diagnostics.errors]
    rendered, diagnostics = render_document(context.controller.index, PlatformKind.ENVIRONMENT, "leaf")
    assert rendered is None
    assert [finding.code for finding in diagnostics.errors] == ["render_not_resolved"]


def test_a_default_is_never_credited_to_a_document(tmp_path):
    documents = [
        *DOCUMENTS[:-1],
        (
            "deployment",
            "bootstrap-acme-001",
            "  extends: bootstrap_base\n  stages:\n    - step: tf\n  locking: { strategy: '${param:spoke.lock}' }\n",
            "  params: { hub: hub-hub01, spoke: spoke-acme-001 }\n",
        ),
    ]
    documents = [
        (
            kind,
            name,
            spec.replace("customers: [acmep01, acmep02]", "customers: [acmep01, acmep02], lock: delegate"),
            meta,
        )
        for kind, name, spec, meta in documents
    ]
    fields = _by_path(_render(tmp_path, PlatformKind.DEPLOYMENT, "bootstrap-acme-001", documents))
    assert fields["spec.stages[0].step"].document == "bootstrap-acme-001"
    assert fields["spec.stages[0].enabled"].document is None  # a schema default, written by nobody
    assert fields["spec.locking.strategy"].written == "${param:spoke.lock}"
    assert fields["spec.locking.enabled"].document is None


def test_a_date_value_renders_as_json_text(tmp_path):
    documents = [
        ("parameterset", "c1", "  values: { go_live: 2026-12-31 }\n", ""),
        ("environment", "base", "  partial: true\n  properties: { go_live: '${param:customer.go_live}' }\n", ""),
        ("environment", "leaf", "  extends: base\n", "  params: { customer: c1 }\n"),
    ]
    rendered = _render(tmp_path, PlatformKind.ENVIRONMENT, "leaf", documents)
    json.dumps(asdict(rendered))  # what `-o json` does: no TypeError on a date
    assert rendered.params == {"customer": {"go_live": "2026-12-31"}}
    assert _reads(_by_path(rendered)["spec.properties.go_live"]) == [("customer", "go_live", "2026-12-31", "c1")]


def test_a_secret_never_reads_a_parameter_set(tmp_path):
    documents = [
        ("parameterset", "c1", "  values: { db: hunter2 }\n", ""),
        (
            "environment",
            "leaf",
            "  secrets:\n    - { key: db, store: constant, value: '${param:customer.db}' }\n",
            "  params: { customer: c1 }\n",
        ),
    ]
    context = _open(tmp_path, documents)
    finding = next(f for f in context.diagnostics.errors if "spec.secrets[0].value reads a parameter set" in f.message)
    assert finding.source is not None and finding.source.endswith("environment-leaf.yaml")
