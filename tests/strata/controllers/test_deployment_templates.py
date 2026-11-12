#!/usr/bin/env python3
"""Deployment templates (`base-parameters-v2.md` (work doc), step 1c).

A deployment whose spec holds `${param:}` loads as a template: meta validated, spec as written. It is validated
per deployable deployment once the resolver substitutes the values, so `${param:}` works in typed and reference
fields (`tenant`, `environments`, `providers[].provider`, `layers.segments`), and a bad value is reported on the
leaf it was resolved for. Checks that read a deployment skip a template that did not resolve.
"""

from pathlib import Path

import pytest

from strata.controllers.artifact_usage import unused_artifacts
from strata.controllers.deployment_resolution import list_deployments, resolve_deployments
from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import resolve_deployment
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel, DeploymentTemplateModel
from strata.utils.errors import UsageError

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\n{meta}spec:\n"

BASE = (
    "  partial: true\n"
    "  workspace: main\n"
    "  tenant: ${param:spoke.tenant}\n"
    "  providers:\n    - slot: p1\n      provider: ${param:hub.provider}\n"
    "  layers:\n    segments:\n      hub: ${param:spoke.hub}\n      spoke: ${param:spoke.name}\n"
    "  environments:\n    - solution-env\n    - hub-${param:spoke.hub}-env\n"
    "  properties:\n    subscription: sub-${param:spoke.name}\n"
)


def _write(root: Path, name: str, kind: str, spec: str, meta: str = "") -> None:
    (root / f"{kind}-{name}.yaml").write_text(_HEADER.format(kind=kind, name=name, meta=meta) + spec, "utf-8")


