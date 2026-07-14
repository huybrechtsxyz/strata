"""CLI tests for ``strata versions add`` and ``strata versions lock`` (ADR-0011)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import yaml
from click.testing import CliRunner

from strata.commands.cli_versions import versions_group
from strata.models.common_models import PlatformKind

_API_VERSION = "strata.huybrechts.xyz/v1"


def _make_version_file(tmp_path: Path, ring: str = "dev", images: dict | None = None) -> Path:
    doc = {
        "apiVersion": _API_VERSION,
        "kind": PlatformKind.VERSION_MANIFEST.value,
        "meta": {"name": ring},
        "spec": {
            "ring": ring,
            "pins": {
                "images": images or {"app": "v2.1.0"},
                "charts": {"traefik": "28.2.0"},
            },
        },
    }
    p = tmp_path / f"{ring}.yaml"
    p.write_text(yaml.dump(doc))
    return p


# ── versions add ─────────────────────────────────────────────────────────────


class TestVersionsAddCommand:
    def test_creates_file(self, tmp_path):
        runner = CliRunner()
        out = tmp_path / "v3.0.0.yaml"
        result = runner.invoke(
            versions_group,
            ["add", "--out", str(out), "--ring", "prd", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()

    def test_json_output_success(self, tmp_path):
        runner = CliRunner()
        out = tmp_path / "v1.0.0.yaml"
        result = runner.invoke(
            versions_group,
            ["add", "--out", str(out), "--ring", "dev", "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["ring"] == "dev"
        assert "file" in data

    def test_copies_pins_from_source(self, tmp_path):
        src = _make_version_file(tmp_path, ring="dev")
        out = tmp_path / "v2.0.0.yaml"
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["add", "--out", str(out), "--ring", "prd", "--from", str(src), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        raw = yaml.safe_load(out.read_text())
        assert raw["spec"]["pins"]["images"]["app"] == "v2.1.0"

    def test_fails_if_file_exists_without_force(self, tmp_path):
        runner = CliRunner()
        existing = _make_version_file(tmp_path, ring="dev")
        result = runner.invoke(
            versions_group,
            ["add", "--out", str(existing), "--ring", "dev", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_force_overwrites_existing(self, tmp_path):
        runner = CliRunner()
        out = tmp_path / "v1.0.0.yaml"
        runner.invoke(versions_group, ["add", "--out", str(out), "--ring", "dev", "--work-path", str(tmp_path)])
        result = runner.invoke(
            versions_group,
            ["add", "--out", str(out), "--ring", "dev", "--force", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output

    def test_missing_source_file_fails(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            [
                "add",
                "--out", str(tmp_path / "v1.0.0.yaml"),
                "--ring", "dev",
                "--from", str(tmp_path / "nonexistent.yaml"),
                "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code == 1

    def test_file_has_correct_kind(self, tmp_path):
        runner = CliRunner()
        out = tmp_path / "v1.0.0.yaml"
        runner.invoke(versions_group, ["add", "--out", str(out), "--ring", "prd", "--work-path", str(tmp_path)])
        raw = yaml.safe_load(out.read_text())
        assert raw["kind"] == PlatformKind.VERSION_MANIFEST.value
        assert raw["spec"]["ring"] == "prd"


# ── versions lock ─────────────────────────────────────────────────────────────


class TestVersionsLockCommand:
    def test_writes_hash_to_file(self, tmp_path):
        p = _make_version_file(tmp_path, ring="prd")
        runner = CliRunner()
        result = runner.invoke(
            versions_group, ["lock", "--file", str(p), "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        raw = yaml.safe_load(p.read_text())
        assert "hash" in raw["spec"]
        assert len(raw["spec"]["hash"]) == 64

    def test_json_output_includes_hash(self, tmp_path):
        p = _make_version_file(tmp_path, ring="prd")
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["lock", "--file", str(p), "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert len(data["hash"]) == 64

    def test_hash_is_deterministic(self, tmp_path):
        p = _make_version_file(tmp_path, ring="prd")
        runner = CliRunner()
        r1 = runner.invoke(versions_group, ["lock", "--file", str(p), "--output", "json", "--work-path", str(tmp_path)])
        r2 = runner.invoke(versions_group, ["lock", "--file", str(p), "--output", "json", "--work-path", str(tmp_path)])
        h1 = json.loads(r1.output)["hash"]
        h2 = json.loads(r2.output)["hash"]
        assert h1 == h2

    def test_fails_on_missing_file(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["lock", "--file", str(tmp_path / "ghost.yaml"), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_fails_on_wrong_kind(self, tmp_path):
        wrong = tmp_path / "workspace.yaml"
        wrong.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: workspace\nmeta:\n  name: test\nspec: {}\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            versions_group,
            ["lock", "--file", str(wrong), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_console_output_shows_hash(self, tmp_path):
        p = _make_version_file(tmp_path, ring="prd")
        runner = CliRunner()
        result = runner.invoke(
            versions_group, ["lock", "--file", str(p), "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert "hash" in result.output.lower() or len([c for c in result.output if c in "abcdef0123456789"]) >= 10
