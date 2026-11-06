#!/usr/bin/env python3
"""Tests for `TerraformCollector` (docs/design/sbom-generation.md Phase 1)."""

from pathlib import Path

import pytest

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.terraform_collector import TerraformCollector, read_provider_locks
from strata.models.common_models import SourceModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel
from strata.utils.diagnostics import Severity


def _graph(**spec) -> ResolvedWorkspaceGraph:
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
            **spec,
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
    assert providers[0].version is None
    assert providers[0].purl == "pkg:terraform/hashicorp/azurerm"
    assert providers[0].properties == {"strata:version-constraint": "~>3.90"}


_AZURERM = (
    'terraform {\n  required_providers {\n    azurerm = {\n      source  = "hashicorp/azurerm"\n'
    '      version = "~> 4.0"\n    }\n  }\n}\n'
)
_RANDOM = 'terraform {\n  required_providers {\n    random = {\n      source = "hashicorp/random"\n    }\n  }\n}\n'


def _lock(*providers: tuple[str, str, str | None]) -> str:
    blocks = []
    for address, version, constraints in providers:
        body = f'  version = "{version}"\n'
        if constraints:
            body += f'  constraints = "{constraints}"\n'
        body += '  hashes = [\n    "h1:abc=",\n  ]\n'
        blocks.append(f'provider "{address}" {{\n{body}}}\n')
    return "\n".join(blocks)


def _providers(build_path: Path) -> list:
    result = TerraformCollector().collect(_graph(), [], build_path)
    assert [d for d in result.diagnostics if d.severity != Severity.INFO] == []
    return [c for c in result.components if c.source_collector == "terraform"]


def _notices(build_path: Path) -> list[str]:
    result = TerraformCollector().collect(_graph(), [], build_path)
    notices = [d for d in result.diagnostics if d.code == "sbom_provider_unlocked"]
    assert all(d.severity == Severity.INFO for d in notices)
    return [d.message for d in notices]


def test_a_root_without_a_lock_file_gets_one_notice_naming_its_providers(tmp_path: Path):
    (tmp_path / "main").mkdir()
    (tmp_path / "main" / "main.tf").write_text(_AZURERM, encoding="utf-8")
    (tmp_path / "main" / "modules" / "net").mkdir(parents=True)
    (tmp_path / "main" / "modules" / "net" / "versions.tf").write_text(_RANDOM, encoding="utf-8")

    assert _notices(tmp_path) == [
        "main: no .terraform.lock.hcl; providers azurerm, random are listed in the SBOM without a version."
    ]


def test_a_lock_file_that_misses_a_provider_gets_a_notice(tmp_path: Path):
    (tmp_path / "main.tf").write_text(_AZURERM, encoding="utf-8")
    (tmp_path / "versions.tf").write_text(_RANDOM, encoding="utf-8")
    (tmp_path / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/azurerm", "4.12.0", None)), encoding="utf-8"
    )

    assert _notices(tmp_path) == [
        ".: .terraform.lock.hcl does not lock random; it is listed in the SBOM without a version."
    ]


def test_a_root_using_providers_it_does_not_declare_gets_a_notice_without_a_lock_file(tmp_path: Path):
    """Without `required_providers` and a lock file the provider is not in the SBOM at all, so say so."""
    (tmp_path / "main").mkdir()
    (tmp_path / "main" / "main.tf").write_text('resource "null_resource" "x" {}\n', encoding="utf-8")
    (tmp_path / "main" / "modules").mkdir()
    (tmp_path / "main" / "modules" / "main.tf").write_text('data "http" "x" {\n  url = "u"\n}\n', encoding="utf-8")
    (tmp_path / "outputs-only").mkdir()
    (tmp_path / "outputs-only" / "main.tf").write_text('output "x" {\n  value = 1\n}\n', encoding="utf-8")

    assert _notices(tmp_path) == ["main: no .terraform.lock.hcl; the providers it uses are not listed in the SBOM."]

    (tmp_path / "main" / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/null", "3.2.1", None)), encoding="utf-8"
    )
    assert _notices(tmp_path) == []


def test_a_root_that_declares_and_uses_providers_gets_one_notice(tmp_path: Path):
    (tmp_path / "main.tf").write_text(_AZURERM + 'resource "null_resource" "x" {}\n', encoding="utf-8")
    assert _notices(tmp_path) == [
        ".: no .terraform.lock.hcl; provider azurerm is listed in the SBOM without a version."
    ]


def test_with_a_provisioner_lock_policy_the_policy_reports_instead(tmp_path: Path):
    from strata.models.provisioner_lock_policy_model import ProvisionerLockPolicyModel

    (tmp_path / "main.tf").write_text(_AZURERM, encoding="utf-8")
    graph = _graph(provisioner_lock_policy=ProvisionerLockPolicyModel())

    result = TerraformCollector().collect(graph, [], tmp_path)

    assert [d for d in result.diagnostics if d.code == "sbom_provider_unlocked"] == []
    assert [c.purl for c in result.components] == ["pkg:terraform/hashicorp/azurerm"]


