"""Tests for the ``versions`` command group."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
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
        result = runner.invoke(
            versions_group, ["init", "--ring", "dev", "--force", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output


# ── export ────────────────────────────────────────────────────────────────────


class TestVersionsExport:
    def test_console_output(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            versions_group, ["export", "--file", str(p), "--work-path", str(tmp_path)]
        )
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
        result = runner.invoke(
            versions_group, ["export", "--file", str(p), "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert "no resolved pins" in result.output


# ── apply ─────────────────────────────────────────────────────────────────────


class TestVersionsApply:
    def test_creates_lock_file_alongside_manifest(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)]
        )
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
        result = runner.invoke(
            versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)]
        )
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
        result = runner.invoke(
            versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 1

    def test_lock_has_generated_metadata(self, tmp_path):
        p = _make_manifest(tmp_path)
        runner = CliRunner()
        runner.invoke(versions_group, ["apply", "--file", str(p), "--work-path", str(tmp_path)])
        doc = yaml.safe_load((tmp_path / "dev.lock.yaml").read_text())
        ann = doc["meta"].get("annotations", {})
        assert "generated_at" in ann
        assert ann["generated_by"] == "strata versions apply"
