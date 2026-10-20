#!/usr/bin/env python3
"""Real-terraform regression test for Phase 5 of docs/design/
unified-terraform-projection.md (ADR-0035) — `strata_variables.tf`'s
generated `object({...})` type. A stubbed test can confirm the Python side
produces valid JSON, but only a real `terraform validate`/`plan` can prove
the HCL type actually accepts the real shape `build_strata_payload()`
produces — including the nullable-but-always-present fields and the one
real exception (`tenant` collapsing to a bare `{}`) found while authoring
the template.
"""

import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from strata.integrations.resolved_context import ResolvedAssetInfo, ResolvedWorkspaceGraph
from strata.integrations.terraform import TerraformIntegration
from strata.integrations.terraform_projection import build_strata_payload
from strata.models.common_models import ModuleReferenceModel, SourceModel
from strata.models.dns_model import DnsMetaModel, DnsModel, DnsRecordModel, DnsSpecModel, DnsZoneModel
from strata.models.firewall_model import FirewallMetaModel, FirewallModel, FirewallSpecModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModuleSpecModel
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
from strata.models.network_model import (
    NetworkDefinitionModel,
    NetworkMetaModel,
    NetworkModel,
    NetworkSpecModel,
    SubnetModel,
)
from strata.models.provider_model import ProviderMetaModel, ProviderModel, ProviderSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.resource_model import (
    ResourceDiskModel,
    ResourceMetaModel,
    ResourceModel,
    ResourceSpecModel,
    ResourceStorageModel,
)
from strata.models.tenant_model import TenantMetaModel, TenantModel, TenantSpecModel
from strata.models.workspace_model import (
    TopologyComponentModel,
    TopologyNamespaceReferenceModel,
    TopologySpecModel,
    WorkspaceMetaModel,
    WorkspaceModel,
    WorkspaceResourceModel,
    WorkspaceSpecModel,
)


def _terraform_available() -> bool:
    return shutil.which("terraform") is not None


def _full_graph() -> ResolvedWorkspaceGraph:
    """One real document per category — enough to exercise every field the
    generated type declares, including the ones that are nullable (`role`,
    `subcategory`, `subnet`, `storage`, provider `display_name`)."""
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p1"],
            provisioners=[ProvisionerModel(name="tf", tool="terraform", source=SourceModel(source_path="infra"))],
            topology=[
                TopologySpecModel(
                    name="main",
                    type="kubernetes",
                    components=[TopologyComponentModel(resource="r1")],
                    namespaces=[TopologyNamespaceReferenceModel(namespace="hearth")],
                )
            ],
            resources=[WorkspaceResourceModel(name="r1", resource="r1", firewalls=["fw1"])],
            namespaces=["hearth"],
            firewalls=["fw1"],
            dns_zones=["zone1"],
            networks=["net1"],
            default_tags={"managed-by": "strata"},
        ),
    )
    provider = ProviderModel(
        meta=ProviderMetaModel(name="p1"),
        spec=ProviderSpecModel(type="azurerm", region="westeurope"),
    )
    resource = ResourceModel(
        meta=ResourceMetaModel(name="r1"),
        spec=ResourceSpecModel(
            provider_type="azurerm",
            resource_type="virtual_machine",
            storage=ResourceStorageModel(disks=[ResourceDiskModel(name="os", size=64, label="os-disk", mount="/data")]),
        ),
    )
    namespace = NamespaceModel(
        meta=NamespaceMetaModel(name="hearth"),
        spec=NamespaceSpecModel(modules=[ModuleReferenceModel(name="auth", module="authentik")]),
    )
    module = ModuleModel(
        meta=ModuleMetaModel(name="authentik"),
        spec=ModuleSpecModel(source=SourceModel(source_path="modules/authentik"), type="helm"),
    )
    firewall = FirewallModel(meta=FirewallMetaModel(name="fw1"), spec=FirewallSpecModel())
    dns = DnsModel(
        meta=DnsMetaModel(name="zone1"),
        spec=DnsSpecModel(
            provider="cloudflare",
            zones=[DnsZoneModel(name="example.com", records=[DnsRecordModel(name="@", type="A", value="1.2.3.4")])],
        ),
    )
    network = NetworkModel(
        meta=NetworkMetaModel(name="net1"),
        spec=NetworkSpecModel(
            networks=[
                NetworkDefinitionModel(
                    name="vnet", address_space=["10.0.0.0/16"], subnets=[SubnetModel(name="aks", cidr="10.0.1.0/24")]
                )
            ]
        ),
    )
    tenant = TenantModel(meta=TenantMetaModel(name="acme"), spec=TenantSpecModel(display_name="Acme"))
    return ResolvedWorkspaceGraph(
        workspace=workspace,
        providers={"p1": provider},
        resources={"r1": resource},
        namespaces={"hearth": namespace},
        modules={"authentik": module},
        firewalls={"fw1": firewall},
        dns={"zone1": dns},
        networks={"net1": network},
        tenant=tenant,
    )


