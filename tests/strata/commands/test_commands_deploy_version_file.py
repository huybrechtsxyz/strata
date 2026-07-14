"""Tests for RunDeployCommand version file handling: -v flag (Layer 3) and auto-resolve (Layer 5)."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import yaml

from strata.commands.deploy.run_deploy_command import RunDeployCommand
from strata.models.common_models import PlatformKind


_API_VERSION = "strata.huybrechts.xyz/v1"

_VERSION_YAML_TEMPLATE = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: v2-1-0
spec:
  ring: dev
  pins:
    images:
      app: v2.1.0
"""


def _make_cmd(tmp_path: Path, version_file: Optional[str] = None, force: bool = False) -> RunDeployCommand:
    cmd = RunDeployCommand(work_path=str(tmp_path), version_file=version_file, force=force)
    return cmd


def _attach_empty_deployment_service(cmd: RunDeployCommand):
    """Attach a minimal mock deployment service (no promotion, no env)."""
    mock_dep_svc = MagicMock()
    mock_dep_svc.model = None
    mock_dep_svc._environment_service = None
    cmd._deployment_service = mock_dep_svc


# ── _apply_explicit_version_file (Layer 3) ───────────────────────────────────


class TestApplyExplicitVersionFile:
    def test_returns_none_on_valid_unlocked_file(self, tmp_path):
        vf = tmp_path / "v2.1.0.yaml"
        vf.write_text(_VERSION_YAML_TEMPLATE)
        cmd = _make_cmd(tmp_path, version_file=str(vf))
        _attach_empty_deployment_service(cmd)
        err = cmd._apply_explicit_version_file()
        assert err is None

    def test_returns_error_on_missing_file(self, tmp_path):
        cmd = _make_cmd(tmp_path, version_file=str(tmp_path / "ghost.yaml"))
        _attach_empty_deployment_service(cmd)
        err = cmd._apply_explicit_version_file()
        assert err is not None
        assert "not found" in err.lower()

    def test_injects_version_file_into_model(self, tmp_path):
        vf = tmp_path / "v2.1.0.yaml"
        vf.write_text(_VERSION_YAML_TEMPLATE)
        cmd = _make_cmd(tmp_path, version_file=str(vf))
        _attach_empty_deployment_service(cmd)
        cmd._inject_version_file = MagicMock()
        cmd._apply_explicit_version_file()
        cmd._inject_version_file.assert_called_once()

    def test_returns_none_for_valid_locked_file(self, tmp_path):
        vf = tmp_path / "v2.1.0.yaml"
        raw = yaml.safe_load(_VERSION_YAML_TEMPLATE)
        pins = raw["spec"]["pins"]
        canonical = json.dumps(pins, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        raw["spec"]["hash"] = digest
        vf.write_text(yaml.dump(raw))
        cmd = _make_cmd(tmp_path, version_file=str(vf))
        _attach_empty_deployment_service(cmd)
        err = cmd._apply_explicit_version_file()
        assert err is None

    def test_returns_error_on_hash_mismatch(self, tmp_path):
        vf = tmp_path / "v2.1.0.yaml"
        raw = yaml.safe_load(_VERSION_YAML_TEMPLATE)
        raw["spec"]["hash"] = "a" * 64  # wrong hash
        vf.write_text(yaml.dump(raw))
        cmd = _make_cmd(tmp_path, version_file=str(vf))
        _attach_empty_deployment_service(cmd)
        err = cmd._apply_explicit_version_file()
        assert err is not None
        assert "mismatch" in err.lower()

    def test_returns_error_when_env_has_promotion_no_force(self, tmp_path):
        vf = tmp_path / "v2.1.0.yaml"
        vf.write_text(_VERSION_YAML_TEMPLATE)
        cmd = _make_cmd(tmp_path, version_file=str(vf), force=False)
        # Set up mock with a promotion
        mock_dep_svc = MagicMock()
        mock_dep_svc.model = None
        mock_env_svc = MagicMock()
        mock_env_svc.model.spec.promotion.strategy = "app-wave"
        mock_dep_svc._environment_service = mock_env_svc
        cmd._deployment_service = mock_dep_svc
        err = cmd._apply_explicit_version_file()
        assert err is not None
        assert "promotion" in err.lower()

    def test_force_bypasses_promotion_mutual_exclusion(self, tmp_path):
        vf = tmp_path / "v2.1.0.yaml"
        vf.write_text(_VERSION_YAML_TEMPLATE)
        cmd = _make_cmd(tmp_path, version_file=str(vf), force=True)
        mock_dep_svc = MagicMock()
        mock_dep_svc.model = None
        mock_env_svc = MagicMock()
        mock_env_svc.model.spec.promotion.strategy = "app-wave"
        mock_dep_svc._environment_service = mock_env_svc
        cmd._deployment_service = mock_dep_svc
        # With force=True, should return None (no error)
        err = cmd._apply_explicit_version_file()
        assert err is None


# ── _should_auto_resolve_version (Layer 5 guard) ─────────────────────────────


class TestShouldAutoResolveVersion:
    def test_false_when_no_deployment_service(self, tmp_path):
        cmd = _make_cmd(tmp_path)
        cmd._deployment_service = None
        assert cmd._should_auto_resolve_version() is False

    def test_false_when_no_env_service(self, tmp_path):
        cmd = _make_cmd(tmp_path)
        mock_dep = MagicMock()
        mock_dep.model = MagicMock()
        mock_dep._environment_service = None
        cmd._deployment_service = mock_dep
        assert cmd._should_auto_resolve_version() is False

    def test_false_when_no_promotion(self, tmp_path):
        cmd = _make_cmd(tmp_path)
        mock_dep = MagicMock()
        mock_dep.model = MagicMock()
        mock_env = MagicMock()
        mock_env.model.spec.promotion = None
        mock_dep._environment_service = mock_env
        cmd._deployment_service = mock_dep
        assert cmd._should_auto_resolve_version() is False

    def test_true_when_env_has_promotion(self, tmp_path):
        cmd = _make_cmd(tmp_path)
        mock_dep = MagicMock()
        mock_dep.model = MagicMock()
        mock_env = MagicMock()
        mock_env.model.spec.promotion.strategy = "app-wave"
        mock_dep._environment_service = mock_env
        cmd._deployment_service = mock_dep
        assert cmd._should_auto_resolve_version() is True


# ── _auto_resolve_version_from_promotion (Layer 5 chain) ─────────────────────


class TestAutoResolveVersionFromPromotion:
    def _setup_cmd(self, tmp_path: Path, ring: str = "dev", strategy_name: str = "app-wave") -> RunDeployCommand:
        """Create a RunDeployCommand with mocked services pointing to ring and strategy."""
        cmd = _make_cmd(tmp_path)

        # Mock deployment service → env service → promotion
        mock_dep = MagicMock()
        mock_dep.model = MagicMock()
        mock_env = MagicMock()
        mock_env.model.spec.promotion.ring = ring
        mock_env.model.spec.promotion.strategy = strategy_name
        mock_dep._environment_service = mock_env
        cmd._deployment_service = mock_dep

        # Mock config service → promotions.strategies
        mock_strategy = MagicMock()
        mock_strategy.name = strategy_name
        mock_strategy.versions_path = "versions/app/"

        mock_config_svc = MagicMock()
        mock_config_svc.model.spec.promotions.strategies = [mock_strategy]
        cmd._configuration_service = mock_config_svc

        return cmd

    def test_returns_error_when_no_lock_file(self, tmp_path):
        cmd = self._setup_cmd(tmp_path)
        (tmp_path / "versions" / "app").mkdir(parents=True)
        # No lock file created
        err = cmd._auto_resolve_version_from_promotion()
        assert err is not None
        assert "lock" in err.lower()

    def test_injects_pointer_target_when_pointer_lock_exists(self, tmp_path):
        (tmp_path / "versions" / "app").mkdir(parents=True)
        # Write version file
        vf = tmp_path / "versions" / "app" / "v2.1.0.yaml"
        vf.write_text(_VERSION_YAML_TEMPLATE)
        # Write pointer lock
        lock = {
            "apiVersion": _API_VERSION,
            "kind": "version-lock",
            "meta": {"name": "dev"},
            "spec": {"ring": "dev", "source": "v2.1.0.yaml"},
        }
        (tmp_path / "versions" / "app" / "dev.lock.yaml").write_text(yaml.dump(lock))

        cmd = self._setup_cmd(tmp_path)
        cmd._inject_version_file = MagicMock()
        err = cmd._auto_resolve_version_from_promotion()
        assert err is None
        cmd._inject_version_file.assert_called_once()
        # Should inject the pointed-to version file, not the lock
        injected = cmd._inject_version_file.call_args[0][0]
        assert "v2.1.0" in injected

    def test_injects_lock_directly_for_old_style_pins_lock(self, tmp_path):
        (tmp_path / "versions" / "app").mkdir(parents=True)
        # Write old-style lock (no source, has pins)
        lock = {
            "apiVersion": _API_VERSION,
            "kind": "version-lock",
            "meta": {"name": "dev"},
            "spec": {
                "ring": "dev",
                "pins": [{"target": {"type": "image", "name": "app"}, "version": "v1.0.0"}],
            },
        }
        (tmp_path / "versions" / "app" / "dev.lock.yaml").write_text(yaml.dump(lock))

        cmd = self._setup_cmd(tmp_path)
        cmd._inject_version_file = MagicMock()
        err = cmd._auto_resolve_version_from_promotion()
        assert err is None
        cmd._inject_version_file.assert_called_once()
        injected = cmd._inject_version_file.call_args[0][0]
        assert "dev.lock.yaml" in injected

    def test_returns_error_when_pointer_target_missing(self, tmp_path):
        (tmp_path / "versions" / "app").mkdir(parents=True)
        # Write pointer lock pointing to nonexistent file
        lock = {
            "apiVersion": _API_VERSION,
            "kind": "version-lock",
            "meta": {"name": "dev"},
            "spec": {"ring": "dev", "source": "ghost.yaml"},
        }
        (tmp_path / "versions" / "app" / "dev.lock.yaml").write_text(yaml.dump(lock))

        cmd = self._setup_cmd(tmp_path)
        err = cmd._auto_resolve_version_from_promotion()
        assert err is not None
        assert "ghost.yaml" in err

    def test_returns_error_when_no_configuration_service(self, tmp_path):
        cmd = _make_cmd(tmp_path)
        mock_dep = MagicMock()
        mock_dep.model = MagicMock()
        mock_env = MagicMock()
        mock_env.model.spec.promotion.ring = "dev"
        mock_env.model.spec.promotion.strategy = "app-wave"
        mock_dep._environment_service = mock_env
        cmd._deployment_service = mock_dep
        cmd._configuration_service = None
        err = cmd._auto_resolve_version_from_promotion()
        assert err is not None


# ── CLI: --version-file / -v option ──────────────────────────────────────────


class TestDeployRunVersionFileOption:
    def test_v_flag_passes_to_command(self, tmp_path):
        """The CLI -v option is wired and passed to RunDeployCommand."""
        from click.testing import CliRunner
        from strata.commands.cli_deploy import deploy

        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True) as mock_exec:
            result = runner.invoke(
                deploy,
                ["run", "--version-file", "versions/dev.yaml", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0, result.output

    def test_short_v_flag_works(self, tmp_path):
        from click.testing import CliRunner
        from strata.commands.cli_deploy import deploy

        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(
                deploy,
                ["run", "-v", "versions/dev.yaml", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0, result.output


# ── --ring / --wave / --promotion CLI options ─────────────────────────────────


def _make_cmd_with_overrides(
    tmp_path: Path,
    ring_override: Optional[str] = None,
    wave: Optional[int] = None,
    promotion_override: Optional[str] = None,
) -> RunDeployCommand:
    return RunDeployCommand(
        work_path=str(tmp_path),
        ring_override=ring_override,
        wave=wave,
        promotion_override=promotion_override,
    )


def _setup_promotion_cmd(
    tmp_path: Path,
    ring: str = "prd",
    strategy_name: str = "app-wave",
    ring_override: Optional[str] = None,
    wave: Optional[int] = None,
    promotion_override: Optional[str] = None,
) -> RunDeployCommand:
    """Helper: command + mocked services for promotion auto-resolve tests."""
    cmd = _make_cmd_with_overrides(
        tmp_path,
        ring_override=ring_override,
        wave=wave,
        promotion_override=promotion_override,
    )
    mock_dep = MagicMock()
    mock_dep.model = MagicMock()
    mock_env = MagicMock()
    mock_env.model.spec.promotion.ring = ring
    mock_env.model.spec.promotion.strategy = strategy_name
    mock_dep._environment_service = mock_env
    cmd._deployment_service = mock_dep

    mock_strategy = MagicMock()
    mock_strategy.name = promotion_override or strategy_name
    mock_strategy.versions_path = "versions/app/"

    mock_config_svc = MagicMock()
    mock_config_svc.model.spec.promotions.strategies = [mock_strategy]
    cmd._configuration_service = mock_config_svc
    return cmd


class TestRingOverride:
    def test_ring_override_uses_different_lock_file(self, tmp_path):
        """--ring prod should load prod.lock.yaml even when env.spec.promotion.ring is 'dev'."""
        (tmp_path / "versions" / "app").mkdir(parents=True)
        vf = tmp_path / "versions" / "app" / "v2.1.0.yaml"
        vf.write_text(_VERSION_YAML_TEMPLATE)
        lock = {
            "apiVersion": _API_VERSION, "kind": "version-lock",
            "meta": {"name": "prd"},
            "spec": {"ring": "prd", "source": "v2.1.0.yaml"},
        }
        (tmp_path / "versions" / "app" / "prd.lock.yaml").write_text(yaml.dump(lock))

        # env.spec.promotion.ring = "dev" but we override with "prd"
        cmd = _setup_promotion_cmd(tmp_path, ring="dev", ring_override="prd")
        cmd._inject_version_file = MagicMock()
        err = cmd._auto_resolve_version_from_promotion()
        assert err is None
        injected = cmd._inject_version_file.call_args[0][0]
        assert "v2.1.0" in injected

    def test_ring_override_fails_if_lock_missing(self, tmp_path):
        (tmp_path / "versions" / "app").mkdir(parents=True)
        cmd = _setup_promotion_cmd(tmp_path, ring="dev", ring_override="staging")
        err = cmd._auto_resolve_version_from_promotion()
        assert err is not None
        assert "lock" in err.lower()


class TestPromotionOverride:
    def test_promotion_override_uses_different_strategy(self, tmp_path):
        """--promotion other-strat should resolve using that strategy's versions_path."""
        (tmp_path / "versions" / "other").mkdir(parents=True)
        vf = tmp_path / "versions" / "other" / "v3.0.0.yaml"
        vf.write_text(_VERSION_YAML_TEMPLATE)
        lock = {
            "apiVersion": _API_VERSION, "kind": "version-lock",
            "meta": {"name": "prd"},
            "spec": {"ring": "prd", "source": "v3.0.0.yaml"},
        }
        (tmp_path / "versions" / "other" / "prd.lock.yaml").write_text(yaml.dump(lock))

        cmd = RunDeployCommand(work_path=str(tmp_path), promotion_override="other-strat")
        mock_dep = MagicMock()
        mock_env = MagicMock()
        mock_env.model.spec.promotion.ring = "prd"
        mock_env.model.spec.promotion.strategy = "app-wave"
        mock_dep._environment_service = mock_env
        cmd._deployment_service = mock_dep

        # Config has two strategies; override should pick "other-strat"
        strat_a = MagicMock(); strat_a.name = "app-wave"; strat_a.versions_path = "versions/app/"
        strat_b = MagicMock(); strat_b.name = "other-strat"; strat_b.versions_path = "versions/other/"
        mock_config = MagicMock()
        mock_config.model.spec.promotions.strategies = [strat_a, strat_b]
        cmd._configuration_service = mock_config

        cmd._inject_version_file = MagicMock()
        err = cmd._auto_resolve_version_from_promotion()
        assert err is None
        injected = cmd._inject_version_file.call_args[0][0]
        assert "v3.0.0" in injected


class TestWaveLayering:
    def _write_version_and_locks(self, tmp_path: Path, ring: str = "prd") -> dict:
        """Create ring lock + wave lock + two version files. Returns paths dict."""
        d = tmp_path / "versions" / "app"
        d.mkdir(parents=True)

        vf_ring = d / "v2.0.0.yaml"
        vf_ring.write_text("""\
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: v2-0-0
spec:
  ring: prd
  pins:
    images:
      app: v2.0.0
      worker: v2.0.0
""")
        vf_wave = d / "v2.1.0.yaml"
        vf_wave.write_text("""\
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: v2-1-0
spec:
  ring: prd
  pins:
    images:
      app: v2.1.0
""")
        ring_lock = {
            "apiVersion": _API_VERSION, "kind": "version-lock",
            "meta": {"name": ring},
            "spec": {"ring": ring, "source": "v2.0.0.yaml"},
        }
        (d / f"{ring}.lock.yaml").write_text(yaml.dump(ring_lock))

        wave_lock = {
            "apiVersion": _API_VERSION, "kind": "version-lock",
            "meta": {"name": f"{ring}.wave.1"},
            "spec": {"ring": ring, "source": "v2.1.0.yaml", "wave": 1},
        }
        (d / f"{ring}.wave.1.lock.yaml").write_text(yaml.dump(wave_lock))
        return {"vf_ring": vf_ring, "vf_wave": vf_wave, "dir": d}

    def test_wave_flag_injects_ring_then_wave(self, tmp_path):
        """With --wave 1, ring version file injected first, then wave (wave wins)."""
        paths = self._write_version_and_locks(tmp_path)

        cmd = _setup_promotion_cmd(tmp_path, ring="prd", wave=1)
        injected: list = []
        cmd._inject_version_file = MagicMock(side_effect=lambda p: injected.append(p))
        err = cmd._auto_resolve_version_from_promotion()

        assert err is None, err
        assert len(injected) == 2
        # Ring file is first (lower priority), wave file is second (wins)
        assert "v2.0.0" in injected[0]
        assert "v2.1.0" in injected[1]

    def test_wave_flag_returns_error_when_wave_lock_missing(self, tmp_path):
        """--wave 2 should fail if the wave 2 lock doesn't exist."""
        self._write_version_and_locks(tmp_path)  # only wave 1 exists
        cmd = _setup_promotion_cmd(tmp_path, ring="prd", wave=2)
        err = cmd._auto_resolve_version_from_promotion()
        assert err is not None
        assert "wave" in err.lower()
        assert "wave.2" in err

    def test_no_wave_flag_injects_only_ring_file(self, tmp_path):
        """Without --wave, only the ring version file is injected (no wave lock)."""
        self._write_version_and_locks(tmp_path)
        cmd = _setup_promotion_cmd(tmp_path, ring="prd", wave=None)
        injected: list = []
        cmd._inject_version_file = MagicMock(side_effect=lambda p: injected.append(p))
        err = cmd._auto_resolve_version_from_promotion()
        assert err is None
        assert len(injected) == 1
        assert "v2.0.0" in injected[0]

    def test_wave_lock_with_inline_pins_layers_correctly(self, tmp_path):
        """Old-style wave lock (inline pins) also layers correctly."""
        d = tmp_path / "versions" / "app"
        d.mkdir(parents=True)
        vf = d / "v2.0.0.yaml"
        vf.write_text(_VERSION_YAML_TEMPLATE)
        (d / "prd.lock.yaml").write_text(yaml.dump({
            "apiVersion": _API_VERSION, "kind": "version-lock",
            "meta": {"name": "prd"},
            "spec": {"ring": "prd", "source": "v2.0.0.yaml"},
        }))
        # Old-style wave lock with pins (no source)
        (d / "prd.wave.1.lock.yaml").write_text(yaml.dump({
            "apiVersion": _API_VERSION, "kind": "version-lock",
            "meta": {"name": "prd.wave.1"},
            "spec": {
                "ring": "prd",
                "wave": 1,
                "pins": [{"target": {"type": "image", "name": "app"}, "version": "v2.1.0"}],
            },
        }))

        cmd = _setup_promotion_cmd(tmp_path, ring="prd", wave=1)
        injected: list = []
        cmd._inject_version_file = MagicMock(side_effect=lambda p: injected.append(p))
        err = cmd._auto_resolve_version_from_promotion()
        assert err is None
        assert len(injected) == 2
        assert "v2.0.0" in injected[0]
        assert "wave.1" in injected[1]


class TestDeployRunFilterFlagsCLI:
    """Smoke-test that --ring / --wave / --promotion reach RunDeployCommand."""

    def test_ring_flag_passed_to_command(self, tmp_path):
        from click.testing import CliRunner
        from strata.commands.cli_deploy import deploy

        captured = {}

        def fake_init(self_cmd, **kwargs):
            captured.update(kwargs)

        runner = CliRunner()
        with patch.object(RunDeployCommand, "__init__", side_effect=fake_init, autospec=True), \
             patch.object(RunDeployCommand, "execute", return_value=True):
            runner.invoke(deploy, ["run", "--ring", "prod", "--work-path", str(tmp_path)])
        assert captured.get("ring_override") == "prod"

    def test_wave_flag_passed_to_command(self, tmp_path):
        from click.testing import CliRunner
        from strata.commands.cli_deploy import deploy

        captured = {}

        def fake_init(self_cmd, **kwargs):
            captured.update(kwargs)

        runner = CliRunner()
        with patch.object(RunDeployCommand, "__init__", side_effect=fake_init, autospec=True), \
             patch.object(RunDeployCommand, "execute", return_value=True):
            runner.invoke(deploy, ["run", "--wave", "2", "--work-path", str(tmp_path)])
        assert captured.get("wave") == 2

    def test_promotion_flag_passed_to_command(self, tmp_path):
        from click.testing import CliRunner
        from strata.commands.cli_deploy import deploy

        captured = {}

        def fake_init(self_cmd, **kwargs):
            captured.update(kwargs)

        runner = CliRunner()
        with patch.object(RunDeployCommand, "__init__", side_effect=fake_init, autospec=True), \
             patch.object(RunDeployCommand, "execute", return_value=True):
            runner.invoke(deploy, ["run", "--promotion", "customer-apps", "--work-path", str(tmp_path)])
        assert captured.get("promotion_override") == "customer-apps"
