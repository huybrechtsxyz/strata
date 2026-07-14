"""Tests for the ``versions`` command group."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from strata.commands.cli_versions import versions_group
from strata.models.common_models import PlatformKind

_API_VERSION = "strata.huybrechts.xyz/v1"


def _make_manifest(tmp_path: Path, ring: str = "dev", images: dict | None = None) -> Path:
    """Write a minimal kind: version YAML file and return its path."""
    doc = {
        "apiVersion": _API_VERSION,
        "kind": PlatformKind.VERSION_MANIFEST.value,
        "meta": {"name": ring},
        "spec": {
            "ring": ring,
            "pins": {
                "images": images or {"app": "v1.0.0"},
                "charts": {"traefik": "28.2.0"},
                "remotes": {"iac_core": "v2.5.0"},
            },
        },
    }
    p = tmp_path / f"{ring}.yaml"
    p.write_text(yaml.dump(doc))
    return p


# ── init ──────────────────────────────────────────────────────────────────────


class TestVersionsInit:
    def test_creates_file_at_default_path(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(versions_group, ["init", "--ring", "dev", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        dest = tmp_path / "versions" / "dev.yaml"
        assert dest.exists()
        doc = yaml.safe_load(dest.read_text())
        assert doc["kind"] == PlatformKind.VERSION_MANIFEST.value
        assert doc["spec"]["ring"] == "dev"

    def test_creates_file_at_explicit_out(self, tmp_path):
        out = tmp_path / "custom" / "my-manifest.yaml"
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["init", "--ring", "prd", "--out", str(out), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()

    def test_json_output(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["init", "--ring", "dev", "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["ring"] == "dev"
        assert "file" in data

    def test_text_output(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["init", "--ring", "dev", "--output", "text", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert result.output.strip().endswith("dev.yaml")

    def test_fails_if_exists_without_force(self, tmp_path):
        runner = CliRunner()
        runner.invoke(versions_group, ["init", "--ring", "dev", "--work-path", str(tmp_path)])
        result = runner.invoke(versions_group, ["init", "--ring", "dev", "--work-path", str(tmp_path)])
        assert result.exit_code == 1

    def test_force_overwrites_existing(self, tmp_path):
        runner = CliRunner()
        runner.invoke(versions_group, ["init", "--ring", "dev", "--work-path", str(tmp_path)])
        result = runner.invoke(versions_group, ["init", "--ring", "dev", "--force", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output


# ── export ────────────────────────────────────────────────────────────────────


class TestVersionsExport:
    def test_console_output(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        result = runner.invoke(versions_group, ["export", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "IMAGE" in result.output
        assert "app" in result.output
        assert "v1.0.0" in result.output

    def test_json_output(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["export", "--file", str(p), "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert "image" in data["pins"]
        assert data["pins"]["image"]["app"] == "v1.0.0"

    def test_text_output(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["export", "--file", str(p), "--output", "text", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        lines = result.output.strip().splitlines()
        assert any(ln.startswith("image/app=") for ln in lines)
        assert any(ln.startswith("helm_chart/traefik=") for ln in lines)
        assert any(ln.startswith("remote/iac_core=") for ln in lines)

    def test_fails_missing_file(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["export", "--file", str(tmp_path / "nonexistent.yaml"), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_empty_pins_shows_placeholder(self, tmp_path):
        doc = {
            "apiVersion": _API_VERSION,
            "kind": PlatformKind.VERSION_MANIFEST.value,
            "meta": {"name": "dev"},
            "spec": {"ring": "dev", "pins": {}},
        }
        p = tmp_path / "dev.yaml"
        p.write_text(yaml.dump(doc))
        runner = CliRunner()
        result = runner.invoke(versions_group, ["export", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "no resolved pins" in result.output


# ── apply ─────────────────────────────────────────────────────────────────────


class TestVersionsApply:
    def test_creates_lock_file_alongside_manifest(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        result = runner.invoke(versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        lock_path = tmp_path / "dev.lock.yaml"
        assert lock_path.exists()
        doc = yaml.safe_load(lock_path.read_text())
        assert doc["kind"] == PlatformKind.VERSION_LOCK.value
        assert doc["spec"]["ring"] == "dev"

    def test_lock_pins_content(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        runner.invoke(versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)])
        lock_path = tmp_path / "dev.lock.yaml"
        doc = yaml.safe_load(lock_path.read_text())
        pins = doc["spec"]["pins"]
        pin_map = {(pin["target"]["type"], pin["target"]["name"]): pin["version"] for pin in pins}
        assert pin_map[("image", "app")] == "v1.0.0"
        assert pin_map[("helm_chart", "traefik")] == "28.2.0"
        assert pin_map[("remote", "iac_core")] == "v2.5.0"

    def test_creates_lock_at_explicit_out(self, tmp_path):
        p = _make_manifest(tmp_path)
        out = tmp_path / "locks" / "dev.lock.yaml"
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["apply", "--file", str(p), "--out", str(out), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()

    def test_json_output(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["apply", "--file", str(p), "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["ring"] == "dev"
        assert data["pins_count"] == 3  # app + traefik + iac_core

    def test_text_output(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["apply", "--file", str(p), "--output", "text", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert result.output.strip().endswith("dev.lock.yaml")

    def test_fails_if_lock_exists_without_force(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        runner.invoke(versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)])
        result = runner.invoke(versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 1

    def test_force_overwrites_existing_lock(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        runner.invoke(versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)])
        result = runner.invoke(
            versions_group,
            ["apply", "--file", str(p), "--force", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output

    def test_fails_if_file_missing(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["apply", "--file", str(tmp_path / "missing.yaml"), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_fails_if_given_version_lock_not_manifest(self, tmp_path):
        """Passing a version-lock file to apply should fail with exit 1."""
        doc = {
            "apiVersion": _API_VERSION,
            "kind": PlatformKind.VERSION_LOCK.value,
            "meta": {"name": "dev"},
            "spec": {
                "ring": "dev",
                "pins": [{"target": {"type": "image", "name": "app"}, "version": "v1.0.0"}],
            },
        }
        p = tmp_path / "dev.lock.yaml"
        p.write_text(yaml.dump(doc))
        runner = CliRunner()
        result = runner.invoke(versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 1

    def test_lock_has_generated_metadata(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        runner.invoke(versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)])
        doc = yaml.safe_load((tmp_path / "dev.lock.yaml").read_text())
        ann = doc["meta"].get("annotations", {})
        assert "generated_at" in ann
        assert ann["generated_by"] == "strata versions apply"


# ── refresh ───────────────────────────────────────────────────────────────────


def _write_module(
    path: Path, name: str, chart_name: str = "", chart_version: str = "", services: list | None = None
) -> None:
    """Write a kind:module YAML file for scanning tests."""
    source: dict
    if chart_name:
        source = {"chart_repository": "https://example.com/charts", "chart_name": chart_name}
        if chart_version:
            source["chart_version"] = chart_version
    else:
        source = {"repository": "my-repo", "source_path": "modules"}

    doc: dict = {
        "apiVersion": _API_VERSION,
        "kind": "module",
        "meta": {"name": name},
        "spec": {"source": source},
    }
    if services:
        doc["spec"]["services"] = services
    path.write_text(yaml.dump(doc))


def _write_workspace(path: Path, repos: list[str]) -> None:
    """Write a kind:workspace YAML file for scanning tests."""
    provisioners = [
        {"name": f"prov_{r}", "provisioner": "terraform", "source": {"repository": r, "source_path": "tf"}}
        for r in repos
    ]
    doc = {
        "apiVersion": _API_VERSION,
        "kind": "workspace",
        "meta": {"name": "ws"},
        "spec": {"provisioners": provisioners},
    }
    path.write_text(yaml.dump(doc))


def _write_configuration(path: Path, remotes: list[tuple[str, str]]) -> None:
    """Write a kind:configuration YAML file for scanning tests."""
    remote_list = [
        {"name": name, "type": "gitops", "repository": "https://git.example.com/r", "reference": ref}
        for name, ref in remotes
    ]
    doc = {
        "apiVersion": _API_VERSION,
        "kind": "configuration",
        "meta": {"name": "cfg"},
        "spec": {"remotes": remote_list},
    }
    path.write_text(yaml.dump(doc))


def _write_environment(path: Path, remote_overrides: list | None = None, module_overrides: list | None = None) -> None:
    """Write a kind:environment YAML file for scanning tests."""
    overrides: dict = {}
    if remote_overrides:
        overrides["remotes"] = [{"remote": r, "reference": ref} for r, ref in remote_overrides]
    if module_overrides:
        overrides["modules"] = module_overrides
    doc = {
        "apiVersion": _API_VERSION,
        "kind": "environment",
        "meta": {"name": "dev"},
        "spec": {"overrides": overrides},
    }
    path.write_text(yaml.dump(doc))


class TestVersionsRefresh:
    def test_adds_new_chart_from_module(self, tmp_path):
        p = _make_manifest(tmp_path, images={})  # no charts in manifest initially
        manifest_doc = yaml.safe_load(p.read_text())
        manifest_doc["spec"]["pins"]["charts"] = {}
        p.write_text(yaml.dump(manifest_doc))

        _write_module(tmp_path / "nginx.yaml", name="nginx", chart_name="nginx", chart_version="1.27.0")

        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        updated = yaml.safe_load(p.read_text())
        assert "nginx" in updated["spec"]["pins"]["charts"]
        assert updated["spec"]["pins"]["charts"]["nginx"] == "1.27.0"

    def test_adds_new_image_from_module_services(self, tmp_path):
        p = _make_manifest(tmp_path, images={})
        _write_module(
            tmp_path / "myapp.yaml",
            name="myapp",
            services=[{"name": "api", "image": "ghcr.io/org/api:v3.0.0"}],
        )

        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        updated = yaml.safe_load(p.read_text())
        assert updated["spec"]["pins"]["images"]["api"] == "ghcr.io/org/api:v3.0.0"

    def test_adds_new_remote_from_workspace(self, tmp_path):
        p = _make_manifest(tmp_path, images={})
        manifest_doc = yaml.safe_load(p.read_text())
        manifest_doc["spec"]["pins"]["remotes"] = {}
        p.write_text(yaml.dump(manifest_doc))

        _write_workspace(tmp_path / "ws.yaml", repos=["iac_new"])

        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        updated = yaml.safe_load(p.read_text())
        assert "iac_new" in updated["spec"]["pins"]["remotes"]

    def test_preserves_existing_pin_versions(self, tmp_path):
        p = _make_manifest(tmp_path)  # has app=v1.0.0, traefik=28.2.0, iac_core=v2.5.0
        _write_module(tmp_path / "traefik.yaml", name="traefik", chart_name="traefik", chart_version="99.0.0")

        runner = CliRunner()
        runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        updated = yaml.safe_load(p.read_text())
        # Existing value must be kept, NOT overwritten with seed
        assert updated["spec"]["pins"]["charts"]["traefik"] == "28.2.0"

    def test_reports_stale_entries(self, tmp_path):
        p = _make_manifest(tmp_path)  # has app=v1.0.0, traefik=28.2.0, iac_core=v2.5.0
        # No module or workspace files → everything in manifest is stale

        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "stale" in result.output.lower() or "⚠" in result.output
        # Stale entries should still be in the file (not removed by default)
        updated = yaml.safe_load(p.read_text())
        assert "app" in updated["spec"]["pins"]["images"]

    def test_remove_stale_flag_deletes_entries(self, tmp_path):
        p = _make_manifest(tmp_path)  # has app=v1.0.0
        # No module/workspace files → app is stale

        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["refresh", "--file", str(p), "--remove-stale", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        updated = yaml.safe_load(p.read_text())
        assert "app" not in (updated["spec"]["pins"].get("images") or {})

    def test_dry_run_does_not_write(self, tmp_path):
        p = _make_manifest(tmp_path, images={})
        original = p.read_text()
        _write_module(tmp_path / "nginx.yaml", name="nginx", chart_name="nginx")

        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["refresh", "--file", str(p), "--dry-run", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert p.read_text() == original  # file unchanged

    def test_json_output(self, tmp_path):
        p = _make_manifest(tmp_path, images={})
        _write_module(tmp_path / "nginx.yaml", name="nginx", chart_name="nginx", chart_version="1.27.0")

        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["refresh", "--file", str(p), "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert "nginx" in data["added"]["charts"]

    def test_text_output(self, tmp_path):
        p = _make_manifest(tmp_path, images={})
        _write_module(tmp_path / "nginx.yaml", name="nginx", chart_name="nginx")

        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["refresh", "--file", str(p), "--output", "text", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "added:charts/nginx" in result.output

    def test_no_changes_reports_up_to_date(self, tmp_path):
        p = _make_manifest(tmp_path, images={})
        # Manifest has no images; no module files → no change needed
        manifest_doc = yaml.safe_load(p.read_text())
        manifest_doc["spec"]["pins"] = {"images": {}, "charts": {}, "remotes": {}}
        p.write_text(yaml.dump(manifest_doc))

        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "up to date" in result.output

    def test_adds_chart_from_workspace_provisioner(self, tmp_path):
        """Workspace provisioner with chart_repository → chart target discovered."""
        p = _make_manifest(tmp_path, images={})
        manifest_doc = yaml.safe_load(p.read_text())
        manifest_doc["spec"]["pins"]["charts"] = {}
        p.write_text(yaml.dump(manifest_doc))

        # Workspace with a Helm-chart-based provisioner
        ws_doc = {
            "apiVersion": _API_VERSION,
            "kind": "workspace",
            "meta": {"name": "ws"},
            "spec": {
                "provisioners": [
                    {
                        "name": "app-chart",
                        "provisioner": "helm",
                        "source": {
                            "chart_repository": "https://charts.example.com",
                            "chart_name": "myapp",
                            "chart_version": "5.0.0",
                        },
                    }
                ]
            },
        }
        (tmp_path / "ws.yaml").write_text(yaml.dump(ws_doc))

        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        updated = yaml.safe_load(p.read_text())
        assert "app-chart" in updated["spec"]["pins"]["charts"]
        assert updated["spec"]["pins"]["charts"]["app-chart"] == "5.0.0"

    def test_adds_remotes_from_configuration(self, tmp_path):
        """kind:configuration spec.remotes[] → remote targets discovered with reference as seed."""
        p = _make_manifest(tmp_path, images={})
        manifest_doc = yaml.safe_load(p.read_text())
        manifest_doc["spec"]["pins"]["remotes"] = {}
        p.write_text(yaml.dump(manifest_doc))

        _write_configuration(tmp_path / "config.yaml", remotes=[("platform_core", "v3.1.0"), ("iac_base", "v1.5.0")])

        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        updated = yaml.safe_load(p.read_text())
        assert updated["spec"]["pins"]["remotes"]["platform_core"] == "v3.1.0"
        assert updated["spec"]["pins"]["remotes"]["iac_base"] == "v1.5.0"

    def test_adds_remote_overrides_from_environment(self, tmp_path):
        """kind:environment spec.overrides.remotes[] → remote targets with reference as seed."""
        p = _make_manifest(tmp_path, images={})
        manifest_doc = yaml.safe_load(p.read_text())
        manifest_doc["spec"]["pins"]["remotes"] = {}
        p.write_text(yaml.dump(manifest_doc))

        _write_environment(tmp_path / "env.yaml", remote_overrides=[("iac_core", "v2.6.0")])

        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        updated = yaml.safe_load(p.read_text())
        assert updated["spec"]["pins"]["remotes"]["iac_core"] == "v2.6.0"

    def test_adds_chart_override_from_environment(self, tmp_path):
        """kind:environment spec.overrides.modules[].chart_version → chart target."""
        p = _make_manifest(tmp_path, images={})
        manifest_doc = yaml.safe_load(p.read_text())
        manifest_doc["spec"]["pins"]["charts"] = {}
        p.write_text(yaml.dump(manifest_doc))

        _write_environment(
            tmp_path / "env.yaml",
            module_overrides=[{"module": "traefik", "chart_version": "30.0.0"}],
        )

        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        updated = yaml.safe_load(p.read_text())
        assert updated["spec"]["pins"]["charts"]["traefik"] == "30.0.0"

    def test_adds_image_override_from_environment(self, tmp_path):
        """kind:environment spec.overrides.modules[].services[].image → image target."""
        p = _make_manifest(tmp_path, images={})

        _write_environment(
            tmp_path / "env.yaml",
            module_overrides=[
                {
                    "module": "myapp",
                    "services": [{"name": "api", "image": "ghcr.io/org/api:v4.0.0"}],
                }
            ],
        )

        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        updated = yaml.safe_load(p.read_text())
        assert updated["spec"]["pins"]["images"]["api"] == "ghcr.io/org/api:v4.0.0"

    def test_fails_on_missing_manifest(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["refresh", "--file", str(tmp_path / "missing.yaml"), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_fails_if_given_lock_file(self, tmp_path):
        doc = {
            "apiVersion": _API_VERSION,
            "kind": PlatformKind.VERSION_LOCK.value,
            "meta": {"name": "dev"},
            "spec": {"ring": "dev", "pins": []},
        }
        p = tmp_path / "dev.lock.yaml"
        p.write_text(yaml.dump(doc))
        runner = CliRunner()
        result = runner.invoke(versions_group, ["refresh", "--file", str(p), "--work-path", str(tmp_path)])
        assert result.exit_code == 1