def test_a_complete_lock_file_or_no_providers_gets_no_notice(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "main.tf").write_text(_AZURERM, encoding="utf-8")
    (tmp_path / "a" / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/azurerm", "4.12.0", None)), encoding="utf-8"
    )
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "main.tf").write_text('output "x" {\n  value = 1\n}\n', encoding="utf-8")

    assert _notices(tmp_path) == []


def test_a_locked_provider_is_listed_at_its_locked_version(tmp_path: Path):
    (tmp_path / "main.tf").write_text(_AZURERM, encoding="utf-8")
    (tmp_path / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/azurerm", "4.12.0", "~> 4.0")), encoding="utf-8"
    )

    [provider] = _providers(tmp_path)

    assert (provider.name, provider.version, provider.purl) == (
        "azurerm",
        "4.12.0",
        "pkg:terraform/hashicorp/azurerm@4.12.0",
    )
    assert provider.properties == {"strata:version-constraint": "~> 4.0"}


def test_a_provider_used_without_required_providers_is_listed_from_the_lock(tmp_path: Path):
    (tmp_path / "main.tf").write_text('resource "null_resource" "x" {}\n', encoding="utf-8")
    (tmp_path / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/null", "3.2.1", None)), encoding="utf-8"
    )

    [provider] = _providers(tmp_path)

    assert (provider.name, provider.purl, provider.properties) == (
        "null",
        "pkg:terraform/hashicorp/null@3.2.1",
        {},
    )


def test_a_lock_only_applies_to_its_own_root_module(tmp_path: Path):
    """Terraform keeps one lock per root module; two roots may lock different versions, both deployed."""
    for root, version in (("a", "4.12.0"), ("b", "4.20.0")):
        (tmp_path / root).mkdir()
        (tmp_path / root / "main.tf").write_text(_AZURERM, encoding="utf-8")
        (tmp_path / root / ".terraform.lock.hcl").write_text(
            _lock(("registry.terraform.io/hashicorp/azurerm", version, "~> 4.0")), encoding="utf-8"
        )
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "main.tf").write_text(_AZURERM, encoding="utf-8")

    assert [p.purl for p in _providers(tmp_path)] == [
        "pkg:terraform/hashicorp/azurerm@4.12.0",
        "pkg:terraform/hashicorp/azurerm@4.20.0",
        "pkg:terraform/hashicorp/azurerm",
    ]


def test_a_provider_from_another_registry_carries_it_as_repository_url(tmp_path: Path):
    (tmp_path / "main.tf").write_text(
        'terraform {\n  required_providers {\n    null = {\n      source  = "registry.opentofu.org/hashicorp/null"\n'
        "    }\n  }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / ".terraform.lock.hcl").write_text(
        _lock(("registry.opentofu.org/hashicorp/null", "3.2.1", ">= 3.0.0")), encoding="utf-8"
    )

    [provider] = _providers(tmp_path)

    assert provider.purl == "pkg:terraform/hashicorp/null@3.2.1?repository_url=registry.opentofu.org"
    assert provider.properties == {"strata:version-constraint": ">= 3.0.0"}  # the lock's, none was declared


def test_a_child_module_uses_its_roots_lock_file(tmp_path: Path):
    """Terraform keeps the lock file at the root; it locks the providers its child modules declare too."""
    (tmp_path / "main.tf").write_text('module "net" {\n  source = "./modules/net"\n}\n', encoding="utf-8")
    (tmp_path / "modules" / "net").mkdir(parents=True)
    (tmp_path / "modules" / "net" / "versions.tf").write_text(_AZURERM, encoding="utf-8")
    (tmp_path / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/azurerm", "4.12.0", "~> 4.0")), encoding="utf-8"
    )

    assert [p.purl for p in _providers(tmp_path)] == ["pkg:terraform/hashicorp/azurerm@4.12.0"]


def test_a_lock_file_above_the_build_path_is_not_used(tmp_path: Path):
    (tmp_path / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/azurerm", "4.12.0", None)), encoding="utf-8"
    )
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "main.tf").write_text(_AZURERM, encoding="utf-8")

    assert [p.purl for p in _providers(tmp_path / "build")] == ["pkg:terraform/hashicorp/azurerm"]


def test_read_provider_locks_reports_a_missing_lock_file(tmp_path: Path):
    (tmp_path / "main.tf").write_text(_AZURERM + 'resource "null_resource" "x" {}\n', encoding="utf-8")

    locks = read_provider_locks(tmp_path)

    assert (locks.lock_file, locks.needs_providers) == (None, True)
    assert locks.declared == {"registry.terraform.io/hashicorp/azurerm"}
    assert locks.unlocked == ["registry.terraform.io/hashicorp/azurerm"]


