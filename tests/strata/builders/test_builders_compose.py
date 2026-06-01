"""Unit tests for ComposeBuilder."""

from unittest.mock import MagicMock, patch

import yaml

from strata.builders.compose_builder import ComposeBuilder
from strata.models.common_models import ServiceDeployerType
from strata.models.module_model import (
    ModuleCheckModel,
    ModuleMountModel,
    ModuleServiceEnvironmentModel,
    ModuleServiceModel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_deployment_service(validated=True, build_path=None, namespace_services=None):
    svc = MagicMock()
    svc.is_validated.return_value = validated
    svc.get_workspace_service.return_value = MagicMock()
    if build_path:
        svc.get_build_path.return_value = build_path
    svc.get_namespace_services.return_value = namespace_services or {}
    return svc


def _mock_namespace_service(module_refs=None):
    """Return a mock NamespaceService with the given module references."""
    ns_svc = MagicMock()
    ns_svc.is_validated.return_value = True
    ns_svc.model = MagicMock()
    ns_svc.model.spec = MagicMock()
    ns_svc.model.spec.modules = module_refs or []
    return ns_svc


def _module_ref(name: str, file: str):
    ref = MagicMock()
    ref.name = name
    ref.file = file
    return ref


def _make_compose_module(name: str, services):
    """Return a mock ModuleModel with type=compose and the given services."""
    mod = MagicMock()
    mod.meta = MagicMock()
    mod.meta.name = name
    mod.spec = MagicMock()
    mod.spec.type = ServiceDeployerType.COMPOSE
    mod.spec.services = services
    return mod


def _make_service(
    name: str,
    image=None,
    command=None,
    restart=None,
    environment=None,
    ports=None,
    mounts=None,
    depends_on=None,
    healthcheck=None,
    configuration=None,
):
    svc = MagicMock(spec=ModuleServiceModel)
    svc.name = name
    svc.image = image
    svc.command = command
    svc.restart = restart
    svc.environment = environment
    svc.ports = ports
    svc.mounts = mounts
    svc.depends_on = depends_on
    svc.healthcheck = healthcheck
    svc.configuration = configuration
    return svc


def _make_mod_service(validated=True, module=None):
    ms = MagicMock()
    ms.is_validated.return_value = validated
    ms.model = module
    ms.get_validation_errors.return_value = ["validation error"]
    return ms


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestComposeBuilderInit:
    def test_defaults(self):
        builder = ComposeBuilder()
        assert builder.verbose is False
        assert not builder.has_errors()
        assert not builder.has_messages()

    def test_verbose(self):
        builder = ComposeBuilder(verbose=True)
        assert builder.verbose is True


# ---------------------------------------------------------------------------
# before_build
# ---------------------------------------------------------------------------


class TestComposeBuilderBeforeBuild:
    def test_not_validated_returns_false(self, tmp_path):
        builder = ComposeBuilder()
        svc = _mock_deployment_service(validated=False)
        assert builder.before_build(svc, tmp_path, tmp_path) is False
        assert any("not validated" in e for e in builder.get_errors())

    def test_no_workspace_service_returns_false(self, tmp_path):
        builder = ComposeBuilder()
        svc = _mock_deployment_service(validated=True)
        svc.get_workspace_service.return_value = None
        assert builder.before_build(svc, tmp_path, tmp_path) is False
        assert any("Workspace" in e for e in builder.get_errors())

    def test_valid_service_returns_true(self, tmp_path):
        builder = ComposeBuilder()
        svc = _mock_deployment_service(validated=True)
        assert builder.before_build(svc, tmp_path, tmp_path) is True
        assert not builder.has_errors()

    def test_verbose_emits_message(self, tmp_path):
        builder = ComposeBuilder(verbose=True)
        svc = _mock_deployment_service(validated=True)
        builder.before_build(svc, tmp_path, tmp_path)
        assert any("validation passed" in m for m in builder.get_messages())


# ---------------------------------------------------------------------------
# after_build
# ---------------------------------------------------------------------------


class TestComposeBuilderAfterBuild:
    def test_always_true(self, tmp_path):
        builder = ComposeBuilder()
        svc = _mock_deployment_service()
        assert builder.after_build(svc, tmp_path, tmp_path) is True

    def test_dry_run_verbose_emits_message(self, tmp_path):
        builder = ComposeBuilder(verbose=True)
        svc = _mock_deployment_service()
        builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert any("DRY-RUN" in m for m in builder.get_messages())


# ---------------------------------------------------------------------------
# build — no namespaces / no compose modules
# ---------------------------------------------------------------------------


class TestComposeBuilderBuildNoOp:
    def test_no_namespaces_returns_true(self, tmp_path):
        builder = ComposeBuilder()
        svc = _mock_deployment_service(namespace_services={})
        assert builder.build(svc, tmp_path, tmp_path) is True
        assert not builder.has_errors()

    def test_namespace_without_modules_skipped(self, tmp_path):
        builder = ComposeBuilder()
        ns_svc = _mock_namespace_service(module_refs=[])
        dep_svc = _mock_deployment_service(
            build_path=tmp_path,
            namespace_services={"ns1": ns_svc},
        )
        assert builder.build(dep_svc, tmp_path, tmp_path) is True
        assert not builder.has_errors()
        assert not (tmp_path / "ns1" / "docker-compose.yml").exists()

    def test_non_compose_module_skipped(self, tmp_path):
        """A module with type != compose should produce no file."""
        non_compose_module = _make_compose_module("mymod", services=[_make_service("web")])
        non_compose_module.spec.type = ServiceDeployerType.HELM  # override to helm

        mod_service = _make_mod_service(module=non_compose_module)
        mod_ref = _module_ref("mymod", "dummy.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        with (
            patch("strata.builders.compose_builder.resolve_path") as mock_rp,
            patch("strata.builders.compose_builder.ModuleService.load", return_value=mod_service),
        ):
            module_path = tmp_path / "dummy.yaml"
            module_path.write_text("")
            mock_rp.return_value = module_path
            result = builder = ComposeBuilder()
            result = builder.build(dep_svc, tmp_path, tmp_path)

        assert result is True
        assert not (tmp_path / "ns1" / "docker-compose.yml").exists()


# ---------------------------------------------------------------------------
# build — compose file generation
# ---------------------------------------------------------------------------


class TestComposeBuilderBuildOutput:
    def _run_build(self, tmp_path, services, namespace="testns", module_name="mymod"):
        """Helper: build one namespace with one compose module and return parsed YAML."""
        compose_module = _make_compose_module(module_name, services=services)
        mod_service = _make_mod_service(module=compose_module)
        mod_ref = _module_ref(module_name, "module.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={namespace: ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = ComposeBuilder()
        with (
            patch("strata.builders.compose_builder.resolve_path") as mock_rp,
            patch("strata.builders.compose_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        assert ok is True, builder.get_errors()
        compose_file = tmp_path / namespace / "docker-compose.yml"
        assert compose_file.exists()
        return yaml.safe_load(compose_file.read_text())

    def test_minimal_service(self, tmp_path):
        svc = _make_service("web", image="nginx:alpine")
        doc = self._run_build(tmp_path, [svc])
        assert "mymod-web" in doc["services"]
        assert doc["services"]["mymod-web"]["image"] == "nginx:alpine"

    def test_service_name_no_prefix_when_equal(self, tmp_path):
        """When module name == service name the prefix is omitted."""
        svc = _make_service("mymod", image="mymod:latest")
        doc = self._run_build(tmp_path, [svc])
        assert "mymod" in doc["services"]
        assert "mymod-mymod" not in doc["services"]

    def test_restart_policy(self, tmp_path):
        svc = _make_service("api", image="app:1", restart="unless-stopped")
        doc = self._run_build(tmp_path, [svc])
        assert doc["services"]["mymod-api"]["restart"] == "unless-stopped"

    def test_command_list(self, tmp_path):
        svc = _make_service("worker", image="app:1", command=["worker", "--queue", "default"])
        doc = self._run_build(tmp_path, [svc])
        assert doc["services"]["mymod-worker"]["command"] == ["worker", "--queue", "default"]

    def test_ports(self, tmp_path):
        svc = _make_service("web", image="nginx:alpine", ports=["8080:80", "443:443"])
        doc = self._run_build(tmp_path, [svc])
        assert doc["services"]["mymod-web"]["ports"] == ["8080:80", "443:443"]

    def test_environment_value(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "TZ"
        env.value = "Europe/Brussels"
        env.var = None
        env.secret = None
        env.feature = None
        svc = _make_service("app", image="app:1", environment=[env])
        doc = self._run_build(tmp_path, [svc])
        assert doc["services"]["mymod-app"]["environment"]["TZ"] == "Europe/Brussels"

    def test_environment_var_emits_substitution(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "APP_VERSION"
        env.value = None
        env.var = "APP_VERSION"
        env.secret = None
        env.feature = None
        svc = _make_service("app", image="app:1", environment=[env])
        doc = self._run_build(tmp_path, [svc])
        assert doc["services"]["mymod-app"]["environment"]["APP_VERSION"] == "${APP_VERSION}"

    def test_environment_secret_emits_substitution(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "DB_PASSWORD"
        env.value = None
        env.var = None
        env.secret = "DB_PASSWORD"
        env.feature = None
        svc = _make_service("db", image="postgres:16", environment=[env])
        doc = self._run_build(tmp_path, [svc])
        assert doc["services"]["mymod-db"]["environment"]["DB_PASSWORD"] == "${DB_PASSWORD}"

    def test_environment_feature_emits_substitution(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "ENABLE_METRICS"
        env.value = None
        env.var = None
        env.secret = None
        env.feature = "enable_metrics"
        svc = _make_service("app", image="app:1", environment=[env])
        doc = self._run_build(tmp_path, [svc])
        assert doc["services"]["mymod-app"]["environment"]["ENABLE_METRICS"] == "${enable_metrics}"

    def test_volume_ref_creates_named_volume(self, tmp_path):
        mount = MagicMock(spec=ModuleMountModel)
        mount.volume_ref = "data"
        mount.source_path = None
        mount.target_path = "/var/data"
        mount.type = "volume"
        svc = _make_service("db", image="postgres:16", mounts=[mount])
        doc = self._run_build(tmp_path, [svc], namespace="myns", module_name="mymod")
        expected_vol = "myns_mymod_data"
        assert expected_vol in doc.get("volumes", {})
        assert f"{expected_vol}:/var/data" in doc["services"]["mymod-db"]["volumes"]

    def test_bind_mount(self, tmp_path):
        mount = MagicMock(spec=ModuleMountModel)
        mount.volume_ref = None
        mount.source_path = "./config/traefik.yml"
        mount.target_path = "/etc/traefik/traefik.yml"
        mount.type = "bind"
        svc = _make_service("proxy", image="traefik:v3", mounts=[mount])
        doc = self._run_build(tmp_path, [svc])
        assert "./config/traefik.yml:/etc/traefik/traefik.yml" in doc["services"]["mymod-proxy"]["volumes"]

    def test_depends_on_rewritten_to_prefixed(self, tmp_path):
        svc_a = _make_service("db", image="postgres:16")
        svc_b = _make_service("api", image="app:1", depends_on=["db"])
        doc = self._run_build(tmp_path, [svc_a, svc_b])
        assert doc["services"]["mymod-api"]["depends_on"] == ["mymod-db"]

    def test_healthcheck_command(self, tmp_path):
        hc = MagicMock(spec=ModuleCheckModel)
        hc.command = ["pgrep", "-f", "traefik"]
        hc.type = "command"
        hc.target = None
        hc.interval = "30s"
        hc.timeout = "5s"
        hc.retries = 3
        svc = _make_service("proxy", image="traefik:v3", healthcheck=hc)
        doc = self._run_build(tmp_path, [svc])
        hc_doc = doc["services"]["mymod-proxy"]["healthcheck"]
        assert hc_doc["test"] == ["CMD", "pgrep", "-f", "traefik"]
        assert hc_doc["interval"] == "30s"
        assert hc_doc["retries"] == 3

    def test_healthcheck_http(self, tmp_path):
        hc = MagicMock(spec=ModuleCheckModel)
        hc.command = None
        hc.type = "http"
        hc.target = "http://localhost:8080/ping"
        hc.interval = "15s"
        hc.timeout = "3s"
        hc.retries = 2
        svc = _make_service("api", image="app:1", healthcheck=hc)
        doc = self._run_build(tmp_path, [svc])
        hc_doc = doc["services"]["mymod-api"]["healthcheck"]
        assert hc_doc["test"][0] == "CMD-SHELL"
        assert "curl" in hc_doc["test"][1]

    def test_configuration_merged_verbatim(self, tmp_path):
        svc = _make_service("app", image="app:1", configuration={"logging": {"driver": "json-file"}})
        doc = self._run_build(tmp_path, [svc])
        assert doc["services"]["mymod-app"]["logging"] == {"driver": "json-file"}

    def test_dry_run_no_file_written(self, tmp_path):
        compose_module = _make_compose_module("mod", services=[_make_service("web", image="nginx:alpine")])
        mod_service = _make_mod_service(module=compose_module)
        mod_ref = _module_ref("mod", "module.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = ComposeBuilder()
        with (
            patch("strata.builders.compose_builder.resolve_path") as mock_rp,
            patch("strata.builders.compose_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path, dry_run=True)

        assert ok is True
        assert not (tmp_path / "ns1" / "docker-compose.yml").exists()


# ---------------------------------------------------------------------------
# build — error paths
# ---------------------------------------------------------------------------


class TestComposeBuilderBuildErrors:
    def test_module_file_not_found(self, tmp_path):
        mod_ref = _module_ref("mod", "missing.yaml")
        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        builder = ComposeBuilder()
        with patch("strata.builders.compose_builder.resolve_path") as mock_rp:
            mock_rp.return_value = tmp_path / "missing.yaml"  # does not exist
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        assert ok is False
        assert any("not found" in e for e in builder.get_errors())

    def test_module_validation_failed(self, tmp_path):
        mod_ref = _module_ref("mod", "module.yaml")
        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        invalid_mod_svc = _make_mod_service(validated=False, module=None)

        builder = ComposeBuilder()
        with (
            patch("strata.builders.compose_builder.resolve_path") as mock_rp,
            patch("strata.builders.compose_builder.ModuleService.load", return_value=invalid_mod_svc),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        assert ok is False
        assert any("validation failed" in e for e in builder.get_errors())
