"""Tests for PromoteController.run_promote (ADR-0011 new layered interface)."""

from __future__ import annotations

from pathlib import Path

import yaml

from strata.controllers.promote_controller import PromoteController

_CONFIG_YAML = """\
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test-config
spec:
  promotions:
    progressions:
      - name: standard
        rings:
          - name: dev
            environments: [dev1]
          - name: prd
            environments: [prd1]
            require: any_one
    strategies:
      - name: app-wave
        type: image
        progression: standard
        versions_path: versions/app/
"""

_VERSION_YAML = """\
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


def _make_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal workspace. Returns (work_path, version_file)."""
    wp = tmp_path
    (wp / ".strata").mkdir()
    (wp / ".strata" / "configuration.yaml").write_text(_CONFIG_YAML)
    (wp / "versions" / "app").mkdir(parents=True)
    vf = wp / "versions" / "app" / "v2.1.0.yaml"
    vf.write_text(_VERSION_YAML)
    return wp, vf


def _no_git(ctrl: PromoteController) -> PromoteController:
    """Patch git operations so tests don't need a real repo."""
    ctrl._git_create_or_checkout_branch = lambda branch, wp: (True, branch)  # type: ignore[assignment]
    ctrl._git_add_and_commit = lambda files, msg, wp: (True, "abc123")  # type: ignore[assignment]
    return ctrl


class TestRunPromoteDryRun:
    def test_dry_run_returns_plan(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)
        ctrl = PromoteController()
        result = ctrl.run_promote("dev", vf, "app-wave", dry_run=True, work_path=wp)
        assert result["dry_run"] is True
        assert result["ring"] == "dev"
        assert result["promotion"] == "app-wave"
        assert any("dev.lock.yaml" in f for f in result["files_to_write"])

    def test_dry_run_with_wave_shows_wave_lock(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)
        ctrl = PromoteController()
        result = ctrl.run_promote("dev", vf, "app-wave", wave=1, dry_run=True, work_path=wp)
        assert any("wave.1" in f for f in result["files_to_write"])

    def test_dry_run_complete_shows_ring_lock_and_deletes(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)
        # Create a wave lock to be "deleted"
        wave_lock = wp / "versions" / "app" / "dev.wave.1.lock.yaml"
        wave_lock.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: version-lock\nmeta:\n  name: dev\nspec:\n  ring: dev\n  source: v2.1.0.yaml\n"
        )
        ctrl = PromoteController()
        result = ctrl.run_promote("dev", vf, "app-wave", complete=True, dry_run=True, work_path=wp)
        assert any("dev.lock.yaml" in f for f in result["files_to_write"])
        assert any("wave.1" in f for f in result["files_to_delete"])


