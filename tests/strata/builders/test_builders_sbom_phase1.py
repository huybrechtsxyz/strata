"""Unit tests for TerraformModuleCollector, ComposeImageCollector, and CollectorPluginLoader."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strata.builders.sbom.collector_plugin_loader import CollectorPluginLoader
from strata.builders.sbom.compose_collector import ComposeImageCollector
from strata.builders.sbom.terraform_module_collector import TerraformModuleCollector
from strata.exceptions.base_exception import PlatformError
from strata.utils.sbom_utils import terraform_module_to_purl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_platform():
    p = MagicMock()
    p.spec.modules = []
    return p


def _write_tf(path: Path, content: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    f = path / "main.tf"
    f.write_text(content, encoding="utf-8")
    return f


def _write_compose(path: Path, content: str, name: str = "docker-compose.yml") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    f = path / name
    f.write_text(content, encoding="utf-8")
    return f


# ===========================================================================
# terraform_module_to_purl (unit tests — no I/O)
# ===========================================================================


class TestTerraformModuleToPurl:
    def test_local_dot_slash_returns_none(self):
        assert terraform_module_to_purl("./modules/vpc") is None

    def test_local_dot_dot_returns_none(self):
        assert terraform_module_to_purl("../shared/modules/vpc") is None

    def test_explicit_registry_with_version(self):
        purl = terraform_module_to_purl("registry.terraform.io/hashicorp/consul/aws", "0.11.0")
        assert purl == "pkg:terraform/hashicorp/consul@0.11.0?repository_url=registry.terraform.io"

    def test_explicit_registry_without_version(self):
        purl = terraform_module_to_purl("registry.terraform.io/Azure/compute/azurerm")
        assert purl == "pkg:terraform/Azure/compute?repository_url=registry.terraform.io"

    def test_short_form_registry(self):
        purl = terraform_module_to_purl("Azure/aks/azurerm", "9.2.1")
        assert purl == "pkg:terraform/Azure/aks@9.2.1?repository_url=registry.terraform.io"

    def test_short_form_registry_no_version(self):
        purl = terraform_module_to_purl("hashicorp/consul/aws")
        assert purl == "pkg:terraform/hashicorp/consul?repository_url=registry.terraform.io"

    def test_github_with_ref_query(self):
        purl = terraform_module_to_purl("github.com/org/module?ref=v1.2.3")
        assert purl == "pkg:github/org/module@v1.2.3"

    def test_github_with_version_attribute(self):
        purl = terraform_module_to_purl("github.com/org/module", "v2.0.0")
        assert purl == "pkg:github/org/module@v2.0.0"

    def test_github_no_ref(self):
        purl = terraform_module_to_purl("github.com/org/module")
        assert purl == "pkg:github/org/module"

    def test_github_with_subdir(self):
        purl = terraform_module_to_purl("github.com/org/module//subdir?ref=v1.0")
        assert purl == "pkg:github/org/module@v1.0"

    def test_unsupported_host_returns_none(self):
        assert terraform_module_to_purl("bitbucket.org/org/module") is None

    def test_explicit_registry_short_path_returns_none(self):
        # Too few path components after host
        assert terraform_module_to_purl("registry.terraform.io/hashicorp") is None


# ===========================================================================
# TerraformModuleCollector
# ===========================================================================


class TestTerraformModuleCollector:
    def test_collector_name(self):
        assert TerraformModuleCollector().get_collector_name() == "terraform-module"

    def test_returns_empty_when_build_path_missing(self, tmp_path):
        collector = TerraformModuleCollector()
        result = collector.collect(_mock_platform(), tmp_path, tmp_path / "nonexistent")
        assert result == []

    def test_collects_registry_module(self, tmp_path):
        build = tmp_path / "build"
        _write_tf(
            build,
            """
