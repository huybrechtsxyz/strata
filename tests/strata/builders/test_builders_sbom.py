"""Unit tests for SBOM builders and collectors."""

from unittest.mock import MagicMock

from strata.builders.sbom.ansible_collector import AnsibleCollectionCollector
from strata.builders.sbom.helm_collector import HelmChartCollector
from strata.builders.sbom.image_collector import ContainerImageCollector
from strata.builders.sbom.terraform_collector import TerraformProviderCollector
from strata.builders.sbom_builder import SbomBuilder
from strata.models.common_models import ProvisionerType
from strata.models.sbom_model import SbomComponentModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_deployment_service(validated=True, build_path=None):
    svc = MagicMock()
    svc.is_validated.return_value = validated
    if build_path is not None:
        svc.get_build_path.return_value = build_path
    return svc


def _mock_platform(modules=None, provisioners=None):
    platform = MagicMock()
    platform.spec.modules = modules or []
    platform.spec.provisioners = provisioners or []
    platform.meta.name = "test_platform"
    return platform


def _mock_service(name, image):
    svc = MagicMock()
    svc.name = name
    svc.image = image
    return svc


def _mock_module(services=None):
    mod = MagicMock()
    mod.services = services or []
    return mod


def _mock_provisioner(provisioner_type, chart_name=None, chart_version=None, chart_repo=None):
    prov = MagicMock()
    prov.provisioner = provisioner_type
    prov.source.chart_name = chart_name
    prov.source.chart_version = chart_version
    prov.source.chart_repository = chart_repo
    return prov


# ---------------------------------------------------------------------------
# SbomBuilder init
# ---------------------------------------------------------------------------


class TestSbomBuilderInit:
    def test_default_collectors_count(self):
        builder = SbomBuilder()
        assert len(builder._collectors) == 8

    def test_injectable_collectors(self):
        custom = [MagicMock()]
        builder = SbomBuilder(collectors=custom)
        assert builder._collectors is custom

    def test_empty_collectors(self):
        builder = SbomBuilder(collectors=[])
        assert builder._collectors == []

    def test_no_errors_on_init(self):
        builder = SbomBuilder()
        assert not builder.has_errors()
        assert builder.sbom_reference is None

    def test_no_deps_excludes_dependency_file_collector(self):
        from strata.builders.sbom.deps_collector import DependencyFileCollector

        builder = SbomBuilder(no_deps=True)
        types = [type(c) for c in builder._collectors]
        assert DependencyFileCollector not in types
        assert len(builder._collectors) == 7

    def test_no_deps_false_includes_dependency_file_collector(self):
        from strata.builders.sbom.deps_collector import DependencyFileCollector

        builder = SbomBuilder(no_deps=False)
        types = [type(c) for c in builder._collectors]
        assert DependencyFileCollector in types

    def test_no_deps_ignored_when_collectors_injected(self):
        """Explicit collectors= wins over no_deps — no_deps has no effect."""
        custom = [MagicMock()]
        builder = SbomBuilder(collectors=custom, no_deps=True)
        assert builder._collectors is custom


# ---------------------------------------------------------------------------
# SbomBuilder before_build
# ---------------------------------------------------------------------------


class TestSbomBuilderBeforeBuild:
    def test_not_validated_returns_false(self, tmp_path):
        builder = SbomBuilder()
        svc = _mock_deployment_service(validated=False)
        assert builder.before_build(svc, tmp_path, tmp_path) is False
        assert builder.has_errors()

    def test_missing_platform_json_returns_false(self, tmp_path):
        builder = SbomBuilder()
        build_path = tmp_path / "build"
        build_path.mkdir()
        svc = _mock_deployment_service(validated=True, build_path=tmp_path / "dep-1.0")
        assert builder.before_build(svc, tmp_path, build_path, dry_run=False) is False
        assert builder.has_errors()

    def test_dry_run_skips_platform_check(self, tmp_path):
        builder = SbomBuilder()
        svc = _mock_deployment_service(validated=True, build_path=tmp_path / "dep-1.0")
        assert builder.before_build(svc, tmp_path, tmp_path, dry_run=True) is True
        assert not builder.has_errors()


# ---------------------------------------------------------------------------
# SbomBuilder build — dry-run
# ---------------------------------------------------------------------------


