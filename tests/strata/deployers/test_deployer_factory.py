"""Unit tests for DeployerFactory."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strata.deployers.base_deployer import BaseDeployer
from strata.deployers.factory import DeployerFactory
from strata.deployers.terraform_deployer import TerraformDeployer


def _mock_stage(name: str = "infra", provisioner: str = "tf_main", topology=None):
    stage = MagicMock()
    stage.name = name
    stage.provisioner = provisioner
    stage.topology = topology
    stage.secrets = None
    return stage


def _mock_deployment_service(provisioners=None, topologies=None):
    svc = MagicMock()
    ws = MagicMock()
    ws.model.spec.provisioners = provisioners or []
    ws.model.spec.topology = topologies or []
    svc.get_workspace_service.return_value = ws
    return svc


def _mock_provisioner(name: str, ptype: str):
    p = MagicMock()
    p.name = name
    p.provisioner = ptype
    return p


def _mock_topology(name: str, provisioner_name: str):
    t = MagicMock()
    t.name = name
    t.provisioner = provisioner_name
    return t


class TestDeployerFactoryRegistration:
    """Tests for register / reset / get_known_types."""

    def setup_method(self):
        DeployerFactory.reset()

    def teardown_method(self):
        DeployerFactory.reset()

    def test_builtin_types_known(self):
        known = DeployerFactory.get_known_types()
        assert "terraform" in known
        assert "ansible" in known
        assert "compose" in known
        assert "helm" in known
        assert "script" in known

    def test_register_custom_type(self):
        class FakeDeployer(BaseDeployer):
            def get_deployer_name(self):
                return "fake"

            def get_supported_steps(self):
                return []

            def validate_workspace(self):
                return True, []

            def validate_environment(self):
                return True, []

            def setup(self):
                return True, []

            def check(self):
                return True, []

            def plan(self):
                return True, []

            def apply(self):
                return True, []

            def destroy(self):
                return True, []

            def plan_destroy(self):
                return True, []

            def show_plan(self):
                return True, {}, []

            def output(self):
                return True, {}, []

        DeployerFactory.register("fake", FakeDeployer)
        assert "fake" in DeployerFactory.get_known_types()
        assert DeployerFactory.is_known_type("fake")

    def test_reset_clears_registry(self):
        DeployerFactory.register("custom", MagicMock)  # type: ignore[arg-type]
        DeployerFactory.reset()
        assert "custom" not in DeployerFactory._registry

    def test_is_known_type_builtin(self):
        assert DeployerFactory.is_known_type("terraform")
        assert not DeployerFactory.is_known_type("pulumi")


class TestDeployerFactoryCreate:
    """Tests for create()."""

    def setup_method(self):
        DeployerFactory.reset()

    def teardown_method(self):
        DeployerFactory.reset()

    def test_create_terraform(self, tmp_path: Path):
        stage = _mock_stage()
        deployer = DeployerFactory.create(
            "terraform",
            stage=stage,
            deployment_service=MagicMock(),
            configuration_service=MagicMock(),
            build_path=tmp_path,
            work_path=tmp_path,
        )
        assert isinstance(deployer, TerraformDeployer)
        assert deployer.get_deployer_name() == "terraform"

    def test_create_unknown_raises(self, tmp_path: Path):
        stage = _mock_stage()
        with pytest.raises(ValueError, match="Unknown deployer type.*pulumi"):
            DeployerFactory.create(
                "pulumi",
                stage=stage,
                deployment_service=MagicMock(),
                configuration_service=MagicMock(),
                build_path=tmp_path,
                work_path=tmp_path,
            )

    def test_create_passes_resolved_values(self, tmp_path: Path):
        stage = _mock_stage()
        rv = MagicMock()
        deployer = DeployerFactory.create(
            "terraform",
            stage=stage,
            deployment_service=MagicMock(),
            configuration_service=MagicMock(),
            build_path=tmp_path,
            work_path=tmp_path,
            resolved_values=rv,
        )
        assert deployer.resolved_values is rv

    def test_create_passes_solution_controller(self, tmp_path: Path):
        stage = _mock_stage()
        sc = MagicMock()
        deployer = DeployerFactory.create(
            "terraform",
            stage=stage,
            deployment_service=MagicMock(),
            configuration_service=MagicMock(),
            build_path=tmp_path,
            work_path=tmp_path,
            solution_controller=sc,
        )
        assert deployer.solution_controller is sc

    def test_create_builtin_caches_in_registry(self, tmp_path: Path):
        stage = _mock_stage()
        DeployerFactory.create(
            "terraform",
            stage=stage,
            deployment_service=MagicMock(),
            configuration_service=MagicMock(),
            build_path=tmp_path,
            work_path=tmp_path,
        )
        # After first create, class should be cached in _registry
        assert "terraform" in DeployerFactory._registry


class TestDeployerFactoryResolveType:
    """Tests for resolve_type()."""

    def test_resolve_via_provisioner(self):
        prov = _mock_provisioner("tf_main", "terraform")
        svc = _mock_deployment_service(provisioners=[prov])
        stage = _mock_stage(provisioner="tf_main")

        resolved, errors = DeployerFactory.resolve_type(stage, svc)
        assert resolved == "terraform"
        assert errors == []

    def test_resolve_via_topology(self):
        prov = _mock_provisioner("infra", "terraform")
        topo = _mock_topology("core", "infra")
        svc = _mock_deployment_service(provisioners=[prov], topologies=[topo])
        stage = _mock_stage(provisioner=None, topology="core")

        resolved, errors = DeployerFactory.resolve_type(stage, svc)
        assert resolved == "terraform"
        assert errors == []

    def test_resolve_missing_provisioner_name(self):
        prov = _mock_provisioner("other", "terraform")
        svc = _mock_deployment_service(provisioners=[prov])
        stage = _mock_stage(provisioner="missing")

        resolved, errors = DeployerFactory.resolve_type(stage, svc)
        assert resolved is None
        assert any("not found" in e for e in errors)

    def test_resolve_no_provisioner_no_topology(self):
        svc = _mock_deployment_service()
        stage = _mock_stage(provisioner=None, topology=None)

        resolved, errors = DeployerFactory.resolve_type(stage, svc)
        assert resolved is None
        assert any("either 'provisioner' or 'topology'" in e for e in errors)

    def test_resolve_unknown_type_errors(self):
        prov = _mock_provisioner("custom", "pulumi")
        svc = _mock_deployment_service(provisioners=[prov])
        stage = _mock_stage(provisioner="custom")

        resolved, errors = DeployerFactory.resolve_type(stage, svc)
        assert resolved is None
        assert any("unsupported type" in e for e in errors)

    def test_resolve_topology_not_found(self):
        svc = _mock_deployment_service()
        stage = _mock_stage(provisioner=None, topology="missing")

        resolved, errors = DeployerFactory.resolve_type(stage, svc)
        assert resolved is None
        assert any("topology" in e and "not found" in e for e in errors)

    def test_resolve_topology_references_missing_provisioner(self):
        topo = _mock_topology("core", "missing_prov")
        svc = _mock_deployment_service(topologies=[topo])
        stage = _mock_stage(provisioner=None, topology="core")

        resolved, errors = DeployerFactory.resolve_type(stage, svc)
        assert resolved is None
        assert any("not defined" in e for e in errors)

    def test_resolve_workspace_service_none(self):
        svc = MagicMock()
        svc.get_workspace_service.return_value = None
        stage = _mock_stage()

        resolved, errors = DeployerFactory.resolve_type(stage, svc)
        assert resolved is None
        assert len(errors) > 0


class TestDeployerFactoryPluginLoading:
    """Tests for load_plugins()."""

    def setup_method(self):
        DeployerFactory.reset()

    def teardown_method(self):
        DeployerFactory.reset()

    def test_no_plugins_dir(self, tmp_path: Path):
        """load_plugins with no .strata/provisioners/ dir is a no-op."""
        DeployerFactory.load_plugins(tmp_path)
        # Should not raise, registry stays empty
        assert len(DeployerFactory._registry) == 0

    def test_load_valid_plugin(self, tmp_path: Path):
        """load_plugins discovers BaseDeployer subclass from .py file."""
        plugins_dir = tmp_path / ".strata" / "provisioners"
        plugins_dir.mkdir(parents=True)

        plugin_code = """