module "vpc" {
  source  = "registry.terraform.io/Azure/vnet/azurerm"
  version = "~> 3.0"
}
""",
        )
        collector = TerraformModuleCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1
        assert "registry.terraform.io" in results[0].purl
        assert "Azure/vnet" in results[0].purl
        assert "~> 3.0" in results[0].purl

    def test_collects_short_form_registry_module(self, tmp_path):
        build = tmp_path / "build"
        _write_tf(
            build,
            """
module "consul" {
  source  = "hashicorp/consul/aws"
  version = "0.11.0"
}
""",
        )
        collector = TerraformModuleCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1
        assert results[0].purl == "pkg:terraform/hashicorp/consul@0.11.0?repository_url=registry.terraform.io"

    def test_skips_local_module(self, tmp_path):
        build = tmp_path / "build"
        _write_tf(
            build,
            """
module "local_mod" {
  source = "./modules/vpc"
}
""",
        )
        collector = TerraformModuleCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert results == []

    def test_collects_github_module(self, tmp_path):
        build = tmp_path / "build"
        _write_tf(
            build,
            """
module "app" {
  source = "github.com/org/module?ref=v2.0.0"
}
""",
        )
        collector = TerraformModuleCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1
        assert results[0].purl == "pkg:github/org/module@v2.0.0"

    def test_module_without_version(self, tmp_path):
        build = tmp_path / "build"
        _write_tf(
            build,
            """
module "vpc" {
  source = "hashicorp/consul/aws"
}
""",
        )
        collector = TerraformModuleCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1
        assert results[0].version is None
        assert "@" not in results[0].purl.split("?")[0]

    def test_deduplicates_same_source(self, tmp_path):
        build = tmp_path / "build"
        _write_tf(
            build,
            """
module "vpc1" {
  source  = "hashicorp/consul/aws"
  version = "0.11.0"
}
module "vpc2" {
  source  = "hashicorp/consul/aws"
  version = "0.12.0"
}
""",
        )
        collector = TerraformModuleCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1
        assert "0.11.0" in results[0].purl  # first occurrence wins

    def test_parse_error_produces_warning(self, tmp_path):
        build = tmp_path / "build"
        build.mkdir()
        bad_tf = build / "broken.tf"
        bad_tf.write_text("{{{invalid hcl", encoding="utf-8")
        collector = TerraformModuleCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert results == []
        assert len(collector.get_warnings()) == 1
        assert "broken.tf" in collector.get_warnings()[0]

    def test_source_collector_name(self, tmp_path):
        build = tmp_path / "build"
        _write_tf(
            build,
            """
module "vpc" {
  source  = "hashicorp/consul/aws"
  version = "0.11.0"
}
""",
        )
        collector = TerraformModuleCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert results[0].source_collector == "terraform-module"

    def test_scans_subdirectories(self, tmp_path):
        build = tmp_path / "build"
        subdir = build / "modules"
        _write_tf(
            subdir,
            """
module "sub" {
  source  = "hashicorp/consul/aws"
  version = "0.11.0"
}
""",
        )
        collector = TerraformModuleCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1


# ===========================================================================
# ComposeImageCollector
# ===========================================================================


class TestComposeImageCollector:
    def test_collector_name(self):
        assert ComposeImageCollector().get_collector_name() == "compose"

    def test_returns_empty_when_build_path_missing(self, tmp_path):
        collector = ComposeImageCollector()
        result = collector.collect(_mock_platform(), tmp_path, tmp_path / "nonexistent")
        assert result == []

    def test_returns_empty_when_no_compose_files(self, tmp_path):
        build = tmp_path / "build"
        build.mkdir()
        collector = ComposeImageCollector()
        result = collector.collect(_mock_platform(), tmp_path, build)
        assert result == []

    def test_collects_images_from_compose_yml(self, tmp_path):
        build = tmp_path / "build"
        _write_compose(
            build,
            """
services:
  web:
    image: nginx:1.27.0
  db:
    image: postgres:16.2