class TestSbomBuilderBuildDryRun:
    def test_dry_run_returns_true_no_file_written(self, tmp_path):
        platform = _mock_platform()
        mock_collector = MagicMock()
        mock_collector.collect.return_value = []
        mock_collector.get_warnings.return_value = []
        mock_collector.get_collector_name.return_value = "mock"

        dep_path = tmp_path / "dep-1.0"
        svc = _mock_deployment_service(validated=True, build_path=dep_path)

        builder = SbomBuilder(collectors=[mock_collector])
        result = builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform)

        assert result is True
        assert not (dep_path / "sbom.json").exists()
        assert builder.sbom_reference is None

    def test_dry_run_message_contains_component_count(self, tmp_path):
        platform = _mock_platform()
        mock_collector = MagicMock()
        mock_collector.collect.return_value = [
            SbomComponentModel(
                component_type="container",
                name="app",
                version="v1",
                purl="pkg:docker/app@v1",
                properties={},
                source_collector="mock",
            )
        ]
        mock_collector.get_warnings.return_value = []
        mock_collector.get_collector_name.return_value = "mock"

        dep_path = tmp_path / "dep-1.0"
        svc = _mock_deployment_service(validated=True, build_path=dep_path)

        builder = SbomBuilder(collectors=[mock_collector])
        builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform)

        assert any("1 component" in m for m in builder.get_messages())


# ---------------------------------------------------------------------------
# ContainerImageCollector
# ---------------------------------------------------------------------------


