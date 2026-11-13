#!/usr/bin/env python3
"""attributeconfig on parameter sets (`base-parameters-v2.md` (work doc), phase 3, "Constraints: attributeconfig").

Two views, both needed because values are layered: `kind: parameterset, block: values, view: document` checks the
shape of what one set holds; `block: params, view: effective` checks what a deployment, environment or tenant
receives once its chain's sets are merged, with the roles as top-level keys.
"""

from pathlib import Path

from strata.controllers.solution_context import SolutionContext, open_solution
from strata.models.common_models import PlatformKind
from strata.services.attributeconfig_evaluator import evaluate_selector

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\n{meta}spec:\n"

#: The work doc's first example: the shape of every spoke's set, selected by its optional label.
SPOKE_SHAPE = (
    "  target:\n    kind: parameterset\n    block: values\n    view: document\n"
    "    selector: { meta.labels.role: spoke }\n"
    "  schema:\n    type: object\n    required: [name, hub, tenant]\n"
    "    properties:\n"
    '      name: { type: string, pattern: "^[a-z][a-z0-9]*-[0-9]{3}$" }\n'
    '      hub: { type: string, pattern: "^hub[0-9]{2}$" }\n'
)

#: The work doc's second example: what the bootstrap deployments receive, selected on the resolved deployment.
BOOTSTRAP_NEEDS = (
    "  target:\n    kind: deployment\n    block: params\n    view: effective\n"
    "    selector: { spec.workspace: main }\n"
    "  schema:\n    required: [spoke, hub]\n"
    "    properties:\n"
    "      spoke: { required: [name, hub, tenant] }\n"
    "      hub:\n        required: [provider]\n"
    "        properties:\n          provider: { enum: [p1, p2] }\n"
)


def _write(root: Path, kind: str, name: str, spec: str, meta: str = "") -> None:
    (root / f"{kind}-{name}.yaml").write_text(_HEADER.format(kind=kind, name=name, meta=meta) + spec, "utf-8")


