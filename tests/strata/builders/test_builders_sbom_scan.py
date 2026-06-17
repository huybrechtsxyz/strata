"""Tests for HelmChartFileCollector and scan mode."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from strata.builders.sbom.helm_chart_file_collector import HelmChartFileCollector
from strata.builders.sbom_builder import SbomBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_platform():
    """Minimal mock platform model — HelmChartFileCollector ignores it."""
    platform = MagicMock()
    platform.spec = MagicMock()
    platform.spec.modules = []
    platform.spec.provisioners = []
    return platform


def _write_chart(path: Path, chart_data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(chart_data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# HelmChartFileCollector
# ---------------------------------------------------------------------------


class TestHelmChartFileCollector:
    def test_empty_directory(self, tmp_path):
        collector = HelmChartFileCollector()
        result = collector.collect(_make_platform(), tmp_path, tmp_path)
        assert result == []

    def test_nonexistent_path(self, tmp_path):
        collector = HelmChartFileCollector()
        result = collector.collect(_make_platform(), tmp_path, tmp_path / "nope")
        assert result == []

    def test_single_chart(self, tmp_path):
        _write_chart(
            tmp_path / "charts" / "traefik" / "Chart.yaml",
            {"apiVersion": "v2", "name": "traefik", "version": "28.3.0"},
        )
        collector = HelmChartFileCollector()
        result = collector.collect(_make_platform(), tmp_path, tmp_path)

        assert len(result) == 1
        assert result[0].name == "traefik"
        assert result[0].version == "28.3.0"
        assert "pkg:helm/traefik@28.3.0" in result[0].purl

    def test_chart_with_dependencies(self, tmp_path):
        _write_chart(
            tmp_path / "Chart.yaml",
            {
                "apiVersion": "v2",
                "name": "my-app",
                "version": "1.0.0",
                "dependencies": [
                    {
                        "name": "redis",
                        "version": "17.0.0",
                        "repository": "https://charts.bitnami.com/bitnami",
                    },
                    {
                        "name": "common",
                        "version": "2.1.0",
                        "repository": "https://charts.bitnami.com/bitnami",
                    },
                ],
            },
        )
        collector = HelmChartFileCollector()
        result = collector.collect(_make_platform(), tmp_path, tmp_path)

        assert len(result) == 3
        names = {c.name for c in result}
        assert names == {"my-app", "redis", "common"}

    def test_skips_local_file_dependencies(self, tmp_path):
        _write_chart(
            tmp_path / "Chart.yaml",
            {
                "apiVersion": "v2",
                "name": "parent",
                "version": "1.0.0",
                "dependencies": [
                    {"name": "local-lib", "version": "0.1.0", "repository": "file://../local-lib"},
                ],
            },
        )
        collector = HelmChartFileCollector()
        result = collector.collect(_make_platform(), tmp_path, tmp_path)

        # Only parent chart — local dep is skipped
        assert len(result) == 1
        assert result[0].name == "parent"

    def test_deduplicates_by_purl(self, tmp_path):
        chart_data = {"apiVersion": "v2", "name": "traefik", "version": "28.3.0"}
        _write_chart(tmp_path / "a" / "Chart.yaml", chart_data)
        _write_chart(tmp_path / "b" / "Chart.yaml", chart_data)

        collector = HelmChartFileCollector()
        result = collector.collect(_make_platform(), tmp_path, tmp_path)
        assert len(result) == 1

    def test_skips_node_modules(self, tmp_path):
        _write_chart(
            tmp_path / "node_modules" / "something" / "Chart.yaml",
            {"apiVersion": "v2", "name": "hidden", "version": "1.0.0"},
        )
        collector = HelmChartFileCollector()
        result = collector.collect(_make_platform(), tmp_path, tmp_path)
        assert result == []

    def test_chart_without_version(self, tmp_path):
        _write_chart(tmp_path / "Chart.yaml", {"apiVersion": "v2", "name": "minimal"})
        collector = HelmChartFileCollector()
        result = collector.collect(_make_platform(), tmp_path, tmp_path)
        assert len(result) == 1
        assert result[0].version is None
        assert "pkg:helm/minimal" in result[0].purl

    def test_invalid_chart_yaml_warns(self, tmp_path):
        chart_file = tmp_path / "Chart.yaml"
        chart_file.write_text("not: [valid: yaml: {{", encoding="utf-8")

        collector = HelmChartFileCollector()
        result = collector.collect(_make_platform(), tmp_path, tmp_path)
        assert result == []
        assert len(collector.get_warnings()) == 1

    def test_collector_name(self):
        assert HelmChartFileCollector().get_collector_name() == "helm"


# ---------------------------------------------------------------------------
# Scan mode (SbomBuilder.scan + scan_inventory)
# ---------------------------------------------------------------------------


class TestSbomBuilderScan:
    def test_scan_empty_directory(self, tmp_path):
        builder = SbomBuilder(collectors=[])
        ok = builder.scan(tmp_path)
        assert ok
        # sbom.json created (with 0 components)
        assert (tmp_path / "sbom.json").exists()
        data = json.loads((tmp_path / "sbom.json").read_text())
        assert data.get("components", []) == []

    def test_scan_with_chart(self, tmp_path):
        _write_chart(
            tmp_path / "Chart.yaml",
            {"apiVersion": "v2", "name": "my-chart", "version": "2.0.0"},
        )
        builder = SbomBuilder()
        ok = builder.scan(tmp_path)
        assert ok
        data = json.loads((tmp_path / "sbom.json").read_text())
        names = [c["name"] for c in data["components"]]
        assert "my-chart" in names

    def test_scan_output_file(self, tmp_path):
        _write_chart(
            tmp_path / "Chart.yaml",
            {"apiVersion": "v2", "name": "test", "version": "1.0.0"},
        )
        out = tmp_path / "output" / "custom.json"
        builder = SbomBuilder()
        ok = builder.scan(tmp_path, output_file=out)
        assert ok
        assert out.exists()
        assert not (tmp_path / "sbom.json").exists()

    def test_scan_inventory(self, tmp_path):
        _write_chart(
            tmp_path / "charts" / "nginx" / "Chart.yaml",
            {"apiVersion": "v2", "name": "nginx", "version": "15.0.0"},
        )
        builder = SbomBuilder()
        text = builder.scan_inventory(tmp_path)
        assert text is not None
        assert "nginx" in text
        assert "15.0.0" in text
        assert "Helm Charts" in text

    def test_scan_no_deps_excludes_lockfiles(self, tmp_path):
        # Write a requirements.txt that would normally be picked up
        (tmp_path / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
        builder = SbomBuilder(no_deps=True)
        text = builder.scan_inventory(tmp_path)
        assert text is not None
        assert "flask" not in text

    def test_scan_includes_compose_images(self, tmp_path):
        compose = {"services": {"web": {"image": "nginx:1.27.0"}}}
        (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose), encoding="utf-8")
        builder = SbomBuilder()
        text = builder.scan_inventory(tmp_path)
        assert text is not None
        assert "web" in text

    def test_scan_with_terraform_files(self, tmp_path):
        tf_content = """
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "4.12.0"
    }
  }
}
"""
        (tmp_path / "main.tf").write_text(tf_content, encoding="utf-8")
        builder = SbomBuilder()
        text = builder.scan_inventory(tmp_path)
        assert text is not None
        assert "azurerm" in text