def _minimal_graph() -> ResolvedWorkspaceGraph:
    """No tenant, no resources, no optional categories at all — the edge
    case that found `tenant` needing every field `optional()` (the whole
    sub-object collapses to a bare `{}` here)."""
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p1"],
            provisioners=[ProvisionerModel(name="tf", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    provider = ProviderModel(
        meta=ProviderMetaModel(name="p1"),
        spec=ProviderSpecModel(type="azurerm", region="westeurope"),
    )
    return ResolvedWorkspaceGraph(workspace=workspace, providers={"p1": provider})


def _validate_payload_against_generated_type(tmp_path: Path, graph: ResolvedWorkspaceGraph) -> None:
    root = tmp_path
    integration = TerraformIntegration()
    output = integration.default_output(root, None, None, graph)  # type: ignore[arg-type]
    assert "strata_variables.tf" in output, 'no existing variable "strata" declaration here — must be generated'
    (root / "strata_variables.tf").write_text(output["strata_variables.tf"], encoding="utf-8")
    (root / "strata.auto.tfvars.json").write_text(output["strata.auto.tfvars.json"], encoding="utf-8")

    subprocess.run(["terraform", "init", "-input=false"], cwd=root, capture_output=True, text=True, check=True)
    validated = subprocess.run(["terraform", "validate", "-json"], cwd=root, capture_output=True, text=True)
    result = json.loads(validated.stdout)
    assert result["valid"], f"terraform validate failed: {validated.stdout}\n{validated.stderr}"
    subprocess.run(
        ["terraform", "plan", "-input=false"],
        cwd=root,
        env={**os.environ, "TF_VAR_strata_secrets": "{}"},
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.skipif(not _terraform_available(), reason="terraform not on PATH")
def test_generated_type_accepts_a_fully_populated_strata_payload(tmp_path: Path):
    _validate_payload_against_generated_type(tmp_path, _full_graph())


@pytest.mark.skipif(not _terraform_available(), reason="terraform not on PATH")
def test_generated_type_accepts_a_minimal_payload_with_no_tenant(tmp_path: Path):
    """The `tenant` edge case: `build_strata_payload()` returns a bare `{}`
    for `tenant` when no tenant is referenced — found while authoring the
    template, every `tenant.*` field had to become `optional()` as a
    result. This is the empirical proof that decision is correct."""
    payload = build_strata_payload(_minimal_graph())
    assert payload["tenant"] == {}
    _validate_payload_against_generated_type(tmp_path, _minimal_graph())


# ---------------------------------------------------------------------------
# Parity: every attribute the payload emits is declared by the packaged type.
# Terraform silently drops an attribute an `object` type does not declare, so
# the real-terraform tests above cannot notice one that is missing.
# ---------------------------------------------------------------------------

_TYPE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[(){}=,]")


def _tokens_of_strata_type() -> list[str]:
    text = TerraformIntegration._STRATA_VARIABLES_TEMPLATE.read_text(encoding="utf-8")
    text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    start = text.index("type = object(", text.index('variable "strata" {'))
    return _TYPE_TOKEN.findall(text[start + len("type = ") :])


def _parse_type(tokens: list[str], i: int) -> tuple[Any, int]:
    """One HCL type expression -> ("leaf",) | ("object", {name: shape}) |
    ("map"|"list", shape) | ("optional", shape). Only the subset the template uses."""
    kind = tokens[i]
    if i + 1 >= len(tokens) or tokens[i + 1] != "(":
        return ("leaf",), i + 1
    i += 2  # ident and "("
    if kind == "object":
        assert tokens[i] == "{"
        i += 1
        attrs: dict[str, Any] = {}
        while tokens[i] != "}":
            name = tokens[i]
            assert tokens[i + 1] == "=", f"expected '=' after {name!r}"
            attrs[name], i = _parse_type(tokens, i + 2)
        shape: Any = ("object", attrs)
        i += 1  # "}"
    else:
        inner, i = _parse_type(tokens, i)
        shape = (kind, inner)
        if kind == "optional" and tokens[i] == ",":  # skip the default value, which may be `{}`
            depth = 0
            while depth > 0 or tokens[i] != ")":
                depth += tokens[i] in "({"
                depth -= tokens[i] in ")}"
                i += 1
    assert tokens[i] == ")"
    return shape, i + 1


def _compare_with_shape(value: Any, shape: Any, path: str, problems: list[str]) -> None:
    optional = shape[0] == "optional"
    if optional:
        shape = shape[1]
    if shape[0] == "map" and isinstance(value, dict):
        for key, item in value.items():
            _compare_with_shape(item, shape[1], f"{path}[{key!r}]", problems)
    elif shape[0] == "list" and isinstance(value, list):
        for index, item in enumerate(value):
            _compare_with_shape(item, shape[1], f"{path}[{index}]", problems)
    elif shape[0] == "object":
        if not isinstance(value, dict):
            problems.append(f"{path}: type declares an object, payload has {type(value).__name__}")
            return
        attrs = shape[1]
        for name in sorted(value.keys() - attrs.keys()):
            problems.append(f"{path}.{name}: emitted by the payload but not declared by the type")
        for name in sorted(attrs.keys() - value.keys()):
            if attrs[name][0] != "optional":
                problems.append(f"{path}.{name}: declared required by the type but not emitted")
        for name in sorted(attrs.keys() & value.keys()):
            _compare_with_shape(value[name], attrs[name], f"{path}.{name}", problems)


def test_packaged_type_declares_every_attribute_the_payload_emits():
    graph = replace(
        _full_graph(),
        resolved_assets={"api": ResolvedAssetInfo(path="assets/api", asset="api-doc", files=["api.json"])},
    )
    payload = build_strata_payload(graph)
    shape, _ = _parse_type(_tokens_of_strata_type(), 0)

    problems: list[str] = []
    _compare_with_shape(payload, shape, "strata", problems)

    assert not problems, "\n".join(problems)


@pytest.mark.skipif(not _terraform_available(), reason="terraform not on PATH")
def test_terraform_can_read_the_bound_provider_document_name(tmp_path: Path):
    """The D-B symptom itself: an attribute missing from the type reads as an error in HCL."""
    output = TerraformIntegration().default_output(tmp_path, None, None, _full_graph())  # type: ignore[arg-type]
    for name in ("strata.auto.tfvars.json", "strata_variables.tf"):
        (tmp_path / name).write_text(output[name], encoding="utf-8")
    (tmp_path / "main.tf").write_text(
        'output "bound" {\n  value = var.strata.providers["p1"].provider\n}\n', encoding="utf-8"
    )
    env = {**os.environ, "TF_VAR_strata_secrets": "{}"}

    subprocess.run(["terraform", "init", "-input=false"], cwd=tmp_path, capture_output=True, text=True, check=True)
    subprocess.run(
        ["terraform", "apply", "-auto-approve", "-input=false"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    read = subprocess.run(
        ["terraform", "output", "-raw", "bound"], cwd=tmp_path, capture_output=True, text=True, check=True
    )

    assert read.stdout == "p1"


@pytest.mark.skipif(not _terraform_available(), reason="terraform not on PATH")
def test_generated_strata_assets_local_reads_the_files_from_the_step_directory(tmp_path: Path):
    """`local.strata_assets` is what a Terraform author uses; prove with a real
    Terraform, run from a step directory two levels below the build root, that it
    reaches the materialised files without the author writing any path arithmetic."""
    build_root = tmp_path / "build"
    (build_root / "assets" / "api" / "nested").mkdir(parents=True)
    (build_root / "assets" / "api" / "api.json").write_text('{"a": 1}', encoding="utf-8")
    (build_root / "assets" / "api" / "nested" / "deep.json").write_text("{}", encoding="utf-8")
    step_dir = build_root / "terraform" / "apim"
    step_dir.mkdir(parents=True)
    graph = replace(
        _full_graph(),
        resolved_assets={
            "api": ResolvedAssetInfo(path="assets/api", asset="api-doc", files=["api.json", "nested/deep.json"])
        },
        step_root="terraform/apim",
    )
    for name, content in TerraformIntegration().default_output(step_dir, None, None, graph).items():  # type: ignore[arg-type]
        (step_dir / name).write_text(content, encoding="utf-8")
    (step_dir / "main.tf").write_text(
        'output "content" {\n  value = file(local.strata_assets.api.files["api.json"])\n}\n'
        'output "count" {\n  value = length(fileset(local.strata_assets.api.dir, "**/*.json"))\n}\n',
        encoding="utf-8",
    )
    env = {**os.environ, "TF_VAR_strata_secrets": "{}"}

    subprocess.run(["terraform", "init", "-input=false"], cwd=step_dir, capture_output=True, text=True, check=True)
    subprocess.run(
        ["terraform", "apply", "-auto-approve", "-input=false"],
        cwd=step_dir,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    read = subprocess.run(["terraform", "output", "-json"], cwd=step_dir, capture_output=True, text=True, check=True)

    outputs = json.loads(read.stdout)
    assert outputs["content"]["value"] == '{"a": 1}'
    assert outputs["count"]["value"] == 2


def test_parity_check_notices_an_undeclared_attribute():
    """Guards the guard: the comparison must actually flag drift."""
    shape, _ = _parse_type(_tokens_of_strata_type(), 0)
    payload = build_strata_payload(_full_graph())
    payload["providers"]["p1"]["not_declared"] = "x"
    del payload["providers"]["p1"]["region"]

    problems: list[str] = []
    _compare_with_shape(payload, shape, "strata", problems)

    assert problems == [
        "strata.providers['p1'].not_declared: emitted by the payload but not declared by the type",
        "strata.providers['p1'].region: declared required by the type but not emitted",
    ]