class TestContainerImageCollector:
    def test_no_modules_returns_empty(self, tmp_path):
        collector = ContainerImageCollector()
        platform = _mock_platform(modules=[])
        result = collector.collect(platform, tmp_path, tmp_path)
        assert result == []

    def test_collects_image(self, tmp_path):
        svc = _mock_service("app", "ghcr.io/org/app:v1.2.3")
        mod = _mock_module(services=[svc])
        platform = _mock_platform(modules=[mod])

        collector = ContainerImageCollector()
        result = collector.collect(platform, tmp_path, tmp_path)

        assert len(result) == 1
        assert result[0].name == "app"
        assert result[0].purl == "pkg:docker/ghcr.io/org/app@v1.2.3"
        assert result[0].component_type == "container"

    def test_deduplicates_by_purl(self, tmp_path):
        svc1 = _mock_service("app", "traefik:v3.0.1")
        svc2 = _mock_service("app2", "traefik:v3.0.1")
        mod = _mock_module(services=[svc1, svc2])
        platform = _mock_platform(modules=[mod])

        collector = ContainerImageCollector()
        result = collector.collect(platform, tmp_path, tmp_path)
        assert len(result) == 1

    def test_floating_tag_emits_warning(self, tmp_path):
        svc = _mock_service("app", "ghcr.io/org/app:latest")
        mod = _mock_module(services=[svc])
        platform = _mock_platform(modules=[mod])

        collector = ContainerImageCollector()
        result = collector.collect(platform, tmp_path, tmp_path)

        assert len(result) == 1
        assert result[0].properties.get("strata:tag-stability") == "floating"
        assert len(collector.get_warnings()) == 1

    def test_pinned_tag_no_warning(self, tmp_path):
        svc = _mock_service("app", "postgres:16-alpine")
        mod = _mock_module(services=[svc])
        platform = _mock_platform(modules=[mod])

        collector = ContainerImageCollector()
        collector.collect(platform, tmp_path, tmp_path)
        assert collector.get_warnings() == []

    def test_service_without_image_skipped(self, tmp_path):
        svc = _mock_service("app", None)
        mod = _mock_module(services=[svc])
        platform = _mock_platform(modules=[mod])

        collector = ContainerImageCollector()
        result = collector.collect(platform, tmp_path, tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# HelmChartCollector
# ---------------------------------------------------------------------------


class TestHelmChartCollector:
    def test_no_provisioners_returns_empty(self, tmp_path):
        platform = _mock_platform(provisioners=[])
        collector = HelmChartCollector()
        assert collector.collect(platform, tmp_path, tmp_path) == []

    def test_collects_helm_chart(self, tmp_path):
        prov = _mock_provisioner(
            ProvisionerType.HELM,
            chart_name="authentik",
            chart_version="2024.12.0",
            chart_repo="https://charts.goauthentik.io",
        )
        platform = _mock_platform(provisioners=[prov])

        collector = HelmChartCollector()
        result = collector.collect(platform, tmp_path, tmp_path)

        assert len(result) == 1
        assert result[0].name == "authentik"
        assert result[0].version == "2024.12.0"
        assert "pkg:helm/authentik@2024.12.0" in result[0].purl

    def test_skips_non_helm_provisioner(self, tmp_path):
        prov = _mock_provisioner(ProvisionerType.TERRAFORM)
        platform = _mock_platform(provisioners=[prov])

        collector = HelmChartCollector()
        result = collector.collect(platform, tmp_path, tmp_path)
        assert result == []

    def test_skips_helm_without_chart_name(self, tmp_path):
        prov = _mock_provisioner(ProvisionerType.HELM, chart_name=None)
        platform = _mock_platform(provisioners=[prov])

        collector = HelmChartCollector()
        result = collector.collect(platform, tmp_path, tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# TerraformProviderCollector
# ---------------------------------------------------------------------------


class TestTerraformProviderCollector:
    def test_empty_build_path_returns_empty(self, tmp_path):
        platform = _mock_platform()
        collector = TerraformProviderCollector()
        result = collector.collect(platform, tmp_path, tmp_path / "nonexistent")
        assert result == []

    def test_parses_required_providers(self, tmp_path):
        tf_content = """
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.90"
    }
  }
}
"""
        tf_file = tmp_path / "main.tf"
        tf_file.write_text(tf_content)
        platform = _mock_platform()

        collector = TerraformProviderCollector()
        result = collector.collect(platform, tmp_path, tmp_path)

        assert len(result) == 1
        assert result[0].name == "azurerm"
        assert result[0].version == "~> 3.90"
        assert result[0].purl == "pkg:terraform/hashicorp/azurerm@~> 3.90"

    def test_deduplicates_providers_across_files(self, tmp_path):
        tf_content = """
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.90"
    }
  }
}
"""
        (tmp_path / "main.tf").write_text(tf_content)
        (tmp_path / "other.tf").write_text(tf_content)
        platform = _mock_platform()

        collector = TerraformProviderCollector()
        result = collector.collect(platform, tmp_path, tmp_path)
        assert len(result) == 1

    def test_invalid_tf_file_adds_warning(self, tmp_path):
        (tmp_path / "bad.tf").write_text("not { valid hcl !!!}")
        platform = _mock_platform()

        collector = TerraformProviderCollector()
        result = collector.collect(platform, tmp_path, tmp_path)
        assert result == []
        assert len(collector.get_warnings()) > 0

    def test_strip_hcl_string(self):
        collector = TerraformProviderCollector()
        assert collector._strip_hcl_string('"hashicorp/azurerm"') == "hashicorp/azurerm"
        assert collector._strip_hcl_string("plain") == "plain"
        assert collector._strip_hcl_string(None) is None


# ---------------------------------------------------------------------------
# AnsibleCollectionCollector
# ---------------------------------------------------------------------------


class TestAnsibleCollectionCollector:
    def test_no_ansible_provisioners_returns_empty(self, tmp_path):
        prov = _mock_provisioner(ProvisionerType.TERRAFORM)
        platform = _mock_platform(provisioners=[prov])
        collector = AnsibleCollectionCollector()
        result = collector.collect(platform, tmp_path, tmp_path)
        assert result == []

    def test_parses_collections(self, tmp_path):
        prov = _mock_provisioner(ProvisionerType.ANSIBLE)
        platform = _mock_platform(provisioners=[prov])

        req_file = tmp_path / "requirements.yml"
        req_file.write_text("collections:\n  - name: community.general\n    version: '7.0.0'\n")

        collector = AnsibleCollectionCollector()
        result = collector.collect(platform, tmp_path, tmp_path)

        assert len(result) == 1
        assert result[0].name == "community.general"
        assert result[0].version == "7.0.0"
        assert result[0].purl == "pkg:ansible/community.general@7.0.0"

    def test_parses_roles(self, tmp_path):
        prov = _mock_provisioner(ProvisionerType.ANSIBLE)
        platform = _mock_platform(provisioners=[prov])

        req_file = tmp_path / "requirements.yml"
        req_file.write_text("roles:\n  - name: geerlingguy.docker\n    version: '6.0.0'\n")

        collector = AnsibleCollectionCollector()
        result = collector.collect(platform, tmp_path, tmp_path)

        assert len(result) == 1
        assert result[0].name == "geerlingguy.docker"
        assert result[0].purl == "pkg:ansible/geerlingguy.docker@6.0.0"

    def test_invalid_yaml_adds_warning(self, tmp_path):
        prov = _mock_provisioner(ProvisionerType.ANSIBLE)
        platform = _mock_platform(provisioners=[prov])

        (tmp_path / "requirements.yml").write_text(": invalid: yaml: [[[")

        collector = AnsibleCollectionCollector()
        collector.collect(platform, tmp_path, tmp_path)
        assert len(collector.get_warnings()) > 0

    def test_missing_name_entry_skipped(self, tmp_path):
        prov = _mock_provisioner(ProvisionerType.ANSIBLE)
        platform = _mock_platform(provisioners=[prov])

        (tmp_path / "requirements.yml").write_text("collections:\n  - version: '1.0.0'\n")

        collector = AnsibleCollectionCollector()
        result = collector.collect(platform, tmp_path, tmp_path)
        assert result == []

    def test_nonexistent_build_path_returns_empty(self, tmp_path):
        prov = _mock_provisioner(ProvisionerType.ANSIBLE)
        platform = _mock_platform(provisioners=[prov])
        collector = AnsibleCollectionCollector()
        result = collector.collect(platform, tmp_path, tmp_path / "nonexistent")
        assert result == []