class TestRunPromoteExecution:
    def test_writes_pointer_lock(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)
        ctrl = _no_git(PromoteController())
        result = ctrl.run_promote("dev", vf, "app-wave", work_path=wp)
        assert not ctrl.has_errors(), ctrl.get_errors()
        lock_path = wp / "versions" / "app" / "dev.lock.yaml"
        assert lock_path.exists()
        raw = yaml.safe_load(lock_path.read_text())
        assert raw["spec"]["source"] == "v2.1.0.yaml"
        assert raw["spec"]["ring"] == "dev"

    def test_commit_sha_in_result(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)
        ctrl = _no_git(PromoteController())
        result = ctrl.run_promote("dev", vf, "app-wave", work_path=wp)
        assert result["commit_sha"] == "abc123"

    def test_wave_writes_wave_lock(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)
        ctrl = _no_git(PromoteController())
        ctrl.run_promote("dev", vf, "app-wave", wave=2, work_path=wp)
        wave_lock = wp / "versions" / "app" / "dev.wave.2.lock.yaml"
        assert wave_lock.exists()
        raw = yaml.safe_load(wave_lock.read_text())
        assert raw["spec"]["source"] == "v2.1.0.yaml"

    def test_complete_writes_ring_lock_and_deletes_waves(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)
        # Create wave locks first
        app_dir = wp / "versions" / "app"
        (app_dir / "dev.wave.1.lock.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: version-lock\nmeta:\n  name: dev\nspec:\n  ring: dev\n  source: v2.1.0.yaml\n"
        )
        (app_dir / "dev.wave.2.lock.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: version-lock\nmeta:\n  name: dev\nspec:\n  ring: dev\n  source: v2.1.0.yaml\n"
        )
        ctrl = _no_git(PromoteController())
        result = ctrl.run_promote("dev", vf, "app-wave", complete=True, work_path=wp)
        assert not ctrl.has_errors(), ctrl.get_errors()
        ring_lock = app_dir / "dev.lock.yaml"
        assert ring_lock.exists()
        assert not (app_dir / "dev.wave.1.lock.yaml").exists()
        assert not (app_dir / "dev.wave.2.lock.yaml").exists()
        assert len(result["files_deleted"]) == 2

    def test_file_outside_versions_path_errors(self, tmp_path):
        wp, _ = _make_workspace(tmp_path)
        outside_vf = tmp_path / "outside" / "v1.0.yaml"
        outside_vf.parent.mkdir(parents=True)
        outside_vf.write_text(_VERSION_YAML)
        ctrl = PromoteController()
        result = ctrl.run_promote("dev", outside_vf, "app-wave", work_path=wp)
        assert result == {}
        assert ctrl.has_errors()
        assert "versions_path" in ctrl.get_errors()[0]

    def test_unknown_promotion_name_errors(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)
        ctrl = PromoteController()
        result = ctrl.run_promote("dev", vf, "nonexistent-promotion", work_path=wp)
        assert result == {}
        assert ctrl.has_errors()

    def test_unknown_ring_errors(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)
        ctrl = PromoteController()
        result = ctrl.run_promote("staging", vf, "app-wave", work_path=wp)
        assert result == {}
        assert ctrl.has_errors()
        assert "staging" in ctrl.get_errors()[0]

    def test_missing_versions_path_on_strategy_errors(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)
        # Override config with no versions_path
        config_no_vp = _CONFIG_YAML.replace("versions_path: versions/app/", "")
        (wp / ".strata" / "configuration.yaml").write_text(config_no_vp)
        ctrl = PromoteController()
        result = ctrl.run_promote("dev", vf, "app-wave", work_path=wp)
        assert result == {}
        assert ctrl.has_errors()
        assert "versions_path" in ctrl.get_errors()[0]


class TestRunPromoteProgressionOrderGate:
    def test_gate_blocks_prd_before_dev(self, tmp_path):
        """Cannot promote to prd if dev lock doesn't exist (gate active via strategy.gates)."""
        config_with_gate = """\
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test-config
spec:
  promotions:
    progressions:
      - name: standard
        rings:
          - name: dev
            environments: [dev1]
          - name: prd
            environments: [prd1]
            require: any_one
    strategies:
      - name: app-wave
        type: image
        progression: standard
        versions_path: versions/app/
        gates:
          require_progression_order: true
"""
        wp = tmp_path
        (wp / ".strata").mkdir()
        (wp / ".strata" / "configuration.yaml").write_text(config_with_gate)
        (wp / "versions" / "app").mkdir(parents=True)
        vf = wp / "versions" / "app" / "v2.1.0.yaml"
        vf.write_text(_VERSION_YAML)
        # No dev lock exists yet
        ctrl = PromoteController()
        result = ctrl.run_promote("prd", vf, "app-wave", work_path=wp)
        assert result == {}
        assert ctrl.has_errors()
        assert "dev" in ctrl.get_errors()[0]

    def test_force_bypasses_gate(self, tmp_path):
        config_with_gate = """\
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test-config
spec:
  promotions:
    progressions:
      - name: standard
        rings:
          - name: dev
            environments: [dev1]
          - name: prd
            environments: [prd1]
    strategies:
      - name: app-wave
        type: image
        progression: standard
        versions_path: versions/app/
        gates:
          require_progression_order: true
"""
        wp = tmp_path
        (wp / ".strata").mkdir()
        (wp / ".strata" / "configuration.yaml").write_text(config_with_gate)
        (wp / "versions" / "app").mkdir(parents=True)
        vf = wp / "versions" / "app" / "v2.1.0.yaml"
        vf.write_text(_VERSION_YAML)
        ctrl = _no_git(PromoteController())
        result = ctrl.run_promote("prd", vf, "app-wave", force=True, work_path=wp)
        assert not ctrl.has_errors(), ctrl.get_errors()
        assert (wp / "versions" / "app" / "prd.lock.yaml").exists()


# ── Version file with hash (locked) ──────────────────────────────────────────

_VERSION_YAML_LOCKED = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: v2-1-0
spec:
  ring: dev
  pins:
    images:
      app: v2.1.0
  hash: "sha256:aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabb"
"""

_VERSION_YAML_V200 = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: v2-0-0
spec:
  ring: dev
  pins:
    images:
      app: v2.0.0
  hash: "sha256:0000111122223333000011112222333300001111222233330000111122223333"
"""


