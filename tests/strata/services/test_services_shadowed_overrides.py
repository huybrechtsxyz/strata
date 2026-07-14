"""Tests for P-5b: shadowed-override detection during strata validate --deep."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml

from strata.models.environment_model import (
    EnvironmentModel,
    EnvironmentOverridesModel,
    EnvironmentRemoteOverrideModel,
)
from strata.models.version_lock_model import VersionPinTargetType
from strata.services.version_service import VersionService
from strata.validators.base_validator import BaseValidator

_API_VERSION = "strata.huybrechts.xyz/v1"


# ─── helpers ──────────────────────────────────────────────────────────────────


def _env_with_remote_override(remote: str, reference: str) -> EnvironmentModel:
    overrides = EnvironmentOverridesModel(remotes=[EnvironmentRemoteOverrideModel(remote=remote, reference=reference)])
    raw = {
        "apiVersion": _API_VERSION,
        "kind": "environment",
        "meta": {"name": "test-env"},
        "spec": {"overrides": {"remotes": [{"remote": remote, "reference": reference}]}},
    }
    return EnvironmentModel.model_validate(raw)


def _env_with_module_override(module: str, chart_version: str) -> EnvironmentModel:
    raw = {
        "apiVersion": _API_VERSION,
        "kind": "environment",
        "meta": {"name": "test-env"},
        "spec": {"overrides": {"modules": [{"module": module, "chart_version": chart_version}]}},
    }
    return EnvironmentModel.model_validate(raw)


def _env_no_overrides() -> EnvironmentModel:
    raw = {
        "apiVersion": _API_VERSION,
        "kind": "environment",
        "meta": {"name": "test-env"},
        "spec": {},
    }
    return EnvironmentModel.model_validate(raw)


def _empty_pins() -> Dict:
    return {
        VersionPinTargetType.REMOTE: {},
        VersionPinTargetType.HELM_CHART: {},
        VersionPinTargetType.IMAGE: {},
        VersionPinTargetType.TOOL: {},
    }


# ─── VersionService.find_shadowed_overrides ───────────────────────────────────


class TestFindShadowedOverrides:
    """Unit tests for VersionService.find_shadowed_overrides."""

    def test_no_overrides_no_warnings(self):
        env = _env_no_overrides()
        pins = _empty_pins()
        result = VersionService.find_shadowed_overrides(env, pins)
        assert result == []

    def test_empty_pins_no_warnings(self):
        env = _env_with_remote_override("iac_core", "v2.4.0")
        pins = _empty_pins()
        result = VersionService.find_shadowed_overrides(env, pins)
        assert result == []

    def test_remote_pin_different_version_shadows_override(self):
        env = _env_with_remote_override("iac_core", "v2.4.0")
        pins = _empty_pins()
        pins[VersionPinTargetType.REMOTE]["iac_core"] = "v2.5.0"
        result = VersionService.find_shadowed_overrides(env, pins)
        assert len(result) == 1
        assert "iac_core" in result[0]
        assert "v2.4.0" in result[0]
        assert "v2.5.0" in result[0]
        assert "no effect" in result[0]

    def test_remote_pin_same_version_no_warning(self):
        env = _env_with_remote_override("iac_core", "v2.5.0")
        pins = _empty_pins()
        pins[VersionPinTargetType.REMOTE]["iac_core"] = "v2.5.0"
        result = VersionService.find_shadowed_overrides(env, pins)
        assert result == []

    def test_remote_pin_different_module_no_warning(self):
        env = _env_with_remote_override("iac_core", "v2.4.0")
        pins = _empty_pins()
        pins[VersionPinTargetType.REMOTE]["other_module"] = "v2.5.0"
        result = VersionService.find_shadowed_overrides(env, pins)
        assert result == []

    def test_helm_chart_pin_shadows_module_override(self):
        env = _env_with_module_override("traefik", "28.0.0")
        pins = _empty_pins()
        pins[VersionPinTargetType.HELM_CHART]["traefik"] = "28.1.0"
        result = VersionService.find_shadowed_overrides(env, pins)
        assert len(result) == 1
        assert "traefik" in result[0]
        assert "28.0.0" in result[0]
        assert "28.1.0" in result[0]

    def test_helm_chart_pin_same_version_no_warning(self):
        env = _env_with_module_override("traefik", "28.1.0")
        pins = _empty_pins()
        pins[VersionPinTargetType.HELM_CHART]["traefik"] = "28.1.0"
        result = VersionService.find_shadowed_overrides(env, pins)
        assert result == []

    def test_does_not_mutate_env_model(self):
        env = _env_with_remote_override("iac_core", "v2.4.0")
        pins = _empty_pins()
        pins[VersionPinTargetType.REMOTE]["iac_core"] = "v2.5.0"
        VersionService.find_shadowed_overrides(env, pins)
        # The override must still be the original value — model was not mutated
        assert env.spec.overrides.remotes[0].reference == "v2.4.0"

    def test_multiple_shadows_all_reported(self):
        raw = {
            "apiVersion": _API_VERSION,
            "kind": "environment",
            "meta": {"name": "test-env"},
            "spec": {
                "overrides": {
                    "remotes": [{"remote": "iac_core", "reference": "v2.4.0"}],
                    "modules": [{"module": "traefik", "chart_version": "28.0.0"}],
                }
            },
        }
        env = EnvironmentModel.model_validate(raw)
        pins = _empty_pins()
        pins[VersionPinTargetType.REMOTE]["iac_core"] = "v2.5.0"
        pins[VersionPinTargetType.HELM_CHART]["traefik"] = "28.1.0"
        result = VersionService.find_shadowed_overrides(env, pins)
        assert len(result) == 2


# ─── BaseValidator warnings ───────────────────────────────────────────────────


class TestBaseValidatorWarnings:
    """BaseValidator.add_validation_warning / get_warnings / has_warnings."""

    def _make_validator(self):
        """Concrete subclass for testing."""

        class _Impl(BaseValidator):
            def validate(self, work_path):
                return True

            def before_validate(self, work_path):
                return True

            def after_validate(self, work_path):
                return True

        return _Impl()

    def test_no_warnings_initially(self):
        v = self._make_validator()
        assert not v.has_warnings()
        assert v.get_warnings() == []

    def test_add_warning_accumulates(self):
        v = self._make_validator()
        v.add_validation_warning("first warning")
        v.add_validation_warning("second warning")
        assert v.has_warnings()
        assert len(v.get_warnings()) == 2

    def test_warnings_do_not_affect_errors(self):
        v = self._make_validator()
        v.add_validation_warning("a warning")
        assert not v.has_errors()


# ─── DeploymentService._check_version_pin_shadows ─────────────────────────────


class TestCheckVersionPinShadows:
    """DeploymentService._check_version_pin_shadows via _validate_dynamic."""

    def _write_env(self, tmp_path: Path, remote: str, reference: str) -> Path:
        env = {
            "apiVersion": _API_VERSION,
            "kind": "environment",
            "meta": {"name": "prod-be"},
            "spec": {"overrides": {"remotes": [{"remote": remote, "reference": reference}]}},
        }
        p = tmp_path / "environments" / "prod-be.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.dump(env))
        return p

    def _write_lock(self, tmp_path: Path, ring: str, remote: str, version: str) -> Path:
        lock = {
            "apiVersion": _API_VERSION,
            "kind": "version-lock",
            "meta": {"name": ring},
            "spec": {
                "ring": ring,
                "pins": [{"target": {"type": "remote", "name": remote}, "version": version}],
            },
        }
        p = tmp_path / "versions" / f"{ring}.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.dump(lock))
        return p

    def _write_deployment(self, tmp_path: Path, env_file: str, version_file: str) -> Path:
        deploy = {
            "apiVersion": _API_VERSION,
            "kind": "deployment",
            "meta": {"name": "my-deploy"},
            "spec": {
                "workspace": {"name": "ws", "file": "workspace.yaml"},
                "environments": [{"file": env_file}],
                "versions": [{"file": version_file}],
                "layers": {"environment": "prod"},
            },
        }
        p = tmp_path / "deploy.yaml"
        p.write_text(yaml.dump(deploy))
        return p

    def test_no_versions_no_warnings(self, tmp_path):
        from strata.services.deployment_service import DeploymentService

        env_p = self._write_env(tmp_path, "iac_core", "v2.4.0")
        deploy_raw = {
            "apiVersion": _API_VERSION,
            "kind": "deployment",
            "meta": {"name": "my-deploy"},
            "spec": {
                "workspace": {"name": "ws", "file": "workspace.yaml"},
                "environments": [{"file": str(env_p)}],
                "layers": {"environment": "prod"},
            },
        }
        p = tmp_path / "deploy.yaml"
        p.write_text(yaml.dump(deploy_raw))
        svc = DeploymentService.load(str(p), validate=True)
        assert svc.model is not None
        # No versions in spec → shadow check never fires
        svc._validate_dynamic(work_path=str(tmp_path))
        assert svc.get_validation_warnings() == []

    def test_pin_shadows_override_produces_warning(self, tmp_path):
        from strata.services.deployment_service import DeploymentService

        env_p = self._write_env(tmp_path, "iac_core", "v2.4.0")
        lock_p = self._write_lock(tmp_path, "prd", "iac_core", "v2.5.0")
        deploy_p = self._write_deployment(tmp_path, str(env_p), str(lock_p))
        svc = DeploymentService.load(str(deploy_p), validate=True)
        assert svc.model is not None
        # Call _validate_dynamic directly to trigger the shadow check
        svc._validate_dynamic(work_path=str(tmp_path))
        warnings = svc.get_validation_warnings()
        assert len(warnings) == 1
        assert "iac_core" in warnings[0]
        assert "no effect" in warnings[0]

    def test_pin_same_version_no_warning(self, tmp_path):
        from strata.services.deployment_service import DeploymentService

        env_p = self._write_env(tmp_path, "iac_core", "v2.5.0")
        lock_p = self._write_lock(tmp_path, "prd", "iac_core", "v2.5.0")
        deploy_p = self._write_deployment(tmp_path, str(env_p), str(lock_p))
        svc = DeploymentService.load(str(deploy_p), validate=True)
        assert svc.model is not None
        svc._validate_dynamic(work_path=str(tmp_path))
        assert svc.get_validation_warnings() == []
