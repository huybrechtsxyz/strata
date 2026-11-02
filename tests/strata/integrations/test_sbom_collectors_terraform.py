#!/usr/bin/env python3
"""Tests for `TerraformCollector` (docs/design/sbom-generation.md Phase 1)."""

from pathlib import Path

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.terraform_collector import TerraformCollector
from strata.models.common_models import SourceModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel


def _graph() -> ResolvedWorkspaceGraph:
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    return ResolvedWorkspaceGraph(workspace=workspace)


def test_collect_returns_empty_when_build_path_does_not_exist(tmp_path: Path):
    result = TerraformCollector().collect(_graph(), [], tmp_path / "missing")
    assert result.components == []


def test_collect_extracts_a_required_provider(tmp_path: Path):
    (tmp_path / "main.tf").write_text(
        'terraform {\n  required_providers {\n    azurerm = {\n      source  = "hashicorp/azurerm"\n'
        '      version = "~>3.90"\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    result = TerraformCollector().collect(_graph(), [], tmp_path)

    providers = [c for c in result.components if c.source_collector == "terraform"]
    assert len(providers) == 1
    assert providers[0].name == "azurerm"
    assert providers[0].version == "~>3.90"
    assert providers[0].purl == "pkg:terraform/hashicorp/azurerm@~>3.90"


def test_collect_extracts_a_registry_module(tmp_path: Path):
    (tmp_path / "main.tf").write_text(
        'module "vpc" {\n  source  = "terraform-aws-modules/vpc/aws"\n  version = "5.0.0"\n}\n',
        encoding="utf-8",
    )
    result = TerraformCollector().collect(_graph(), [], tmp_path)

    modules = [c for c in result.components if c.source_collector == "terraform-module"]
    assert len(modules) == 1
    assert modules[0].name == "terraform-aws-modules/vpc"
    assert modules[0].version == "5.0.0"


def test_collect_skips_a_local_module_source(tmp_path: Path):
    (tmp_path / "main.tf").write_text('module "net" {\n  source = "./modules/net"\n}\n', encoding="utf-8")
    result = TerraformCollector().collect(_graph(), [], tmp_path)
    assert [c for c in result.components if c.source_collector == "terraform-module"] == []


def test_collect_warns_on_an_unparseable_tf_file(tmp_path: Path):
    (tmp_path / "main.tf").write_text("this is not valid { hcl [[[", encoding="utf-8")
    result = TerraformCollector().collect(_graph(), [], tmp_path)

    assert result.components == []
    assert len(result.diagnostics) == 1
    assert "failed to parse" in result.diagnostics[0].message


def test_collect_deduplicates_providers_first_occurrence_wins(tmp_path: Path):
    (tmp_path / "a.tf").write_text(
        'terraform {\n  required_providers {\n    azurerm = {\n      source  = "hashicorp/azurerm"\n'
        '      version = "~>3.90"\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    (tmp_path / "b.tf").write_text(
        'terraform {\n  required_providers {\n    azurerm = {\n      source  = "hashicorp/azurerm"\n'
        '      version = "~>5.0"\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    result = TerraformCollector().collect(_graph(), [], tmp_path)

    providers = [c for c in result.components if c.source_collector == "terraform"]
    assert len(providers) == 1
    assert providers[0].version == "~>3.90"  # a.tf sorts before b.tf, first occurrence wins
