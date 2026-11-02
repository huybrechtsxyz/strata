#!/usr/bin/env python3
"""Real-terraform regression test for Phase 6 of docs/design/
unified-terraform-projection.md (ADR-0035) — `build_strata_locals()`'s
generated `strata_locals.tf`, now wired into `TerraformIntegration.
default_output()` itself (the gap found during the requirements-
traceability pass against ADR-0035's own decision text, which commits to
`build run` emitting both generated files). Exercises the real
`default_output()`/`rewrite_strata_payload()` call sites directly, not a
hand-assembled stand-in for them.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.terraform import TerraformIntegration
from strata.integrations.terraform_projection import build_strata_payload, rewrite_strata_payload
from strata.models.common_models import SourceModel
from strata.models.dns_model import DnsMetaModel, DnsModel, DnsRecordModel, DnsSpecModel, DnsZoneModel
from strata.models.provider_model import ProviderMetaModel, ProviderModel, ProviderSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel

OUTPUTS_TF = """
output "api_token" {
  value     = local.strata.providers.kamatera.configuration.api_token
  sensitive = true
}

output "region" {
  value = local.strata.providers.kamatera.region
}
"""

DNS_OUTPUTS_TF = """
output "dns_secret" {
  value     = local.strata.dns["public-dns"].zones["example.com"].records[0].value
  sensitive = true
}
"""


def _terraform_available() -> bool:
    return shutil.which("terraform") is not None


def _graph_with_a_provider_secret() -> ResolvedWorkspaceGraph:
    """One provider whose own `configuration` carries a `${secret:}` token
    - a real, full payload (every top-level key present, matching the
    generated type's own strict contract), not a hand-rolled partial dict.
    """
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["kamatera"],
            provisioners=[ProvisionerModel(name="tf", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    provider = ProviderModel(
        meta=ProviderMetaModel(name="kamatera"),
        spec=ProviderSpecModel(
            type="kamatera",
            region="eu",
            configuration={"api_token": "${secret:API_TOKEN}"},
        ),
    )
    return ResolvedWorkspaceGraph(workspace=workspace, providers={"kamatera": provider})


@pytest.mark.skipif(not _terraform_available(), reason="terraform not on PATH")
def test_local_strata_resolves_secret_via_strata_secrets_and_literal_otherwise(tmp_path: Path):
    root = tmp_path
    graph = _graph_with_a_provider_secret()
    integration = TerraformIntegration()

    # No existing `.tf` sources yet when `default_output()` runs, matching
    # a real `build run`: scan finds nothing, so both `strata_variables.tf`
    # (Phase 5) and `strata_locals.tf` (Phase 6) are generated, alongside
    # the build-time `strata.auto.tfvars.json`.
    default_output = integration.default_output(root, None, None, graph)  # type: ignore[arg-type]
    (root / "strata_variables.tf").write_text(default_output["strata_variables.tf"], encoding="utf-8")
    (root / "strata_locals.tf").write_text(default_output["strata_locals.tf"], encoding="utf-8")
    (root / "outputs.tf").write_text(OUTPUTS_TF, encoding="utf-8")

    build_time_payload = build_strata_payload(graph)

    # Phase 3: deploy-time rewrite - resolves everything, sentinels the
    # secret leaf, and returns the real value separately.
    resolved, secrets = rewrite_strata_payload(build_time_payload, {"API_TOKEN": "super-secret-value"})
    (root / "strata.auto.tfvars.json").write_text(json.dumps({"strata": resolved}), encoding="utf-8")

    env = {**os.environ, "TF_VAR_strata_secrets": json.dumps(secrets)}
    subprocess.run(["terraform", "init", "-input=false"], cwd=root, env=env, capture_output=True, text=True, check=True)
    subprocess.run(
        ["terraform", "plan", "-input=false", "-out=tfplan"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    shown = subprocess.run(
        ["terraform", "show", "-json", "tfplan"], cwd=root, env=env, capture_output=True, text=True, check=True
    )
    outputs = json.loads(shown.stdout)["planned_values"]["outputs"]

    # local.strata's merge resolves the secret path to its real value, not
    # the sentinel var.strata itself carries.
    assert outputs["api_token"]["value"] == "super-secret-value"
    # An ordinary, non-secret sibling resolves to its plain literal value,
    # read through the exact same `local.strata.*` syntax.
    assert outputs["region"]["value"] == "eu"


def _graph_with_a_dotted_key_list_secret() -> ResolvedWorkspaceGraph:
    """A DNS zone named `example.com` (a real key containing a literal
    `.`) with a `${secret:}` inside `records`, a list — the exact real
    shape that found both the list-vs-merge() bug and the lookup-key
    double-escaping bug this test module exists to guard against."""
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["kamatera"],
            provisioners=[ProvisionerModel(name="tf", tool="terraform", source=SourceModel(source_path="infra"))],
            dns_zones=["public-dns"],
        ),
    )
    provider = ProviderModel(
        meta=ProviderMetaModel(name="kamatera"),
        spec=ProviderSpecModel(type="kamatera", region="eu"),
    )
    dns = DnsModel(
        meta=DnsMetaModel(name="public-dns"),
        spec=DnsSpecModel(
            provider="cloudflare",
            zones=[
                DnsZoneModel(
                    name="example.com",
                    records=[DnsRecordModel(name="@", type="A", value="${secret:DNS_SECRET}")],
                )
            ],
        ),
    )
    return ResolvedWorkspaceGraph(workspace=workspace, providers={"kamatera": provider}, dns={"public-dns": dns})


@pytest.mark.skipif(not _terraform_available(), reason="terraform not on PATH")
def test_local_strata_resolves_a_secret_reached_through_a_dotted_key_and_a_list_index(tmp_path: Path):
    root = tmp_path
    graph = _graph_with_a_dotted_key_list_secret()
    integration = TerraformIntegration()

    default_output = integration.default_output(root, None, None, graph)  # type: ignore[arg-type]
    (root / "strata_variables.tf").write_text(default_output["strata_variables.tf"], encoding="utf-8")
    (root / "strata_locals.tf").write_text(default_output["strata_locals.tf"], encoding="utf-8")
    (root / "outputs.tf").write_text(DNS_OUTPUTS_TF, encoding="utf-8")

    build_time_payload = build_strata_payload(graph)
    resolved, secrets = rewrite_strata_payload(build_time_payload, {"DNS_SECRET": "dns-secret-value"})
    (root / "strata.auto.tfvars.json").write_text(json.dumps({"strata": resolved}), encoding="utf-8")

    env = {**os.environ, "TF_VAR_strata_secrets": json.dumps(secrets)}
    subprocess.run(["terraform", "init", "-input=false"], cwd=root, env=env, capture_output=True, text=True, check=True)
    subprocess.run(
        ["terraform", "plan", "-input=false", "-out=tfplan"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    shown = subprocess.run(
        ["terraform", "show", "-json", "tfplan"], cwd=root, env=env, capture_output=True, text=True, check=True
    )
    outputs = json.loads(shown.stdout)["planned_values"]["outputs"]

    # Proves the lookup key HCL decodes to is the real secrets-map key,
    # not an over- or under-escaped near-miss that silently falls back to
    # the sentinel instead.
    assert outputs["dns_secret"]["value"] == "dns-secret-value"
