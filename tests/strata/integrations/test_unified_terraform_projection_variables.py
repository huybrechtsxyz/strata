#!/usr/bin/env python3
"""Real-terraform regression test for Phase 5 of docs/work/
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
import shutil
import subprocess
from pathlib import Path

import pytest

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.terraform import TerraformIntegration
from strata.integrations.terraform_projection import build_strata_payload
from strata.models.common_models import ModuleReferenceModel, SourceModel
from strata.models.dns_model import DnsMetaModel, DnsModel, DnsRecordModel, DnsSpecModel, DnsZoneModel
from strata.models.firewall_model import FirewallMetaModel, FirewallModel, FirewallSpecModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModulePropertiesModel, ModuleSpecModel
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
from strata.models.network_model import (
    NetworkDefinitionModel,
    NetworkMetaModel,
    NetworkModel,
    NetworkSpecModel,
    SubnetModel,
)
from strata.models.provider_model import ProviderMetaModel, ProviderModel, ProviderPropertiesModel, ProviderSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.resource_model import (
    ResourceDiskModel,
    ResourceMetaModel,
    ResourceModel,
    ResourcePropertiesModel,
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
        spec=ProviderSpecModel(properties=ProviderPropertiesModel(type="azurerm", region="westeurope")),
    )
    resource = ResourceModel(
        meta=ResourceMetaModel(name="r1"),
        spec=ResourceSpecModel(
            properties=ResourcePropertiesModel(provider_type="azurerm", resource_type="virtual_machine"),
            storage=ResourceStorageModel(disks=[ResourceDiskModel(name="os", size=64, label="os-disk", mount="/data")]),
        ),
    )
    namespace = NamespaceModel(
        meta=NamespaceMetaModel(name="hearth"),
        spec=NamespaceSpecModel(modules=[ModuleReferenceModel(name="auth", module="authentik")]),
    )
    module = ModuleModel(
        meta=ModuleMetaModel(name="authentik"),
        spec=ModuleSpecModel(
            source=SourceModel(source_path="modules/authentik"), type="helm", properties=ModulePropertiesModel()
        ),
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
        spec=ProviderSpecModel(properties=ProviderPropertiesModel(type="azurerm", region="westeurope")),
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