def _open(tmp_path: Path, documents: list[tuple[str, str, str, str]]) -> SolutionContext:
    """A solution with a workspace, an environment and a tenant, plus the `(kind, name, spec, meta)` given."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s", meta="") + "  {}\n", "utf-8")
    for provider in ("p1", "p2"):
        _write(root, "provider", provider, "  type: local\n  region: local\n")
    _write(
        root,
        "workspace",
        "main",
        "  providers:\n    - p1\n  provisioners:\n    - name: tf\n      tool: terraform\n"
        "      source:\n        source_path: infra\n",
    )
    _write(root, "environment", "prd", "  {}\n")
    _write(root, "tenant", "acme", "  display_name: Acme\n")
    for kind, name, spec, meta in documents:
        _write(root, kind, name, spec, meta)
    context = open_solution(root)
    context.resolve()
    return context


def _spoke(name: str, values: str, role: str = "spoke") -> tuple[str, str, str, str]:
    return ("parameterset", name, f"  values: {values}\n", f"  labels: {{ role: {role} }}\n")


def _findings(context: SolutionContext, prefix: str = "") -> list[tuple[str, str]]:
    return [
        (finding.location or "", finding.message)
        for finding in context.diagnostics.errors
        if finding.code != "attributeconfig_invalid" and finding.message.startswith(prefix)
    ]


# ---------------------------------------------------------------------------
# One parameter set at a time: kind: parameterset, block: values, view: document
# ---------------------------------------------------------------------------


def test_the_shape_of_each_set_is_checked_on_the_set(tmp_path):
    documents = [
        ("attributeconfig", "spoke-values-shape", SPOKE_SHAPE, ""),
        _spoke("spoke-acme-001", "{ name: acme-001, hub: hub01, tenant: acme }"),
        _spoke("spoke-bad", "{ name: Bad, tenant: acme }"),
        _spoke("hub-hub01", "{ provider: p1 }", role="hub"),  # another role: not selected
    ]
    context = _open(tmp_path, documents)
    errors = [f for f in context.diagnostics.errors if f.source and f.source.endswith(".yaml")]
    assert {(f.source.rsplit("-", 1)[-1], f.location) for f in errors if f.source} == {
        ("bad.yaml", "spec.values"),  # hub is required
        ("bad.yaml", "spec.values.name"),  # the pattern
    }


def test_a_selector_walks_into_labels():
    class Meta:
        labels = {"role": "spoke"}

    class Document:
        meta = Meta()

    assert evaluate_selector(Document(), {"meta.labels.role": "spoke"})  # type: ignore[arg-type]
    assert not evaluate_selector(Document(), {"meta.labels.role": "hub"})  # type: ignore[arg-type]
    assert not evaluate_selector(Document(), {"meta.labels.missing": "spoke"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# What a consumer receives: block: params, view: effective
# ---------------------------------------------------------------------------

_BASE = (
    "deployment",
    "stack_base",
    "  partial: true\n  workspace: main\n  environments: [prd]\n  properties:\n    hub: ${param:spoke.hub}\n",
    "  params: { hub: stack-defaults }\n",
)


def _leaf(name: str, params: str) -> tuple[str, str, str, str]:
    return ("deployment", name, "  extends: stack_base\n", f"  params: {params}\n")


def test_merged_values_are_checked_per_deployment(tmp_path):
    documents = [
        ("attributeconfig", "bootstrap-needs", BOOTSTRAP_NEEDS, ""),
        ("parameterset", "stack-defaults", "  values: { provider: p1 }\n", ""),
        ("parameterset", "hub-west", "  values: { provider: p9 }\n", ""),
        _spoke("spoke-acme-001", "{ name: acme-001, hub: hub01, tenant: acme }"),
        _spoke("spoke-short", "{ name: short-001, hub: hub01 }"),
        _BASE,
        _leaf("acme", "{ spoke: spoke-acme-001 }"),  # hub from the base's defaults: passes
        _leaf("short", "{ spoke: spoke-short }"),  # no tenant
        _leaf("west", "{ spoke: spoke-acme-001, hub: hub-west }"),  # the leaf's hub set overrides the default
    ]
    context = _open(tmp_path, documents)
    found = _findings(context, "merged parameter values")
    assert sorted(location for location, _ in found) == ["meta.params.hub.provider", "meta.params.spoke"]
    messages = " | ".join(message for _, message in found)
    assert "deployment 'short'" in messages and "'tenant' is a required property" in messages
    assert "deployment 'west'" in messages and "'p9' is not one of" in messages
    assert "stack_base" not in messages  # a partial base receives no values of its own


def test_a_deployment_that_names_no_set_receives_no_values(tmp_path):
    documents = [
        ("attributeconfig", "bootstrap-needs", BOOTSTRAP_NEEDS, ""),
        ("deployment", "plain", "  workspace: main\n  environments: [prd]\n", ""),
    ]
    context = _open(tmp_path, documents)
    assert [message for _, message in _findings(context, "merged parameter values")] == [
        "merged parameter values of deployment 'plain' (attributeconfig 'bootstrap-needs'): "
        "'spoke' is a required property",
        "merged parameter values of deployment 'plain' (attributeconfig 'bootstrap-needs'): "
        "'hub' is a required property",
    ]


def test_environments_and_tenants_are_checked_resolved(tmp_path):
    needs_code = (
        "  target:\n    kind: {kind}\n    block: params\n    view: effective\n"
        "  schema:\n    required: [customer]\n"
        "    properties:\n      customer: {{ required: [code] }}\n"
    )
    documents = [
        ("attributeconfig", "env-needs", needs_code.format(kind="environment"), ""),
        ("attributeconfig", "tenant-needs", needs_code.format(kind="tenant"), ""),
        ("parameterset", "c1", "  values: { code: c1 }\n", ""),
        ("parameterset", "c2", "  values: { project: p2 }\n", ""),
        ("environment", "env_base", "  partial: true\n", "  params: { customer: c1 }\n"),
        ("environment", "env-c1", "  extends: env_base\n", ""),  # inherits the base's set: passes
        ("environment", "env-c2", "  {}\n", "  params: { customer: c2 }\n"),
        ("tenant", "initech", "  display_name: Initech\n", "  params: { customer: c2 }\n"),
    ]
    context = _open(tmp_path, documents)
    found = {message.split(" (")[0] for _, message in _findings(context, "merged parameter values")}
    # `prd` and `acme` (from the fixture) name no set, so they fail too; the partial base is never checked.
    assert found == {
        "merged parameter values of environment 'env-c2'",
        "merged parameter values of environment 'prd'",
        "merged parameter values of tenant 'initech'",
        "merged parameter values of tenant 'acme'",
    }
    index = context.controller.index
    assert index.get(PlatformKind.ENVIRONMENT, "env_base") is not None


def test_a_params_schema_matching_nothing_is_reported_unused(tmp_path):
    needs = BOOTSTRAP_NEEDS.replace("spec.workspace: main", "spec.workspace: nope")
    context = _open(tmp_path, [("attributeconfig", "bootstrap-needs", needs, "")])
    assert "attributeconfig_unused" in [finding.code for finding in context.diagnostics.warnings]
