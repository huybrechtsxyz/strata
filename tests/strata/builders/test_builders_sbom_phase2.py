"""Unit tests for Phase 2: SbomBuilder.render_inventory() and strata guide Phase 8."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strata.builders.sbom_builder import _INVENTORY_GROUP_LABELS, SbomBuilder
from strata.models.sbom_model import SbomComponentModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _comp(
    name: str,
    version: str | None,
    purl: str,
    source_collector: str,
    component_type: str = "library",
    properties: dict | None = None,
) -> SbomComponentModel:
    return SbomComponentModel(
        name=name,
        version=version,
        purl=purl,
        source_collector=source_collector,
        component_type=component_type,
        properties=properties or {},
    )


def _floating_comp(name: str, purl: str, source_collector: str) -> SbomComponentModel:
    return _comp(
        name=name,
        version="latest",
        purl=purl,
        source_collector=source_collector,
        component_type="container",
        properties={"strata:tag-stability": "floating"},
    )


def _mock_deployment_service(name: str = "xyz-production", build_path: Path | None = None):
    svc = MagicMock()
    svc.is_validated.return_value = True
    svc.model.meta.name = name
    if build_path is not None:
        svc.get_build_path.return_value = build_path
    return svc


# ---------------------------------------------------------------------------
# _INVENTORY_GROUP_LABELS
# ---------------------------------------------------------------------------


class TestInventoryGroupLabels:
    def test_all_phase1_collectors_have_labels(self):
        assert _INVENTORY_GROUP_LABELS["image"] == "Container Images"
        assert _INVENTORY_GROUP_LABELS["compose"] == "Compose Services"
        assert _INVENTORY_GROUP_LABELS["helm"] == "Helm Charts"
        assert _INVENTORY_GROUP_LABELS["terraform"] == "Terraform Providers"
        assert _INVENTORY_GROUP_LABELS["terraform-module"] == "Terraform Modules"
        assert _INVENTORY_GROUP_LABELS["ansible"] == "Ansible Collections"
        assert _INVENTORY_GROUP_LABELS["deps"] == "Application Dependencies"


# ---------------------------------------------------------------------------
# SbomBuilder._format_inventory
# ---------------------------------------------------------------------------


class TestFormatInventory:
    """Direct tests for the internal formatter — no I/O."""

    def _format(self, components, deployment_name="test-platform"):
        builder = SbomBuilder(collectors=[])
        return builder._format_inventory(components, deployment_name)

    def test_empty_components_shows_zero_total(self):
        text = self._format([])
        assert "Total: 0 components" in text

    def test_single_component_shows_one(self):
        text = self._format([_comp("nginx", "1.27.0", "pkg:docker/nginx@1.27.0", "image", "container")])
        assert "Total: 1 component" in text
        assert "nginx" in text
        assert "1.27.0" in text

    def test_group_heading_uses_labels(self):
        text = self._format(
            [
                _comp("nginx", "1.27.0", "pkg:docker/nginx@1.27.0", "image", "container"),
            ]
        )
        assert "Container Images (1)" in text

    def test_unknown_collector_falls_back_to_title_case(self):
        text = self._format(
            [
                _comp("mypkg", "1.0", "pkg:pypi/mypkg@1.0", "custom-scanner"),
            ]
        )
        assert "Custom-Scanner" in text or "custom-scanner" in text.lower()

    def test_floating_tag_shown_with_warning(self):
        text = self._format(
            [
                _floating_comp("redis", "pkg:docker/redis@latest", "compose"),
            ]
        )
        assert "⚠ floating" in text
        assert "1 floating tag" in text

    def test_multiple_floating_tags_plural(self):
        text = self._format(
            [
                _floating_comp("redis", "pkg:docker/redis@latest", "image"),
                _floating_comp("nginx", "pkg:docker/nginx@latest", "image"),
            ]
        )
        assert "2 floating tags" in text

    def test_none_version_shown_as_dash(self):
        text = self._format([_comp("mypkg", None, "pkg:pypi/mypkg", "deps")])
        assert "—" in text

    def test_deployment_name_in_header(self):
        text = self._format([], deployment_name="xyz-production")
        assert "xyz-production" in text

    def test_no_deployment_name_still_shows_header(self):
        text = self._format([], deployment_name=None)
        assert "Platform Inventory" in text

    def test_repository_url_extracted_from_helm_purl(self):
        text = self._format(
            [
                _comp(
                    "traefik",
                    "28.3.0",
                    "pkg:helm/traefik@28.3.0?repository_url=https://helm.traefik.io/traefik",
                    "helm",
                    "library",
                )
            ]
        )
        assert "helm.traefik.io" in text

    def test_terraform_purl_shows_registry(self):
        text = self._format(
            [
                _comp(
                    "azurerm",
                    "4.12.0",
                    "pkg:terraform/hashicorp/azurerm@4.12.0?repository_url=registry.terraform.io",
                    "terraform",
                    "library",
                )
            ]
        )
        assert "registry.terraform.io" in text

    def test_docker_purl_without_repository_url_shows_docker_io(self):
        text = self._format([_comp("nginx", "1.27.0", "pkg:docker/nginx@1.27.0", "image", "container")])
        assert "docker.io" in text

    def test_github_purl_shows_github_com(self):
        text = self._format([_comp("my-module", None, "pkg:github/org/repo", "terraform-module", "library")])
        assert "github.com" in text

    def test_groups_are_ordered_by_collector_insertion(self):
        """Groups must appear in the order their source_collector first appeared."""
        components = [
            _comp("nginx", "1.27.0", "pkg:docker/nginx@1.27.0", "image", "container"),
            _comp("traefik", "28.3.0", "pkg:helm/traefik@28.3.0", "helm", "library"),
            _comp("azurerm", "4.12.0", "pkg:terraform/hashicorp/azurerm@4.12.0", "terraform", "library"),
        ]
        text = self._format(components)
        img_pos = text.index("Container Images")
        helm_pos = text.index("Helm Charts")
        tf_pos = text.index("Terraform Providers")
        assert img_pos < helm_pos < tf_pos

    def test_total_count_matches_components(self):
        components = [
            _comp("a", "1", "pkg:docker/a@1", "image", "container"),
            _comp("b", "2", "pkg:docker/b@2", "image", "container"),
            _comp("c", "3", "pkg:helm/c@3", "helm"),
        ]
        text = self._format(components)
        assert "Total: 3 components" in text


# ---------------------------------------------------------------------------
# SbomBuilder.render_inventory — integration with collector pipeline
# ---------------------------------------------------------------------------


class TestRenderInventory:
    def _builder_with_one_component(self) -> tuple[SbomBuilder, SbomComponentModel]:
        comp = _comp("nginx", "1.27.0", "pkg:docker/nginx@1.27.0", "image", "container")
        collector = MagicMock()
        collector.collect.return_value = [comp]
        collector.get_warnings.return_value = []
        collector.get_collector_name.return_value = "image"
        builder = SbomBuilder(collectors=[collector])
        return builder, comp

    def test_render_inventory_returns_string(self, tmp_path):
        builder, _ = self._builder_with_one_component()
        platform_model = MagicMock()
        deployment_svc = _mock_deployment_service(build_path=tmp_path)

        with patch.object(builder, "render_inventory", wraps=builder.render_inventory):
            text = builder.render_inventory(
                deployment_service=deployment_svc,
                work_path=tmp_path,
                build_path=tmp_path,
                platform_model=platform_model,
            )

        assert text is not None
        assert isinstance(text, str)
        assert "nginx" in text

    def test_render_inventory_no_platform_model_missing_file_returns_none(self, tmp_path):
        builder = SbomBuilder(collectors=[])
        deployment_svc = _mock_deployment_service(build_path=tmp_path)
        # No platform.json written → should fail gracefully

        text = builder.render_inventory(
            deployment_service=deployment_svc,
            work_path=tmp_path,
            build_path=tmp_path,
        )
        assert text is None
        assert builder.get_errors()

    def test_render_inventory_collector_warnings_go_to_messages(self, tmp_path):
        collector = MagicMock()
        collector.collect.return_value = []
        collector.get_warnings.return_value = ["some warning"]
        collector.get_collector_name.return_value = "image"
        builder = SbomBuilder(collectors=[collector])
        platform_model = MagicMock()
        deployment_svc = _mock_deployment_service(build_path=tmp_path)

        text = builder.render_inventory(
            deployment_service=deployment_svc,
            work_path=tmp_path,
            build_path=tmp_path,
            platform_model=platform_model,
        )
        assert text is not None
        msgs = builder.drain_messages()
        assert any("some warning" in m for m in msgs)


# ---------------------------------------------------------------------------
# Guide Phase 8 — _evaluate_checklist
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path, solution: dict | None = None):
    strata_dir = tmp_path / ".strata"
    strata_dir.mkdir(exist_ok=True)
    if solution is not None:
        (strata_dir / "solution.json").write_text(json.dumps(solution))


def _make_solution_json(
    name: str = "my-platform",
    repositories: list | None = None,
    profiles: list | None = None,
) -> dict:
    return {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "workspace",
        "meta": {"name": name, "annotations": {}, "labels": {}},
        "spec": {
            "repositories": repositories or [],
            "profiles": profiles or [],
            "deployments": [],
            "networks": [],
            "providers": [],
            "solution_id": "test-id-001",
        },
    }


def _make_profile(name: str = "prd", active: bool = True, config_paths: list | None = None) -> dict:
    return {
        "name": name,
        "active": active,
        "configfile_paths": config_paths or [],
        "envfile_paths": [],
        "datafile_paths": [],
        "secretfile_paths": [],
    }


def _sbom_json(component_count: int = 2) -> str:
    return json.dumps(
        {
            "bomFormat": "CycloneDX",
            "components": [{"type": "library", "name": f"comp-{i}", "version": "1.0"} for i in range(component_count)],
        }
    )


try:
    from strata.commands.cli_guide import guide_command

    GUIDE_MISSING = False
except ImportError:
    guide_command = None  # type: ignore[assignment]
    GUIDE_MISSING = True

from click.testing import CliRunner

pytestmark_guide = pytest.mark.skipif(GUIDE_MISSING, reason="guide command not yet implemented")


@pytest.mark.skipif(GUIDE_MISSING, reason="guide command not yet implemented")
class TestGuidePhase8:
    """Phase 8 — Platform inventory generated."""

    def _run(self, tmp_path: Path, solution: dict | None = None):
        if solution:
            _make_workspace(tmp_path, solution=solution)
        return CliRunner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )

    def test_phase8_pending_when_no_build_dir(self, tmp_path):
        """Phase 8 is pending when build/ does not exist."""
        solution = _make_solution_json(
            profiles=[_make_profile(active=True, config_paths=[{"name": "cfg", "path": "@r/cfg.yaml"}])]
        )
        result = self._run(tmp_path, solution)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase8 = next(i for i in data["checklist"] if i["phase"] == 8)
        assert phase8["status"] == "pending"

    def test_phase8_pending_when_no_sbom_json(self, tmp_path):
        """Phase 8 is pending when build/ exists but has no sbom.json."""
        solution = _make_solution_json(
            profiles=[_make_profile(active=True, config_paths=[{"name": "cfg", "path": "@r/cfg.yaml"}])]
        )
        (tmp_path / "build" / "my-deployment").mkdir(parents=True)
        (tmp_path / "build" / "my-deployment" / "platform.json").write_text("{}")
        result = self._run(tmp_path, solution)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase8 = next(i for i in data["checklist"] if i["phase"] == 8)
        assert phase8["status"] == "pending"

    def test_phase8_ok_when_sbom_has_components(self, tmp_path):
        """Phase 8 is ok when sbom.json has components."""
        solution = _make_solution_json(
            profiles=[_make_profile(active=True, config_paths=[{"name": "cfg", "path": "@r/cfg.yaml"}])]
        )
        sbom_dir = tmp_path / "build" / "my-deployment"
        sbom_dir.mkdir(parents=True)
        (sbom_dir / "sbom.json").write_text(_sbom_json(component_count=3))
        result = self._run(tmp_path, solution)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase8 = next(i for i in data["checklist"] if i["phase"] == 8)
        assert phase8["status"] == "ok"
        assert "3 components" in (phase8["detail"] or "")

    def test_phase8_warn_when_sbom_empty(self, tmp_path):
        """Phase 8 is ⚠️ when sbom.json exists but components list is empty."""
        solution = _make_solution_json(
            profiles=[_make_profile(active=True, config_paths=[{"name": "cfg", "path": "@r/cfg.yaml"}])]
        )
        sbom_dir = tmp_path / "build" / "my-deployment"
        sbom_dir.mkdir(parents=True)
        (sbom_dir / "sbom.json").write_text(_sbom_json(component_count=0))
        result = self._run(tmp_path, solution)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase8 = next(i for i in data["checklist"] if i["phase"] == 8)
        assert phase8["status"] == "warn"
        assert "empty" in (phase8["detail"] or "")

    def test_phase8_ok_sums_across_multiple_sbom_files(self, tmp_path):
        """Component count is summed when multiple sbom.json files exist."""
        solution = _make_solution_json(
            profiles=[_make_profile(active=True, config_paths=[{"name": "cfg", "path": "@r/cfg.yaml"}])]
        )
        for dep in ("dep-a", "dep-b"):
            d = tmp_path / "build" / dep
            d.mkdir(parents=True)
            (d / "sbom.json").write_text(_sbom_json(component_count=5))
        result = self._run(tmp_path, solution)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase8 = next(i for i in data["checklist"] if i["phase"] == 8)
        assert phase8["status"] == "ok"
        assert "10 components" in (phase8["detail"] or "")

    def test_checklist_has_8_phases(self, tmp_path):
        """The workspace-mode checklist always has exactly 8 phases."""
        solution = _make_solution_json()
        result = self._run(tmp_path, solution)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phases = [i["phase"] for i in data["checklist"]]
        assert phases == list(range(1, 9))