def _solution(tmp_path: Path, *, base: str = BASE, leaves: bool = True) -> Path:
    """Two hubs and two spokes; one template base; one leaf per spoke naming the spoke's and its hub's sets."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s", meta="") + "  {}\n", "utf-8")
    for provider in ("p1", "p2", "p3"):
        _write(root, provider, "provider", "  type: local\n  region: local\n")
    _write(
        root,
        "main",
        "workspace",
        "  providers:\n    - p1\n  provisioners:\n    - name: tf\n      tool: terraform\n"
        "      source:\n        source_path: infra\n",
    )
    for environment in ("solution-env", "hub-hub01-env", "hub-hub02-env"):
        _write(root, environment, "environment", "  {}\n")
    for tenant in ("acme", "initech"):
        _write(root, tenant, "tenant", f"  display_name: {tenant.title()}\n  properties:\n    owner: {tenant}\n")
    _write(root, "hub-hub01", "parameterset", "  values: { provider: p2 }\n")
    _write(root, "hub-hub02", "parameterset", "  values: { provider: p3 }\n")
    _write(root, "spoke-acme", "parameterset", "  values: { name: acme-001, hub: hub01, tenant: acme }\n")
    _write(root, "spoke-initech", "parameterset", "  values: { name: initech-001, hub: hub02, tenant: initech }\n")
    _write(root, "base", "deployment", base)
    if leaves:
        for spoke, hub in (("acme", "hub01"), ("initech", "hub02")):
            meta = f"  params: {{ hub: hub-{hub}, spoke: spoke-{spoke} }}\n"
            _write(root, spoke, "deployment", "  extends: base\n", meta)
    return root


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_a_deployment_with_param_tokens_loads_as_a_template(tmp_path):
    context = open_solution(_solution(tmp_path))
    assert context.ok, context.diagnostics
    index = context.controller.index
    assert isinstance(index.get(PlatformKind.DEPLOYMENT, "base").model, DeploymentTemplateModel)  # type: ignore[union-attr]
    assert isinstance(index.get(PlatformKind.DEPLOYMENT, "acme").model, DeploymentModel)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("meta", "spec", "location"),
    [
        ("", "  extends: base-${param:spoke.hub}\n  tenant: ${param:spoke.tenant}\n", "spec.extends"),
        ("  params: { spoke: '${param:x}' }\n", "  extends: base\n", "meta.params.spoke"),
        ("", "  partial: ${param:spoke.partial}\n  tenant: ${param:spoke.tenant}\n", "spec.partial"),
    ],
)
def test_names_values_files_and_chains_are_never_templated(tmp_path, meta, spec, location):
    """Rule 5: `${param:}` is not allowed in `meta.name`, `meta.params`, `spec.extends` (or `spec.partial`)."""
    root = _solution(tmp_path, leaves=False)
    _write(root, "bad", "deployment", spec, meta)
    context = open_solution(root)
    assert any(finding.location and finding.location.startswith(location) for finding in context.diagnostics.errors), (
        context.diagnostics
    )


def test_a_malformed_token_in_a_template_is_a_load_error(tmp_path):
    root = _solution(tmp_path, leaves=False)
    _write(root, "bad", "deployment", "  partial: true\n  tenant: ${param:spoke.tenant}\n  kube_context: ${param:}\n")
    context = open_solution(root)
    (finding,) = context.diagnostics.errors
    assert "Malformed token" in finding.message and "spec.kube_context" in finding.message


# ---------------------------------------------------------------------------
# Typed and reference fields, resolved per leaf
# ---------------------------------------------------------------------------


def test_reference_fields_are_filled_from_the_values_and_the_solution_validates(tmp_path):
    context = open_solution(_solution(tmp_path)).require_valid()
    resolved, _ = resolve_deployments(context.controller.index)
    acme, initech = resolved["acme"].model, resolved["initech"].model
    assert (acme.spec.tenant, acme.spec.environments) == ("acme", ["solution-env", "hub-hub01-env"])
    assert [(p.slot, p.provider) for p in acme.spec.providers or []] == [("p1", "p2")]
    assert acme.spec.layers is not None and acme.spec.layers.segments == {"hub": "hub01", "spoke": "acme-001"}
    assert (initech.spec.tenant, initech.spec.environments) == ("initech", ["solution-env", "hub-hub02-env"])
    assert [(p.slot, p.provider) for p in initech.spec.providers or []] == [("p1", "p3")]
    # Tenant defaults (ADR-0024) fold in from the substituted tenant.
    assert acme.spec.properties == {"owner": "acme", "subscription": "sub-acme-001"}


def test_an_unknown_environment_after_substitution_is_reported_on_the_leaf(tmp_path):
    root = _solution(tmp_path)
    (root / "environment-hub-hub02-env.yaml").unlink()
    found = open_solution(root).resolve()
    (finding,) = [f for f in found.errors if f.code == "unknown_reference"]
    assert finding.source is not None and finding.source.endswith("deployment-initech.yaml")
    assert finding.location == "spec.environments.1"
    assert "unknown environment 'hub-hub02-env'" in finding.message
    assert "in deployment 'initech' after ${param:} substitution" in finding.message


def test_an_unknown_tenant_after_substitution_is_reported_not_silently_skipped(tmp_path):
    root = _solution(tmp_path)
    (root / "parameterset-spoke-initech.yaml").write_text(
        _HEADER.format(kind="parameterset", name="spoke-initech", meta="")
        + "  values: { name: initech-001, hub: hub02, tenant: initek }\n",
        "utf-8",
    )
    found = open_solution(root).resolve()
    assert [(f.location, f.code) for f in found.errors] == [("spec.tenant", "unknown_reference")]
    assert "unknown tenant 'initek'" in found.errors[0].message


def test_a_value_the_model_rejects_is_reported_on_the_template_for_the_leaf(tmp_path):
    root = _solution(tmp_path)
    (root / "parameterset-spoke-initech.yaml").write_text(
        _HEADER.format(kind="parameterset", name="spoke-initech", meta="")
        + "  values: { name: initech-001, hub: hub02, tenant: Initech Corp }\n",
        "utf-8",
    )
    found = open_solution(root).resolve()
    (finding,) = found.errors
    assert finding.source is not None and finding.source.endswith("deployment-base.yaml")
    assert finding.location == "spec.tenant"
    assert finding.message.startswith("base spec.tenant: invalid after ${param:} substitution:")
    assert finding.message.endswith("(for initech)")


def test_a_leaf_can_be_a_template_itself(tmp_path):
    root = _solution(tmp_path, leaves=False)
    _write(
        root,
        "solo",
        "deployment",
        "  workspace: main\n  tenant: ${param:spoke.tenant}\n"
        "  environments: [solution-env, 'hub-${param:spoke.hub}-env']\n",  # in a flow list, `{` needs quotes
        "  params: { spoke: spoke-acme }\n",
    )
    context = open_solution(root).require_valid()
    resolved, _ = resolve_deployments(context.controller.index)
    assert resolved["solo"].model.spec.environments == ["solution-env", "hub-hub01-env"]


def test_a_template_base_without_leaves_is_not_a_finding(tmp_path):
    context = open_solution(_solution(tmp_path, leaves=False))
    assert context.resolve().ok
    assert context.ok


# ---------------------------------------------------------------------------
# Consumers: a template base is a partial base, a template leaf is its resolved document
# ---------------------------------------------------------------------------


def test_deploy_list_shows_a_template_base_as_written_and_a_leaf_resolved(tmp_path):
    context = open_solution(_solution(tmp_path)).require_valid()
    rows = {row.name: row for row in list_deployments(context.controller.index)}
    assert rows["base"].partial and rows["base"].tenant == "${param:spoke.tenant}"
    assert rows["acme"].tenant == "acme" and rows["acme"].environments == ("solution-env", "hub-hub01-env")
    deployable = list_deployments(context.controller.index, deployable=True, tenant="initech")
    assert [row.name for row in deployable] == ["initech"]


def test_a_command_resolves_a_template_leaf_and_refuses_a_template_base(tmp_path):
    context = open_solution(_solution(tmp_path)).require_valid()
    assert resolve_deployment(context, "acme").spec.tenant == "acme"
    with pytest.raises(UsageError, match="partial base that reads parameter sets"):
        resolve_deployment(context, "base")


def test_unused_artifacts_still_works_with_a_template_base(tmp_path):
    root = _solution(tmp_path)
    _write(root, "orphan", "artifact", "  image_name: example/orphan\n")
    context = open_solution(root).require_valid()
    assert unused_artifacts(context) == ["orphan"]