def _make_workspace_locked(tmp_path: Path) -> tuple[Path, Path]:
    """Workspace where the version file has spec.hash set."""
    wp = tmp_path
    (wp / ".strata").mkdir()
    (wp / ".strata" / "configuration.yaml").write_text(_CONFIG_YAML)
    (wp / "versions" / "app").mkdir(parents=True)
    vf = wp / "versions" / "app" / "v2.1.0.yaml"
    vf.write_text(_VERSION_YAML_LOCKED)
    return wp, vf


class TestWritePointerLockNewFields:
    """_write_pointer_lock populates spec.hash, spec.version, spec.wave, spec.previous."""

    def test_hash_and_version_written_from_locked_file(self, tmp_path):
        wp, vf = _make_workspace_locked(tmp_path)
        ctrl = _no_git(PromoteController())
        ctrl.run_promote("dev", vf, "app-wave", work_path=wp)
        lock_path = wp / "versions" / "app" / "dev.lock.yaml"
        data = yaml.safe_load(lock_path.read_text())
        spec = data["spec"]
        assert spec["hash"] == "sha256:aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabb"
        assert spec["version"] == "v2-1-0"

    def test_no_hash_when_version_file_unlocked(self, tmp_path):
        wp, vf = _make_workspace(tmp_path)  # unlocked file (no spec.hash)
        ctrl = _no_git(PromoteController())
        ctrl.run_promote("dev", vf, "app-wave", work_path=wp)
        lock_path = wp / "versions" / "app" / "dev.lock.yaml"
        data = yaml.safe_load(lock_path.read_text())
        assert "hash" not in data["spec"]

    def test_wave_written_on_wave_lock(self, tmp_path):
        wp, vf = _make_workspace_locked(tmp_path)
        ctrl = _no_git(PromoteController())
        ctrl.run_promote("dev", vf, "app-wave", wave=2, work_path=wp)
        wave_lock = wp / "versions" / "app" / "dev.wave.2.lock.yaml"
        data = yaml.safe_load(wave_lock.read_text())
        assert data["spec"]["wave"] == 2

    def test_no_wave_on_ring_lock(self, tmp_path):
        wp, vf = _make_workspace_locked(tmp_path)
        ctrl = _no_git(PromoteController())
        ctrl.run_promote("dev", vf, "app-wave", work_path=wp)
        lock_path = wp / "versions" / "app" / "dev.lock.yaml"
        data = yaml.safe_load(lock_path.read_text())
        assert "wave" not in data["spec"]

    def test_previous_written_on_second_promote(self, tmp_path):
        """Second promotion should write spec.previous pointing to the first version."""
        wp, _ = _make_workspace_locked(tmp_path)
        vf_v200 = wp / "versions" / "app" / "v2.0.0.yaml"
        vf_v200.write_text(_VERSION_YAML_V200)
        vf_v210 = wp / "versions" / "app" / "v2.1.0.yaml"  # already written by _make_workspace_locked

        ctrl = _no_git(PromoteController())
        # First promote: v2.0.0
        ctrl.run_promote("dev", vf_v200, "app-wave", work_path=wp)
        assert not ctrl.has_errors(), ctrl.get_errors()

        # Second promote: v2.1.0
        ctrl2 = _no_git(PromoteController())
        ctrl2.run_promote("dev", vf_v210, "app-wave", work_path=wp)
        assert not ctrl2.has_errors(), ctrl2.get_errors()

        lock_path = wp / "versions" / "app" / "dev.lock.yaml"
        data = yaml.safe_load(lock_path.read_text())
        spec = data["spec"]
        assert "v2.1.0.yaml" in spec["source"]
        assert spec["previous"]["source"] == "v2.0.0.yaml"
        assert spec["previous"]["version"] == "v2-0-0"
        assert spec["previous"]["hash"] == "sha256:0000111122223333000011112222333300001111222233330000111122223333"

    def test_no_previous_on_wave_lock(self, tmp_path):
        """Wave locks never carry spec.previous."""
        wp, vf = _make_workspace_locked(tmp_path)
        # Create a ring lock first so there's something to carry over
        ctrl = _no_git(PromoteController())
        ctrl.run_promote("dev", vf, "app-wave", work_path=wp)
        ctrl2 = _no_git(PromoteController())
        ctrl2.run_promote("dev", vf, "app-wave", wave=1, work_path=wp)
        wave_lock = wp / "versions" / "app" / "dev.wave.1.lock.yaml"
        data = yaml.safe_load(wave_lock.read_text())
        assert "previous" not in data["spec"]