def test_read_provider_locks_reports_providers_the_lock_file_misses(tmp_path: Path):
    (tmp_path / "main.tf").write_text(_AZURERM, encoding="utf-8")
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "versions.tf").write_text(
        'terraform {\n  required_providers {\n    random = {\n      source = "hashicorp/random"\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    (tmp_path / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/azurerm", "4.12.0", None)), encoding="utf-8"
    )

    locks = read_provider_locks(tmp_path)

    assert locks.lock_file == tmp_path / ".terraform.lock.hcl"
    assert locks.locked == {"registry.terraform.io/hashicorp/azurerm"}
    assert locks.unlocked == ["registry.terraform.io/hashicorp/random"]


@pytest.mark.parametrize(
    ("content", "needs"),
    [
        ('output "x" {\n  value = 1\n}\nlocals {\n  y = 2\n}\n', False),
        ('resource "null_resource" "x" {}\n', True),
        ('data "azurerm_client_config" "current" {}\n', True),
        ('provider "azurerm" {\n  features {}\n}\n', True),
        ('module "net" {\n  source = "./net"\n}\n', True),
    ],
)
def test_read_provider_locks_knows_when_a_root_needs_providers(tmp_path: Path, content, needs):
    (tmp_path / "main.tf").write_text(content, encoding="utf-8")
    assert read_provider_locks(tmp_path).needs_providers is needs


def test_only_root_level_blocks_or_declared_providers_need_a_lock(tmp_path: Path):
    """A folder the root does not call is not run by `terraform init`; a declared provider always counts."""
    (tmp_path / "main.tf").write_text('output "x" {\n  value = 1\n}\n', encoding="utf-8")
    (tmp_path / "unused").mkdir()
    (tmp_path / "unused" / "main.tf").write_text('resource "null_resource" "x" {}\n', encoding="utf-8")
    assert read_provider_locks(tmp_path).needs_providers is False

    (tmp_path / "unused" / "versions.tf").write_text(_AZURERM, encoding="utf-8")
    assert read_provider_locks(tmp_path).needs_providers is True


def test_a_nested_root_with_its_own_lock_file_is_left_out_of_the_outer_root(tmp_path: Path):
    """`infra/envs/prd` is a separate Terraform root; its providers are locked there, not in `infra`'s lock file."""
    (tmp_path / "main.tf").write_text(_AZURERM, encoding="utf-8")
    (tmp_path / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/azurerm", "4.12.0", None)), encoding="utf-8"
    )
    nested = tmp_path / "envs" / "prd"
    nested.mkdir(parents=True)
    (nested / "main.tf").write_text(_RANDOM, encoding="utf-8")
    (nested / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/random", "3.6.0", None)), encoding="utf-8"
    )

    locks = read_provider_locks(tmp_path)

    assert locks.declared == {"registry.terraform.io/hashicorp/azurerm"}
    assert locks.unlocked == []


def test_a_nested_folder_without_a_lock_file_still_belongs_to_the_outer_root(tmp_path: Path):
    (tmp_path / "main.tf").write_text(_AZURERM, encoding="utf-8")
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "versions.tf").write_text(_RANDOM, encoding="utf-8")
    assert read_provider_locks(tmp_path).unlocked == [
        "registry.terraform.io/hashicorp/azurerm",
        "registry.terraform.io/hashicorp/random",
    ]


def test_what_terraform_init_left_in_dot_terraform_is_not_read(tmp_path: Path):
    """`deploy run` runs `terraform init` in the build path; its downloaded modules are not the sources."""
    (tmp_path / "main.tf").write_text(_AZURERM, encoding="utf-8")
    (tmp_path / ".terraform.lock.hcl").write_text(
        _lock(("registry.terraform.io/hashicorp/azurerm", "4.12.0", None)), encoding="utf-8"
    )
    downloaded = tmp_path / ".terraform" / "modules" / "vpc"
    downloaded.mkdir(parents=True)
    (downloaded / "main.tf").write_text(
        _RANDOM + 'module "sub" {\n  source  = "terraform-aws-modules/vpc/aws"\n  version = "5.0.0"\n}\n',
        encoding="utf-8",
    )

    result = TerraformCollector().collect(_graph(), [], tmp_path)

    assert [c.purl for c in result.components] == ["pkg:terraform/hashicorp/azurerm@4.12.0"]
    assert result.diagnostics == []
    assert read_provider_locks(tmp_path).declared == {"registry.terraform.io/hashicorp/azurerm"}


def test_an_unparseable_lock_file_warns_and_falls_back_to_the_constraint(tmp_path: Path):
    (tmp_path / "main.tf").write_text(_AZURERM, encoding="utf-8")
    (tmp_path / ".terraform.lock.hcl").write_text("provider [[[", encoding="utf-8")

    result = TerraformCollector().collect(_graph(), [], tmp_path)

    assert [c.purl for c in result.components] == ["pkg:terraform/hashicorp/azurerm"]
    warnings = [d for d in result.diagnostics if d.severity == Severity.WARNING]
    assert len(warnings) == 1
    assert ".terraform.lock.hcl" in warnings[0].message


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
    assert providers[0].properties == {"strata:version-constraint": "~>3.90"}  # a.tf sorts first, first one wins