from strata.deployers.base_deployer import BaseDeployer

class TestPluginDeployer(BaseDeployer):
    def get_deployer_name(self):
        return "test_plugin"
    def get_supported_steps(self):
        return ["setup", "apply"]
    def validate_workspace(self):
        return True, []
    def validate_environment(self):
        return True, []
    def setup(self):
        return True, []
    def check(self):
        return True, []
    def plan(self):
        return True, []
    def apply(self):
        return True, []
    def destroy(self):
        return True, []
    def plan_destroy(self):
        return True, []
    def show_plan(self):
        return True, {}, []
    def output(self):
        return True, {}, []
"""
        (plugins_dir / "test_plugin.py").write_text(plugin_code)

        DeployerFactory.load_plugins(tmp_path)
        assert DeployerFactory.is_known_type("test_plugin")

    def test_skip_underscore_prefixed_files(self, tmp_path: Path):
        """Files starting with _ are skipped."""
        plugins_dir = tmp_path / ".strata" / "provisioners"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "_helper.py").write_text("# should be skipped")

        DeployerFactory.load_plugins(tmp_path)
        assert len(DeployerFactory._registry) == 0

    def test_invalid_plugin_logged_not_raised(self, tmp_path: Path):
        """Plugin with syntax error logs warning but doesn't crash."""
        plugins_dir = tmp_path / ".strata" / "provisioners"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "bad.py").write_text("def broken(:\n")

        # Should not raise
        DeployerFactory.load_plugins(tmp_path)
        assert len(DeployerFactory._registry) == 0