class TestRunPointerRollback:
    """run_pointer_rollback() restores the ring lock to spec.previous."""

    def _make_workspace_with_previous(self, tmp_path: Path):
        """Workspace where dev.lock.yaml already has spec.previous set."""
        wp = tmp_path
        (wp / ".strata").mkdir()
        (wp / ".strata" / "configuration.yaml").write_text(_CONFIG_YAML)
        (wp / "versions" / "app").mkdir(parents=True)

        # Two version files
        vf_v200 = wp / "versions" / "app" / "v2.0.0.yaml"
        vf_v200.write_text(_VERSION_YAML_V200)
        vf_v210 = wp / "versions" / "app" / "v2.1.0.yaml"
        vf_v210.write_text(_VERSION_YAML_LOCKED)

        # Ring lock pointing at v2.1.0, with previous pointing at v2.0.0
        lock_path = wp / "versions" / "app" / "dev.lock.yaml"
        lock_path.write_text("""\
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: dev
spec:
  ring: dev
  source: v2.1.0.yaml
  hash: "sha256:aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabb"
  version: v2-1-0
  previous:
    source: v2.0.0.yaml
    version: v2-0-0
    hash: "sha256:0000111122223333000011112222333300001111222233330000111122223333"
""")
        return wp

    def test_dry_run_returns_plan(self, tmp_path):
        wp = self._make_workspace_with_previous(tmp_path)
        ctrl = PromoteController()
        result = ctrl.run_pointer_rollback("dev", "app-wave", wp, dry_run=True)
        assert result["dry_run"] is True
        assert result["rollback_to_version"] == "v2-0-0"
        assert result["previous_source"] == "v2.0.0.yaml"

    def test_rollback_rewrites_lock_to_previous(self, tmp_path):
        wp = self._make_workspace_with_previous(tmp_path)
        ctrl = _no_git(PromoteController())
        result = ctrl.run_pointer_rollback("dev", "app-wave", wp)
        assert not ctrl.has_errors(), ctrl.get_errors()

        lock_path = wp / "versions" / "app" / "dev.lock.yaml"
        data = yaml.safe_load(lock_path.read_text())
        spec = data["spec"]
        assert "v2.0.0.yaml" in spec["source"]
        assert spec["version"] == "v2-0-0"

    def test_rollback_writes_new_previous_pointing_to_current(self, tmp_path):
        """After rollback, spec.previous should point to v2.1.0 (the version we rolled back from)."""
        wp = self._make_workspace_with_previous(tmp_path)
        ctrl = _no_git(PromoteController())
        ctrl.run_pointer_rollback("dev", "app-wave", wp)

        lock_path = wp / "versions" / "app" / "dev.lock.yaml"
        data = yaml.safe_load(lock_path.read_text())
        previous = data["spec"].get("previous", {})
        assert "v2.1.0.yaml" in previous.get("source", "")

    def test_rollback_fails_if_no_previous(self, tmp_path):
        wp = tmp_path
        (wp / ".strata").mkdir()
        (wp / ".strata" / "configuration.yaml").write_text(_CONFIG_YAML)
        (wp / "versions" / "app").mkdir(parents=True)
        lock_path = wp / "versions" / "app" / "dev.lock.yaml"
        lock_path.write_text("""\
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: dev
spec:
  ring: dev
  source: v2.1.0.yaml
""")
        ctrl = PromoteController()
        result = ctrl.run_pointer_rollback("dev", "app-wave", wp)
        assert result == {}
        assert ctrl.has_errors()
        assert "no spec.previous" in ctrl.get_errors()[0].lower()

    def test_rollback_fails_if_inline_pins_lock(self, tmp_path):
        wp = tmp_path
        (wp / ".strata").mkdir()
        (wp / ".strata" / "configuration.yaml").write_text(_CONFIG_YAML)
        (wp / "versions" / "app").mkdir(parents=True)
        lock_path = wp / "versions" / "app" / "dev.lock.yaml"
        lock_path.write_text("""\
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: dev
spec:
  ring: dev
  pins:
    - target: {type: image, name: app}
      version: v2.0.0
""")
        ctrl = PromoteController()
        result = ctrl.run_pointer_rollback("dev", "app-wave", wp)
        assert result == {}
        assert ctrl.has_errors()
        assert "inline-pins" in ctrl.get_errors()[0]