""",
        )
        collector = ComposeImageCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 2
        purls = {r.purl for r in results}
        assert "pkg:docker/nginx@1.27.0" in purls
        assert "pkg:docker/postgres@16.2" in purls

    def test_collects_images_from_compose_yaml(self, tmp_path):
        build = tmp_path / "build"
        _write_compose(
            build,
            """
services:
  app:
    image: myapp:v1.0.0
""",
            name="docker-compose.yaml",
        )
        collector = ComposeImageCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1
        assert results[0].purl == "pkg:docker/myapp@v1.0.0"

    def test_skips_services_without_image(self, tmp_path):
        build = tmp_path / "build"
        _write_compose(
            build,
            """
services:
  builder:
    build: ./app
  web:
    image: nginx:1.27.0
""",
        )
        collector = ComposeImageCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1
        assert results[0].name == "web"

    def test_floating_tag_adds_property(self, tmp_path):
        build = tmp_path / "build"
        _write_compose(
            build,
            """
services:
  redis:
    image: redis:latest
""",
        )
        collector = ComposeImageCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1
        assert results[0].properties.get("strata:tag-stability") == "floating"
        assert len(collector.get_warnings()) == 1

    def test_no_tag_is_floating(self, tmp_path):
        build = tmp_path / "build"
        _write_compose(
            build,
            """
services:
  app:
    image: myapp
""",
        )
        collector = ComposeImageCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert results[0].properties.get("strata:tag-stability") == "floating"

    def test_deduplicates_same_purl(self, tmp_path):
        build = tmp_path / "build"
        _write_compose(
            build,
            """
services:
  web1:
    image: nginx:1.27.0
  web2:
    image: nginx:1.27.0
""",
        )
        collector = ComposeImageCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1

    def test_parse_error_produces_warning(self, tmp_path):
        build = tmp_path / "build"
        build.mkdir()
        bad = build / "docker-compose.yml"
        bad.write_text(": invalid: yaml: content: {{{", encoding="utf-8")
        collector = ComposeImageCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert results == []
        assert len(collector.get_warnings()) == 1

    def test_source_collector_name(self, tmp_path):
        build = tmp_path / "build"
        _write_compose(
            build,
            """
services:
  web:
    image: nginx:1.27.0
""",
        )
        collector = ComposeImageCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert results[0].source_collector == "compose"

    def test_scans_subdirectories(self, tmp_path):
        build = tmp_path / "build"
        subdir = build / "svc"
        _write_compose(
            subdir,
            """
services:
  app:
    image: myapp:v2.0.0