class TestBaseDeployerLifecycleMethods:
    """Tests for the new status() and health() default methods."""

    def test_status_default(self, tmp_path: Path):
        deployer = TerraformDeployer(
            stage=MagicMock(),
            deployment_service=MagicMock(),
            configuration_service=MagicMock(),
            build_path=tmp_path,
            work_path=tmp_path,
        )
        ok, data, msgs = deployer.status()
        assert ok is True
        assert isinstance(data, dict)

    def test_health_default(self, tmp_path: Path):
        deployer = TerraformDeployer(
            stage=MagicMock(),
            deployment_service=MagicMock(),
            configuration_service=MagicMock(),
            build_path=tmp_path,
            work_path=tmp_path,
        )
        ok, data, msgs = deployer.health()
        assert ok is True
        assert isinstance(data, dict)


class TestDeployerFactoryManifestLoading:
    """Tests for provisioner.yaml manifest loading inside load_plugins()."""

    _PLUGIN_CODE = """
from strata.deployers.base_deployer import BaseDeployer

class ManifestPluginDeployer(BaseDeployer):
    def get_deployer_name(self): return "myplugin"
    def get_supported_steps(self): return ["setup", "apply"]
    def validate_workspace(self): return True, []
    def validate_environment(self): return True, []
    def setup(self): return True, []
    def check(self): return True, []
    def plan(self): return True, []
    def apply(self): return True, []
    def destroy(self): return True, []
    def plan_destroy(self): return True, []
    def show_plan(self): return True, {}, []
    def output(self): return True, {}, []
"""

    def setup_method(self):
        DeployerFactory.reset()

    def teardown_method(self):
        DeployerFactory.reset()

    def test_load_sibling_yaml_manifest(self, tmp_path: Path):
        """load_plugins() reads a sibling {stem}.yaml alongside the .py file."""
        plugins_dir = tmp_path / ".strata" / "provisioners"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "myplugin.py").write_text(self._PLUGIN_CODE)
        (plugins_dir / "myplugin.yaml").write_text(
            "name: myplugin\nversion: '1.2.3'\ndescription: Test plugin\nrequires:\n  - myplugin-cli\n"
        )

        DeployerFactory.load_plugins(tmp_path)

        manifests = DeployerFactory.get_manifests()
        assert "myplugin" in manifests
        assert manifests["myplugin"].version == "1.2.3"
        assert manifests["myplugin"].description == "Test plugin"
        assert manifests["myplugin"].requires == ["myplugin-cli"]

    def test_load_fallback_provisioner_yaml(self, tmp_path: Path):
        """Falls back to provisioner.yaml when no {stem}.yaml exists."""
        plugins_dir = tmp_path / ".strata" / "provisioners"
        plugins_dir.mkdir(parents=True)
        plugin_code = self._PLUGIN_CODE.replace('"myplugin"', '"fallback_plugin"').replace(
            "ManifestPluginDeployer", "FallbackPluginDeployer"
        )
        (plugins_dir / "fallback_plugin.py").write_text(plugin_code)
        (plugins_dir / "provisioner.yaml").write_text("name: fallback_plugin\nversion: '0.1.0'\n")

        DeployerFactory.load_plugins(tmp_path)

        manifests = DeployerFactory.get_manifests()
        assert "fallback_plugin" in manifests
        assert manifests["fallback_plugin"].version == "0.1.0"

    def test_invalid_manifest_does_not_crash(self, tmp_path: Path):
        """A broken provisioner.yaml is logged but doesn't prevent plugin load."""
        plugins_dir = tmp_path / ".strata" / "provisioners"
        plugins_dir.mkdir(parents=True)
        plugin_code = self._PLUGIN_CODE.replace('"myplugin"', '"badmanifest_plugin"').replace(
            "ManifestPluginDeployer", "BadManifestDeployer"
        )
        (plugins_dir / "badmanifest_plugin.py").write_text(plugin_code)
        (plugins_dir / "badmanifest_plugin.yaml").write_text("!!invalid yaml: [")

        DeployerFactory.load_plugins(tmp_path)

        # Plugin still registered despite bad manifest
        assert DeployerFactory.is_known_type("badmanifest_plugin")
        # Manifest not loaded
        assert "badmanifest_plugin" not in DeployerFactory.get_manifests()

    def test_reset_clears_manifests(self, tmp_path: Path):
        """reset() also clears _manifests."""
        plugins_dir = tmp_path / ".strata" / "provisioners"
        plugins_dir.mkdir(parents=True)
        plugin_code = self._PLUGIN_CODE.replace('"myplugin"', '"reset_plugin"').replace(
            "ManifestPluginDeployer", "ResetPluginDeployer"
        )
        (plugins_dir / "reset_plugin.py").write_text(plugin_code)
        (plugins_dir / "reset_plugin.yaml").write_text("name: reset_plugin\nversion: '1.0.0'\n")

        DeployerFactory.load_plugins(tmp_path)
        assert len(DeployerFactory.get_manifests()) == 1

        DeployerFactory.reset()
        assert len(DeployerFactory.get_manifests()) == 0

    def test_get_manifests_returns_copy(self, tmp_path: Path):
        """get_manifests() returns a copy so mutations don't affect internal state."""
        plugins_dir = tmp_path / ".strata" / "provisioners"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "myplugin.py").write_text(self._PLUGIN_CODE)
        (plugins_dir / "myplugin.yaml").write_text("name: myplugin\nversion: '1.0.0'\n")

        DeployerFactory.load_plugins(tmp_path)
        copy = DeployerFactory.get_manifests()
        copy["injected"] = None  # type: ignore[assignment]
        assert "injected" not in DeployerFactory.get_manifests()
