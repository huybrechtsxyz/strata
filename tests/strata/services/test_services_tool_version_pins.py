"""Tests for P-5a: type:tool version pin support."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml

from strata.models.version_lock_model import VersionPinTargetType
from strata.services.version_service import VersionService

_API_VERSION = "strata.huybrechts.xyz/v1"


# ─── helpers ──────────────────────────────────────────────────────────────────


def _empty_pins() -> Dict:
    return {
        VersionPinTargetType.REMOTE: {},
        VersionPinTargetType.HELM_CHART: {},
        VersionPinTargetType.IMAGE: {},
        VersionPinTargetType.TOOL: {},
    }


def _workspace_data(provisioner_name: str = "infra", provisioner_type: str = "terraform") -> dict:
    return {
        "apiVersion": _API_VERSION,
        "kind": "workspace",
        "meta": {"name": "ws"},
        "spec": {
            "providers": [{"name": "azure", "file": "providers/azure.yaml"}],
            "provisioners": [
                {
                    "name": provisioner_name,
                    "provisioner": provisioner_type,
                    "source": {"source_path": "terraform"},
                }
            ],
            "topology": [
                {
                    "name": "platform",
                    "provider": "azure",
                    "provisioner": provisioner_name,
                    "type": "kubernetes",
                    "components": [{"resource": "node"}],
                }
            ],
            "resources": [{"name": "node", "file": "resources/node.yaml"}],
        },
    }


# ─── WorkspaceIacModel.version field ──────────────────────────────────────────


class TestWorkspaceIacModelVersionField:
    """The new version field is optional and defaults to None."""

    def test_version_field_absent_defaults_to_none(self):
        from strata.models.workspace_model import WorkspaceIacModel

        raw = {
            "name": "infra",
            "provisioner": "terraform",
            "source": {"repository": "haven", "source_path": "terraform"},
        }
        model = WorkspaceIacModel.model_validate(raw)
        assert model.version is None

    def test_version_field_set(self):
        from strata.models.workspace_model import WorkspaceIacModel

        raw = {
            "name": "infra",
            "provisioner": "terraform",
            "source": {"repository": "haven", "source_path": "terraform"},
            "version": "1.8.3",
        }
        model = WorkspaceIacModel.model_validate(raw)
        assert model.version == "1.8.3"


# ─── VersionManifestPinsModel.tools ───────────────────────────────────────────


class TestVersionManifestPinsModelTools:
    """tools field on VersionManifestPinsModel is optional."""

    def test_tools_absent_defaults_to_none(self):
        from strata.models.version_manifest_model import VersionManifestPinsModel

        model = VersionManifestPinsModel.model_validate({})
        assert model.tools is None

    def test_tools_field_parsed(self):
        from strata.models.version_manifest_model import VersionManifestPinsModel

        model = VersionManifestPinsModel.model_validate({"tools": {"infra": "1.8.3"}})
        assert model.tools == {"infra": "1.8.3"}


# ─── VersionService.resolve_pins — manifest tools field ───────────────────────


class TestResolvePinsManifestTools:
    """resolve_pins includes manifest.tools in the TOOL bucket."""

    def _make_manifest(self, tools: dict) -> object:
        from strata.models.version_manifest_model import VersionManifestModel

        raw = {
            "apiVersion": _API_VERSION,
            "kind": "version",
            "meta": {"name": "prd"},
            "spec": {"ring": "prd", "pins": {"tools": tools}},
        }
        return VersionManifestModel.model_validate(raw)

    def test_tools_in_manifest_included_in_pins(self):
        manifest = self._make_manifest({"infra": "1.8.3"})
        result = VersionService.resolve_pins([manifest])
        assert result[VersionPinTargetType.TOOL] == {"infra": "1.8.3"}

    def test_manifest_without_tools_yields_empty_tool_bucket(self):
        from strata.models.version_manifest_model import VersionManifestModel

        raw = {
            "apiVersion": _API_VERSION,
            "kind": "version",
            "meta": {"name": "prd"},
            "spec": {"ring": "prd", "pins": {"remotes": {"iac_core": "v2.5.0"}}},
        }
        manifest = VersionManifestModel.model_validate(raw)
        result = VersionService.resolve_pins([manifest])
        assert result[VersionPinTargetType.TOOL] == {}

    def test_lock_file_tool_pin_included(self, tmp_path):
        """A version-lock with type:tool is already parsed by resolve_pins."""
        from strata.models.version_lock_model import VersionLockModel

        raw = {
            "apiVersion": _API_VERSION,
            "kind": "version-lock",
            "meta": {"name": "prd"},
            "spec": {
                "ring": "prd",
                "pins": [{"target": {"type": "tool", "name": "infra"}, "version": "1.8.3"}],
            },
        }
        lock = VersionLockModel.model_validate(raw)
        result = VersionService.resolve_pins([lock])
        assert result[VersionPinTargetType.TOOL] == {"infra": "1.8.3"}


# ─── VersionService.apply_to_workspace ────────────────────────────────────────


class TestApplyToWorkspace:
    """VersionService.apply_to_workspace patches provisioner.version fields."""

    def _load_workspace_spec(self, data: dict):
        from strata.models.workspace_model import WorkspaceModel

        return WorkspaceModel.model_validate(data).spec

    def test_no_tool_pins_returns_spec_unchanged(self):
        spec = self._load_workspace_spec(_workspace_data())
        pins = _empty_pins()
        result = VersionService.apply_to_workspace(spec, pins)
        assert result.provisioners[0].version is None

    def test_tool_pin_sets_provisioner_version(self):
        spec = self._load_workspace_spec(_workspace_data())
        pins = _empty_pins()
        pins[VersionPinTargetType.TOOL]["infra"] = "1.8.3"
        VersionService.apply_to_workspace(spec, pins)
        assert spec.provisioners[0].version == "1.8.3"

    def test_unmatched_pin_does_not_modify_provisioner(self):
        spec = self._load_workspace_spec(_workspace_data("infra"))
        pins = _empty_pins()
        pins[VersionPinTargetType.TOOL]["other"] = "1.8.3"
        VersionService.apply_to_workspace(spec, pins)
        assert spec.provisioners[0].version is None

    def test_pin_overwrites_existing_version(self):
        data = _workspace_data()
        data["spec"]["provisioners"][0]["version"] = "1.7.0"
        spec = self._load_workspace_spec(data)
        pins = _empty_pins()
        pins[VersionPinTargetType.TOOL]["infra"] = "1.8.3"
        VersionService.apply_to_workspace(spec, pins)
        assert spec.provisioners[0].version == "1.8.3"

    def test_multiple_provisioners_only_matching_patched(self):
        data = _workspace_data()
        data["spec"]["provisioners"].append(
            {
                "name": "config",
                "provisioner": "ansible",
                "source": {"source_path": "ansible"},
            }
        )
        data["spec"]["topology"].append(
            {
                "name": "configure",
                "provider": "azure",
                "provisioner": "config",
                "type": "kubernetes",
                "components": [{"resource": "node"}],
            }
        )
        spec = self._load_workspace_spec(data)
        pins = _empty_pins()
        pins[VersionPinTargetType.TOOL]["infra"] = "1.8.3"
        VersionService.apply_to_workspace(spec, pins)
        assert spec.provisioners[0].version == "1.8.3"  # patched
        assert spec.provisioners[1].version is None  # untouched


# ─── DeploymentService._apply_tool_version_pins ───────────────────────────────


class TestDeploymentServiceApplyToolVersionPins:
    """_apply_tool_version_pins returns patched WorkspaceService or original."""

    def _write_workspace(self, tmp_path: Path, provisioner_name: str = "infra") -> Path:
        data = _workspace_data(provisioner_name)
        # Remove resource reference since no actual file; test only loads model structure
        data["spec"].pop("resources", None)
        data["spec"]["topology"][0]["components"] = []
        data["spec"]["topology"][0]["components"].append({"resource": "dummy"})
        # Actually, WorkspaceIacModel.source needs to exist — simplify to minimum
        data = {
            "apiVersion": _API_VERSION,
            "kind": "workspace",
            "meta": {"name": "ws"},
            "spec": {
                "providers": [{"name": "azure", "file": "providers/azure.yaml"}],
                "provisioners": [
                    {
                        "name": provisioner_name,
                        "provisioner": "terraform",
                        "source": {"source_path": "terraform"},
                    }
                ],
                "topology": [
                    {
                        "name": "platform",
                        "provider": "azure",
                        "provisioner": provisioner_name,
                        "type": "kubernetes",
                        "components": [{"resource": "node"}],
                    }
                ],
                "resources": [{"name": "node", "file": "resources/node.yaml"}],
            },
        }
        p = tmp_path / "workspace.yaml"
        p.write_text(yaml.dump(data))
        return p

    def _write_lock(self, tmp_path: Path, provisioner: str, version: str) -> Path:
        lock = {
            "apiVersion": _API_VERSION,
            "kind": "version-lock",
            "meta": {"name": "prd"},
            "spec": {
                "ring": "prd",
                "pins": [{"target": {"type": "tool", "name": provisioner}, "version": version}],
            },
        }
        p = tmp_path / "versions" / "prd.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.dump(lock))
        return p

    def _write_env(self, tmp_path: Path) -> Path:
        env = {
            "apiVersion": _API_VERSION,
            "kind": "environment",
            "meta": {"name": "prod"},
            "spec": {},
        }
        p = tmp_path / "environments" / "prod.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.dump(env))
        return p

    def _write_deployment(self, tmp_path: Path, env_p: Path, ws_p: Path, lock_p: Path) -> Path:
        deploy = {
            "apiVersion": _API_VERSION,
            "kind": "deployment",
            "meta": {"name": "my-deploy"},
            "spec": {
                "workspace": {"name": "ws", "file": str(ws_p)},
                "environments": [{"file": str(env_p)}],
                "versions": [{"file": str(lock_p)}],
                "layers": {"segments": {"environment": "prod"}},
            },
        }
        p = tmp_path / "deploy.yaml"
        p.write_text(yaml.dump(deploy))
        return p

    def test_no_tool_pins_returns_original_service(self, tmp_path):
        from strata.services.deployment_service import DeploymentService
        from strata.services.workspace_service import WorkspaceService

        ws_p = self._write_workspace(tmp_path)
        env_p = self._write_env(tmp_path)
        lock_raw = {
            "apiVersion": _API_VERSION,
            "kind": "version-lock",
            "meta": {"name": "prd"},
            "spec": {
                "ring": "prd",
                "pins": [{"target": {"type": "remote", "name": "iac_core"}, "version": "v2.5.0"}],
            },
        }
        lock_p = tmp_path / "versions" / "prd.yaml"
        lock_p.parent.mkdir(parents=True, exist_ok=True)
        lock_p.write_text(yaml.dump(lock_raw))
        deploy_p = self._write_deployment(tmp_path, env_p, ws_p, lock_p)

        svc = DeploymentService.load(str(deploy_p), validate=True)
        ws_svc = WorkspaceService.load(str(ws_p), validate=True)
        result = svc._apply_tool_version_pins(ws_svc, str(tmp_path))
        # No tool pins → same object returned
        assert result is ws_svc

    def test_tool_pin_patches_provisioner_version(self, tmp_path):
        from strata.services.deployment_service import DeploymentService
        from strata.services.workspace_service import WorkspaceService

        ws_p = self._write_workspace(tmp_path)
        env_p = self._write_env(tmp_path)
        lock_p = self._write_lock(tmp_path, "infra", "1.8.3")
        deploy_p = self._write_deployment(tmp_path, env_p, ws_p, lock_p)

        svc = DeploymentService.load(str(deploy_p), validate=True)
        ws_svc = WorkspaceService.load(str(ws_p), validate=True)
        patched = svc._apply_tool_version_pins(ws_svc, str(tmp_path))

        # A new service is returned (not the original)
        assert patched is not ws_svc
        assert patched.model is not None
        infra = next(p for p in patched.model.spec.provisioners if p.name == "infra")
        assert infra.version == "1.8.3"

    def test_original_workspace_service_not_mutated(self, tmp_path):
        from strata.services.deployment_service import DeploymentService
        from strata.services.workspace_service import WorkspaceService

        ws_p = self._write_workspace(tmp_path)
        env_p = self._write_env(tmp_path)
        lock_p = self._write_lock(tmp_path, "infra", "1.8.3")
        deploy_p = self._write_deployment(tmp_path, env_p, ws_p, lock_p)

        svc = DeploymentService.load(str(deploy_p), validate=True)
        ws_svc = WorkspaceService.load(str(ws_p), validate=True)
        original_version = ws_svc.model.spec.provisioners[0].version

        svc._apply_tool_version_pins(ws_svc, str(tmp_path))

        # Original must be untouched (cache safety)
        assert ws_svc.model.spec.provisioners[0].version == original_version
