"""Tests for the ``promote`` command group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from strata.commands.cli_promote import promote_group
from strata.models.common_models import PlatformKind

_API_VERSION = "strata.huybrechts.xyz/v1"


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_activity_log(
    tmp_path: Path,
    target: str = "iac_core",
    version: str = "v2.5.0",
    ring: str = "prd",
    status: str = "completed",
) -> Path:
    """Write a minimal activity log YAML under .strata/promotions/."""
    promotions_dir = tmp_path / ".strata" / "promotions"
    promotions_dir.mkdir(parents=True, exist_ok=True)
    log = {
        "target": target,
        "version": version,
        "previous_version": "v2.4.0",
        "ring": ring,
        "environments": ["prod-be"],
        "strategy": "infra-cautious",
        "progression": "standard",
        "rings": ["dev", "test", "prd"],
        "branch": f"promote/{target}-{version}-{ring}",
        "events": [
            {
                "timestamp": "2026-07-11T12:00:00Z",
                "action": "committed",
                "ring_wave": 1,
                "environments": ["prod-be"],
                "commit": "abc1234",
            },
            {
                "timestamp": "2026-07-11T12:05:00Z",
                "action": status,
            },
        ],
    }
    fname = f"{target}-{version}-{ring}.yaml"
    p = promotions_dir / fname
    p.write_text(yaml.dump(log, default_flow_style=False))
    return p


def _make_promotion_record(
    tmp_path: Path,
    name: str = "prom-20260711-prd",
    target: str = "iac_core",
    ring: str = "prd",
    outcome: str = "completed",
) -> Path:
    """Write a minimal promotion-record YAML under .strata/promotions/records/."""
    records_dir = tmp_path / ".strata" / "promotions" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "apiVersion": _API_VERSION,
        "kind": PlatformKind.PROMOTION_RECORD.value,
        "meta": {"name": name, "labels": {"target": target, "ring": ring, "outcome": outcome}},
        "spec": {
            "target": {"type": "remote", "name": target, "from_version": "v2.4.0", "to_version": "v2.5.0"},
            "strategy": "infra-cautious",
            "progression": "standard",
            "rings": ["dev", "test", "prd"],
            "outcome": outcome,
            "initiated_by": "test-user",
            "hostname": "test-host",
            "started_at": "2026-07-11T12:00:00Z",
            "completed_at": "2026-07-11T12:05:00Z",
            "branch": f"promote/{target}-v2.5.0-{ring}",
        },
    }
    p = records_dir / f"{name}.yaml"
    p.write_text(yaml.dump(record, default_flow_style=False))
    return p


def _make_ring_lock(
    tmp_path: Path,
    ring: str = "prd",
    target_type: str = "remote",
    target_name: str = "iac_core",
    version: str = "v2.4.0",
) -> Path:
    """Write a minimal version-lock YAML under versions/."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    lock = {
        "apiVersion": _API_VERSION,
        "kind": PlatformKind.VERSION_LOCK.value,
        "meta": {"name": ring},
        "spec": {
            "ring": ring,
            "pins": [{"target": {"type": target_type, "name": target_name}, "version": version}],
        },
    }
    p = versions_dir / f"{ring}.yaml"
    p.write_text(yaml.dump(lock, default_flow_style=False))
    return p


def _make_config(tmp_path: Path) -> Path:
    """Write a minimal configuration YAML with promotions declared."""
    config = {
        "apiVersion": _API_VERSION,
        "kind": "configuration",
        "meta": {"name": "test-config"},
        "spec": {
            "promotions": {
                "progressions": [
                    {
                        "name": "standard",
                        "rings": [
                            {"name": "dev", "environments": ["dev1"]},
                            {"name": "prd", "environments": ["prod-be"], "require": "any_one"},
                        ],
                    }
                ],
                "strategies": [
                    {
                        "name": "infra-cautious",
                        "type": "remote",
                        "progression": "standard",
                        "waves": [{"name": "all"}],
                        "scope": None,
                        "gates": {"require_progression_order": True},
                    }
                ],
            }
        },
    }
    config_dir = tmp_path / ".strata"
    config_dir.mkdir(parents=True, exist_ok=True)
    p = config_dir / "configuration.yaml"
    p.write_text(yaml.dump(config, default_flow_style=False))
    return p


# ── status ────────────────────────────────────────────────────────────────────