""",
        )
        collector = ComposeImageCollector()
        results = collector.collect(_mock_platform(), tmp_path, build)
        assert len(results) == 1


# ===========================================================================
# CollectorPluginLoader
# ===========================================================================


class TestCollectorPluginLoader:
    def test_returns_empty_when_no_config_file(self, tmp_path):
        result = CollectorPluginLoader.load(tmp_path)
        assert result == []

    def test_returns_empty_when_collectors_key_missing(self, tmp_path):
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "collectors.yaml").write_text("other_key: value\n")
        result = CollectorPluginLoader.load(tmp_path)
        assert result == []

    def test_returns_empty_when_collectors_is_empty(self, tmp_path):
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "collectors.yaml").write_text("collectors: []\n")
        result = CollectorPluginLoader.load(tmp_path)
        assert result == []

    def test_loads_collector_type_plugin(self, tmp_path):
        # Write a minimal BaseSbomCollector subclass to a plugin file
        plugins_dir = tmp_path / ".strata" / "plugins"
        plugins_dir.mkdir(parents=True)
        plugin_file = plugins_dir / "my_collector.py"
        plugin_file.write_text(
            "from strata.builders.sbom.base_sbom_collector import BaseSbomCollector\n"
            "from strata.models.platform_artifact_model import PlatformArtifactModel\n"
            "from pathlib import Path\n"
            "from typing import List\n"
            "class MyCollector(BaseSbomCollector):\n"
            "    def get_collector_name(self): return 'my'\n"
            "    def collect(self, p, w, d): return []\n",
            encoding="utf-8",
        )
        strata_dir = tmp_path / ".strata"
        (strata_dir / "collectors.yaml").write_text(
            "collectors:\n"
            "  - name: my-collector\n"
            "    path: .strata/plugins/my_collector.py\n"
            "    class: MyCollector\n"
            "    type: collector\n"
        )
        result = CollectorPluginLoader.load(tmp_path)
        assert len(result) == 1
        assert result[0].get_collector_name() == "my"

    def test_lockfile_parser_missing_file_raises(self, tmp_path):
        """Phase 3: lockfile_parser with a missing file raises PlatformError."""
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "collectors.yaml").write_text(
            "collectors:\n  - name: cargo\n    path: .strata/plugins/cargo.py\n    type: lockfile_parser\n"
        )
        with pytest.raises(PlatformError, match="file not found"):
            CollectorPluginLoader.load(tmp_path)

    def test_raises_on_missing_plugin_file(self, tmp_path):
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "collectors.yaml").write_text(
            "collectors:\n"
            "  - name: missing\n"
            "    path: .strata/plugins/does_not_exist.py\n"
            "    class: SomeCollector\n"
            "    type: collector\n"
        )
        with pytest.raises(PlatformError, match="file not found"):
            CollectorPluginLoader.load(tmp_path)

    def test_raises_on_missing_class_name(self, tmp_path):
        plugins_dir = tmp_path / ".strata" / "plugins"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "x.py").write_text("pass\n")
        (tmp_path / ".strata" / "collectors.yaml").write_text(
            "collectors:\n  - name: bad\n    path: .strata/plugins/x.py\n    type: collector\n"  # no class key
        )
        with pytest.raises(PlatformError, match="'class' is required"):
            CollectorPluginLoader.load(tmp_path)

    def test_raises_on_wrong_base_class(self, tmp_path):
        plugins_dir = tmp_path / ".strata" / "plugins"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "bad.py").write_text("class NotACollector: pass\n")
        (tmp_path / ".strata" / "collectors.yaml").write_text(
            "collectors:\n"
            "  - name: bad\n"
            "    path: .strata/plugins/bad.py\n"
            "    class: NotACollector\n"
            "    type: collector\n"
        )
        with pytest.raises(PlatformError, match="subclass of BaseSbomCollector"):
            CollectorPluginLoader.load(tmp_path)

    def test_raises_on_unknown_type(self, tmp_path):
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "collectors.yaml").write_text(
            "collectors:\n  - name: bad\n    path: .strata/plugins/x.py\n    class: X\n    type: unknown_type\n"
        )
        with pytest.raises(PlatformError, match="unknown type"):
            CollectorPluginLoader.load(tmp_path)

    def test_raises_on_invalid_yaml(self, tmp_path):
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "collectors.yaml").write_text("{{{not yaml at all\n")
        with pytest.raises(PlatformError, match="Failed to parse"):
            CollectorPluginLoader.load(tmp_path)


# ===========================================================================
# SbomBuilder — updated default collector count
# ===========================================================================


class TestSbomBuilderDefaultCollectors:
    def test_default_collectors_count_is_seven(self):
        from strata.builders.sbom_builder import SbomBuilder

        builder = SbomBuilder()
        assert len(builder._collectors) == 7

    def test_default_collectors_includes_compose(self):
        from strata.builders.sbom_builder import SbomBuilder

        builder = SbomBuilder()
        names = [c.get_collector_name() for c in builder._collectors]
        assert "compose" in names

    def test_default_collectors_includes_terraform_module(self):
        from strata.builders.sbom_builder import SbomBuilder

        builder = SbomBuilder()
        names = [c.get_collector_name() for c in builder._collectors]
        assert "terraform-module" in names