class TestPromoteStatus:
    def test_empty_no_promotions(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(promote_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No in-flight" in result.output

    def test_shows_in_flight_promotion(self, tmp_path):
        _make_activity_log(tmp_path, status="committed")
        runner = CliRunner()
        result = runner.invoke(promote_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "iac_core" in result.output
        assert "prd" in result.output

    def test_shows_completed_promotion(self, tmp_path):
        _make_activity_log(tmp_path, status="completed")
        runner = CliRunner()
        result = runner.invoke(promote_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "completed" in result.output

    def test_json_output(self, tmp_path):
        _make_activity_log(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group, ["status", "--output", "json", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert len(data["promotions"]) == 1
        prom = data["promotions"][0]
        assert prom["target"] == "iac_core"
        assert prom["ring"] == "prd"

    def test_text_output(self, tmp_path):
        _make_activity_log(tmp_path, status="in-progress")
        runner = CliRunner()
        result = runner.invoke(
            promote_group, ["status", "--output", "text", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert "iac_core" in result.output
        assert "\t" in result.output  # tab-separated

    def test_multiple_promotions(self, tmp_path):
        _make_activity_log(tmp_path, target="iac_core", version="v2.5.0", ring="prd")
        _make_activity_log(tmp_path, target="traefik", version="28.2.0", ring="dev")
        runner = CliRunner()
        result = runner.invoke(
            promote_group, ["status", "--output", "json", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["promotions"]) == 2


# ── matrix ────────────────────────────────────────────────────────────────────


class TestPromoteMatrix:
    def test_empty_no_versions_dir(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(promote_group, ["matrix", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No version matrix" in result.output

    def test_shows_ring_data(self, tmp_path):
        _make_ring_lock(tmp_path, ring="prd", version="v2.5.0")
        _make_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(promote_group, ["matrix", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output

    def test_json_output_rings_key(self, tmp_path):
        _make_ring_lock(tmp_path, ring="prd", version="v2.5.0")
        _make_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group, ["matrix", "--output", "json", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert "matrix" in data
        assert "rings" in data["matrix"]

    def test_json_output_no_config(self, tmp_path):
        """Without config, matrix returns empty rings list (no progressions to index from)."""
        _make_ring_lock(tmp_path, ring="prd", version="v2.5.0")
        runner = CliRunner()
        result = runner.invoke(
            promote_group, ["matrix", "--output", "json", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["matrix"]["rings"] == []

    def test_text_output(self, tmp_path):
        _make_ring_lock(tmp_path, ring="prd", version="v2.5.0")
        _make_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group, ["matrix", "--output", "text", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output

    def test_filter_by_remote(self, tmp_path):
        _make_ring_lock(tmp_path, ring="prd", target_name="iac_core", version="v2.5.0")
        _make_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            ["matrix", "--remote", "iac_core", "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # All version entries should relate to iac_core only
        for ring_data in data["matrix"]["rings"]:
            for key in ring_data.get("versions", {}):
                assert "iac_core" in key


# ── history ───────────────────────────────────────────────────────────────────


class TestPromoteHistory:
    def test_empty_no_records_dir(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(promote_group, ["history", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No promotion records" in result.output

    def test_shows_record(self, tmp_path):
        _make_promotion_record(tmp_path)
        runner = CliRunner()
        result = runner.invoke(promote_group, ["history", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "iac_core" in result.output

    def test_json_output(self, tmp_path):
        _make_promotion_record(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group, ["history", "--output", "json", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert len(data["records"]) == 1
        rec = data["records"][0]
        assert rec["ring"] == "prd"
        assert rec["outcome"] == "completed"

    def test_text_output(self, tmp_path):
        _make_promotion_record(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group, ["history", "--output", "text", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert "iac_core" in result.output

    def test_filter_by_ring(self, tmp_path):
        _make_promotion_record(tmp_path, name="prom-prd", ring="prd")
        _make_promotion_record(tmp_path, name="prom-dev", ring="dev")
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            ["history", "--ring", "prd", "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert all(r["ring"] == "prd" for r in data["records"])

    def test_filter_by_remote(self, tmp_path):
        _make_promotion_record(tmp_path, name="prom-iac", target="iac_core")
        _make_promotion_record(tmp_path, name="prom-traefik", target="traefik")
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            ["history", "--remote", "iac_core", "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        for rec in data["records"]:
            assert "iac_core" in rec.get("target", "")

    def test_last_parameter(self, tmp_path):
        for i in range(5):
            _make_promotion_record(tmp_path, name=f"prom-{i:04d}", target="iac_core")
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            ["history", "--last", "3", "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["records"]) <= 3


# ── log ───────────────────────────────────────────────────────────────────────


class TestPromoteLog:
    def test_shows_log_for_target_ring(self, tmp_path):
        _make_activity_log(tmp_path, target="iac_core", version="v2.5.0", ring="prd")
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            ["log", "--remote", "iac_core", "--to", "prd", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "iac_core" in result.output
        assert "prd" in result.output

    def test_shows_specific_version(self, tmp_path):
        _make_activity_log(tmp_path, target="iac_core", version="v2.5.0", ring="prd")
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "log", "--remote", "iac_core", "--to", "prd",
                "--version", "v2.5.0", "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "v2.5.0" in result.output

    def test_json_output(self, tmp_path):
        _make_activity_log(tmp_path, target="iac_core", version="v2.5.0", ring="prd")
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "log", "--remote", "iac_core", "--to", "prd",
                "--output", "json", "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["target"] == "iac_core"
        assert data["ring"] == "prd"
        assert len(data["events"]) >= 1

    def test_text_output(self, tmp_path):
        _make_activity_log(tmp_path, target="iac_core", version="v2.5.0", ring="prd")
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "log", "--remote", "iac_core", "--to", "prd",
                "--output", "text", "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "committed" in result.output  # event action

    def test_fails_if_not_found(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            ["log", "--remote", "iac_core", "--to", "prd", "--work-path", str(tmp_path)],
        )
        assert result.exit_code != 0

    def test_requires_remote_or_module(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            promote_group, ["log", "--to", "prd", "--work-path", str(tmp_path)]
        )
        assert result.exit_code != 0


# ── start (dry-run only — no git required) ────────────────────────────────────


class TestPromoteStartDryRun:
    """Dry-run tests exercise gate logic and plan output without touching git."""

    def test_dry_run_requires_remote_or_module(self, tmp_path):
        _make_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            ["start", "--version", "v2.5.0", "--to", "prd", "--dry-run", "--work-path", str(tmp_path)],
        )
        assert result.exit_code != 0

    def test_dry_run_remote_mutually_exclusive_with_module(self, tmp_path):
        _make_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "start", "--remote", "iac_core", "--module", "traefik",
                "--version", "v2.5.0", "--to", "prd", "--dry-run",
                "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code != 0

    def test_dry_run_no_config_reports_error(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "start", "--remote", "iac_core", "--version", "v2.5.0",
                "--to", "prd", "--dry-run", "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code != 0

    def test_dry_run_gate_fails_missing_dev_lock(self, tmp_path):
        """prd gate requires dev lock first — fails if dev lock absent."""
        _make_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "start", "--remote", "iac_core", "--version", "v2.5.0",
                "--to", "prd", "--dry-run", "--work-path", str(tmp_path),
            ],
        )
        # Gate fails: no dev lock → exit code 1 or 3
        assert result.exit_code != 0

    def test_dry_run_first_ring_no_gate_succeeds(self, tmp_path):
        """Promoting to the first ring (dev) has no inbound gate → dry-run succeeds."""
        _make_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "start", "--remote", "iac_core", "--version", "v2.5.0",
                "--to", "dev", "--dry-run", "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Dry-run" in result.output or "dry_run" in result.output

    def test_dry_run_json_output_first_ring(self, tmp_path):
        _make_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "start", "--remote", "iac_core", "--version", "v2.5.0",
                "--to", "dev", "--dry-run", "--output", "json",
                "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["dry_run"] is True
        assert data["ring"] == "dev"
        assert "files_to_write" in data

    def test_dry_run_gate_passes_when_dev_lock_present(self, tmp_path):
        """Gate passes when the prior ring (dev) already has the version pinned."""
        _make_config(tmp_path)
        _make_ring_lock(tmp_path, ring="dev", target_name="iac_core", version="v2.5.0")
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "start", "--remote", "iac_core", "--version", "v2.5.0",
                "--to", "prd", "--dry-run", "--output", "json",
                "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["dry_run"] is True
        assert data["ring"] == "prd"

    def test_dry_run_text_output(self, tmp_path):
        _make_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "start", "--remote", "iac_core", "--version", "v2.5.0",
                "--to", "dev", "--dry-run", "--output", "text",
                "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "promote/" in result.output  # branch name


# ── rollback (dry-run only) ───────────────────────────────────────────────────


class TestPromoteRollbackDryRun:
    def test_dry_run_requires_remote_or_module(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            ["rollback", "--to", "prd", "--dry-run", "--work-path", str(tmp_path)],
        )
        assert result.exit_code != 0

    def test_dry_run_no_config_reports_error(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "rollback", "--remote", "iac_core", "--to", "prd",
                "--dry-run", "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code != 0

    def test_dry_run_resolves_from_version_via_flag(self, tmp_path):
        """--from-version escape hatch should resolve previous_version without git or activity log."""
        _make_config(tmp_path)
        _make_ring_lock(tmp_path, ring="prd", target_name="iac_core", version="v2.5.0")
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "rollback", "--remote", "iac_core", "--to", "prd",
                "--from-version", "v2.4.0", "--dry-run", "--output", "json",
                "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["dry_run"] is True
        assert data["rollback_to_version"] == "v2.4.0"

    def test_dry_run_resolves_from_activity_log(self, tmp_path):
        """previous_version tier-1: activity log present."""
        _make_config(tmp_path)
        _make_ring_lock(tmp_path, ring="prd", target_name="iac_core", version="v2.5.0")
        _make_activity_log(
            tmp_path, target="iac_core", version="v2.5.0", ring="prd", status="completed"
        )
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "rollback", "--remote", "iac_core", "--to", "prd",
                "--dry-run", "--output", "json", "--work-path", str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["dry_run"] is True
        assert data["rollback_to_version"] == "v2.4.0"

    def test_dry_run_fails_without_from_version_or_activity_log(self, tmp_path):
        """If all 3 tiers fail, exits with error."""
        _make_config(tmp_path)
        _make_ring_lock(tmp_path, ring="prd", target_name="iac_core", version="v2.5.0")
        runner = CliRunner()
        result = runner.invoke(
            promote_group,
            [
                "rollback", "--remote", "iac_core", "--to", "prd",
                "--dry-run", "--work-path", str(tmp_path),
            ],
        )
        # Tier 1 (no log), Tier 2 (no git), Tier 3 (no flag) → error
        assert result.exit_code != 0
