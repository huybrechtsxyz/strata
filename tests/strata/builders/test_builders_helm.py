"""Unit tests for HelmBuilder.

NOTE: `src/strata/builders/helm_builder.py` may not exist yet.
These tests are written from the design spec and will be collected
once the implementation is in place.
"""

from unittest.mock import MagicMock, patch

import pytest
import yaml

try:
    from strata.builders.helm_builder import HelmBuilder

    IMPL_MISSING = False
except ImportError:
    HelmBuilder = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

from strata.models.common_models import ServiceDeployerType
from strata.models.module_model import (
    ModuleMountModel,
    ModuleServiceEnvironmentModel,
    ModuleServiceModel,
)

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="HelmBuilder not yet implemented")

# ---------------------------------------------------------------------------
# Helpers — mirror test_builders_compose.py conventions exactly
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


def _make_helm_module(
    name: str, services, release_name=None, kubernetes_namespace=None, source=None, configuration=None
):
    """Return a mock ModuleModel with type=helm and the given services."""
    mod = MagicMock()
    mod.meta = MagicMock()
    mod.meta.name = name
    mod.spec = MagicMock()
    mod.spec.type = ServiceDeployerType.HELM
    mod.spec.services = services
    mod.spec.release_name = release_name
    mod.spec.kubernetes_namespace = kubernetes_namespace
    mod.spec.configuration = configuration
    # Default source: git-based (no chart fields)
    if source is None:
        src = MagicMock()
        src.chart_repository = None
        src.chart_name = None
        src.chart_version = None
        src.source_path = None
        src.repository = None
        mod.spec.source = src
    else:
        mod.spec.source = source
    return mod


def _make_service(
    name: str,
    image=None,
    environment=None,
    mounts=None,
    configuration=None,
):
    svc = MagicMock(spec=ModuleServiceModel)
    svc.name = name
    svc.image = image
    svc.environment = environment
    svc.mounts = mounts
    svc.configuration = configuration
    return svc


def _make_mod_service(validated=True, module=None):
    ms = MagicMock()
    ms.is_validated.return_value = validated
    ms.model = module
    ms.get_validation_errors.return_value = ["validation error"]
    return ms


def _make_pvc_mount(name="data", storage_class="fast-ssd", access_mode="ReadWriteOnce", storage_size="10Gi"):
    """Return a mock ModuleMountModel representing a Kubernetes PVC."""
    mount = MagicMock(spec=ModuleMountModel)
    mount.volume_ref = None
    mount.source_path = None
    mount.storage_class = storage_class
    mount.access_mode = access_mode
    mount.storage_size = storage_size
    mount.name = name
    mount.target_path = "/var/data"
    mount.type = "volume"
    return mount


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestHelmBuilderInit:
    def test_defaults(self):
        builder = HelmBuilder()
        assert builder.verbose is False
        assert not builder.has_errors()
        assert not builder.has_messages()

    def test_verbose(self):
        builder = HelmBuilder(verbose=True)
        assert builder.verbose is True


# ---------------------------------------------------------------------------
# before_build
# ---------------------------------------------------------------------------


class TestHelmBuilderBeforeBuild:
    def test_not_validated_returns_false(self, tmp_path):
        builder = HelmBuilder()
        svc = _mock_deployment_service(validated=False)
        assert builder.before_build(svc, tmp_path, tmp_path) is False
        assert any("not validated" in e for e in builder.get_errors())

    def test_no_workspace_service_returns_false(self, tmp_path):
        builder = HelmBuilder()
        svc = _mock_deployment_service(validated=True)
        svc.get_workspace_service.return_value = None
        assert builder.before_build(svc, tmp_path, tmp_path) is False
        assert any("Workspace" in e for e in builder.get_errors())

    def test_valid_service_returns_true(self, tmp_path):
        builder = HelmBuilder()
        svc = _mock_deployment_service(validated=True)
        assert builder.before_build(svc, tmp_path, tmp_path) is True
        assert not builder.has_errors()

    def test_verbose_emits_message(self, tmp_path):
        builder = HelmBuilder(verbose=True)
        svc = _mock_deployment_service(validated=True)
        builder.before_build(svc, tmp_path, tmp_path)
        assert any("validation passed" in m for m in builder.get_messages())


# ---------------------------------------------------------------------------
# after_build
# ---------------------------------------------------------------------------


class TestHelmBuilderAfterBuild:
    def test_always_true(self, tmp_path):
        builder = HelmBuilder()
        svc = _mock_deployment_service()
        assert builder.after_build(svc, tmp_path, tmp_path) is True

    def test_dry_run_verbose_emits_message(self, tmp_path):
        builder = HelmBuilder(verbose=True)
        svc = _mock_deployment_service()
        builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert any("DRY-RUN" in m for m in builder.get_messages())


# ---------------------------------------------------------------------------
# build — no-op paths
# ---------------------------------------------------------------------------


class TestHelmBuilderBuildNoOp:
    def test_no_namespaces_returns_true(self, tmp_path):
        builder = HelmBuilder()
        svc = _mock_deployment_service(namespace_services={})
        assert builder.build(svc, tmp_path, tmp_path) is True
        assert not builder.has_errors()

    def test_namespace_without_modules_skipped(self, tmp_path):
        builder = HelmBuilder()
        ns_svc = _mock_namespace_service(module_refs=[])
        dep_svc = _mock_deployment_service(
            build_path=tmp_path,
            namespace_services={"ns1": ns_svc},
        )
        assert builder.build(dep_svc, tmp_path, tmp_path) is True
        assert not builder.has_errors()
        assert not (tmp_path / "ns1").exists()

    def test_non_helm_module_skipped(self, tmp_path):
        """A module with type != helm should produce no output files."""
        non_helm_module = _make_helm_module("mymod", services=[_make_service("web")])
        non_helm_module.spec.type = ServiceDeployerType.COMPOSE  # override to compose

        mod_service = _make_mod_service(module=non_helm_module)
        mod_ref = _module_ref("mymod", "dummy.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        module_path = tmp_path / "dummy.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            result = builder.build(dep_svc, tmp_path, tmp_path)

        assert result is True
        assert not (tmp_path / "ns1" / "mymod" / "values.yaml").exists()
        assert not (tmp_path / "ns1" / "mymod" / "meta.yaml").exists()


# ---------------------------------------------------------------------------
# build — output generation
# ---------------------------------------------------------------------------


class TestHelmBuilderBuildOutput:
    """Verify generated values.yaml and meta.yaml contents."""

    def _run_build(
        self,
        tmp_path,
        services,
        namespace="testns",
        module_name="mymod",
        release_name=None,
        kubernetes_namespace=None,
        source=None,
        configuration=None,
    ):
        """Helper: build one namespace with one helm module; return (values_doc, meta_doc)."""
        helm_module = _make_helm_module(
            module_name,
            services=services,
            release_name=release_name,
            kubernetes_namespace=kubernetes_namespace,
            source=source,
            configuration=configuration,
        )
        mod_service = _make_mod_service(module=helm_module)
        mod_ref = _module_ref(module_name, "module.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={namespace: ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        assert ok is True, builder.get_errors()

        values_file = tmp_path / namespace / module_name / "values.yaml"
        meta_file = tmp_path / namespace / module_name / "meta.yaml"
        assert values_file.exists(), f"values.yaml not written: {list((tmp_path / namespace).rglob('*'))}"
        assert meta_file.exists(), "meta.yaml not written"

        return yaml.safe_load(values_file.read_text()), yaml.safe_load(meta_file.read_text())

    # --- values.yaml: service key naming ---

    def test_minimal_service(self, tmp_path):
        """A service with only an image produces a top-level key in values.yaml."""
        svc = _make_service("web", image="nginx:alpine")
        values, _ = self._run_build(tmp_path, [svc])
        assert "mymod-web" in values

    def test_service_key_no_prefix_when_equal(self, tmp_path):
        """When module name == service name the prefix is omitted."""
        svc = _make_service("mymod", image="mymod:latest")
        values, _ = self._run_build(tmp_path, [svc])
        assert "mymod" in values
        assert "mymod-mymod" not in values

    # --- values.yaml: env block ---

    def test_env_value_literal(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "TZ"
        env.value = "Europe/Brussels"
        env.var = None
        env.secret = None
        env.feature = None
        svc = _make_service("app", image="app:1", environment=[env])
        values, _ = self._run_build(tmp_path, [svc])
        assert values["mymod-app"]["env"]["TZ"] == "Europe/Brussels"

    def test_env_var_emits_substitution(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "APP_VERSION"
        env.value = None
        env.var = "APP_VERSION"
        env.secret = None
        env.feature = None
        svc = _make_service("app", image="app:1", environment=[env])
        values, _ = self._run_build(tmp_path, [svc])
        assert values["mymod-app"]["env"]["APP_VERSION"] == "${APP_VERSION}"

    def test_env_secret_emits_substitution(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "DB_PASSWORD"
        env.value = None
        env.var = None
        env.secret = "DB_PASSWORD"
        env.feature = None
        svc = _make_service("db", image="postgres:16", environment=[env])
        values, _ = self._run_build(tmp_path, [svc])
        assert values["mymod-db"]["env"]["DB_PASSWORD"] == "${DB_PASSWORD}"

    def test_env_feature_emits_substitution(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "ENABLE_METRICS"
        env.value = None
        env.var = None
        env.secret = None
        env.feature = "enable_metrics"
        svc = _make_service("app", image="app:1", environment=[env])
        values, _ = self._run_build(tmp_path, [svc])
        assert values["mymod-app"]["env"]["ENABLE_METRICS"] == "${enable_metrics}"

    # --- values.yaml: persistence block ---

    def test_pvc_persistence_block(self, tmp_path):
        """A mount with storage_class produces a persistence block."""
        mount = _make_pvc_mount(name="data", storage_class="fast-ssd", access_mode="ReadWriteOnce", storage_size="10Gi")
        svc = _make_service("db", image="postgres:16", mounts=[mount])
        values, _ = self._run_build(tmp_path, [svc])
        persistence = values["mymod-db"]["persistence"]
        assert "data" in persistence
        assert persistence["data"]["storageClass"] == "fast-ssd"
        assert persistence["data"]["accessMode"] == "ReadWriteOnce"
        assert persistence["data"]["size"] == "10Gi"

    def test_non_pvc_mount_excluded_from_persistence(self, tmp_path):
        """A mount without storage_class should NOT produce a persistence block."""
        mount = MagicMock(spec=ModuleMountModel)
        mount.volume_ref = "data"
        mount.source_path = None
        mount.storage_class = None
        mount.access_mode = None
        mount.storage_size = None
        mount.name = "data"
        mount.target_path = "/var/data"
        mount.type = "volume"
        svc = _make_service("app", image="app:1", mounts=[mount])
        values, _ = self._run_build(tmp_path, [svc])
        assert "persistence" not in values.get("mymod-app", {})

    # --- values.yaml: configuration merged verbatim ---

    def test_configuration_merged_verbatim(self, tmp_path):
        svc = _make_service("app", image="app:1", configuration={"replicaCount": 2, "resources": {"cpu": "500m"}})
        values, _ = self._run_build(tmp_path, [svc])
        assert values["mymod-app"]["replicaCount"] == 2
        assert values["mymod-app"]["resources"] == {"cpu": "500m"}

    # --- meta.yaml ---

    def test_meta_uses_release_name_when_set(self, tmp_path):
        svc = _make_service("web", image="nginx:alpine")
        _, meta = self._run_build(tmp_path, [svc], release_name="my-release")
        assert meta["releaseName"] == "my-release"

    def test_meta_falls_back_to_module_name_for_release(self, tmp_path):
        svc = _make_service("web", image="nginx:alpine")
        _, meta = self._run_build(tmp_path, [svc], module_name="mymod", release_name=None)
        assert meta["releaseName"] == "mymod"

    def test_meta_uses_kubernetes_namespace_when_set(self, tmp_path):
        svc = _make_service("web", image="nginx:alpine")
        _, meta = self._run_build(tmp_path, [svc], kubernetes_namespace="prod-ns")
        assert meta["namespace"] == "prod-ns"

    def test_meta_falls_back_to_namespace_name(self, tmp_path):
        svc = _make_service("web", image="nginx:alpine")
        _, meta = self._run_build(tmp_path, [svc], namespace="testns", kubernetes_namespace=None)
        assert meta["namespace"] == "testns"

    # --- meta.yaml: chart coordinates ---

    def test_meta_includes_chart_coordinates_for_registry_source(self, tmp_path):
        """When source is chart-based, meta.yaml includes chartName/Version/Repository."""
        src = MagicMock()
        src.chart_repository = "https://argoproj.github.io/argo-helm"
        src.chart_name = "argo-cd"
        src.chart_version = "7.8.0"
        src.source_path = None
        src.repository = None
        svc = _make_service("server", image="argoproj/argocd:v2")
        _, meta = self._run_build(tmp_path, [svc], release_name="argocd", source=src)
        assert meta["chartName"] == "argo-cd"
        assert meta["chartVersion"] == "7.8.0"
        assert meta["chartRepository"] == "https://argoproj.github.io/argo-helm"

    def test_meta_omits_chart_coordinates_for_git_source(self, tmp_path):
        """When source is git-based (no chart_repository), meta.yaml has no chart keys."""
        svc = _make_service("web", image="nginx:alpine")
        _, meta = self._run_build(tmp_path, [svc])
        assert "chartName" not in meta
        assert "chartVersion" not in meta
        assert "chartRepository" not in meta

    # --- values.yaml: module-level configuration ---

    def test_module_configuration_merged_into_values_with_services(self, tmp_path):
        """spec.configuration merges on top of service-generated values."""
        svc = _make_service("web", image="nginx:alpine")
        config = {"global": {"domain": "example.com"}, "replicaCount": 3}
        values, _ = self._run_build(tmp_path, [svc], configuration=config)
        assert values["global"] == {"domain": "example.com"}
        assert values["replicaCount"] == 3
        # Service entry still present
        assert "mymod-web" in values

    def test_module_configuration_creates_values_for_serviceless_module(self, tmp_path):
        """spec.configuration creates values.yaml even when no services exist."""
        config = {"server": {"insecure": True}, "redis": {"enabled": False}}
        values, _ = self._run_build(tmp_path, services=None, configuration=config)
        assert values["server"] == {"insecure": True}
        assert values["redis"] == {"enabled": False}

    def test_no_configuration_no_services_no_values_file(self, tmp_path):
        """Without services or configuration, values.yaml is not written."""
        helm_module = _make_helm_module("mod", services=None)
        mod_service = _make_mod_service(module=helm_module)
        mod_ref = _module_ref("mod", "module.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        assert ok is True
        assert not (tmp_path / "ns1" / "mod" / "values.yaml").exists()
        assert (tmp_path / "ns1" / "mod" / "meta.yaml").exists()

    # --- dry_run ---

    def test_dry_run_no_files_written(self, tmp_path):
        helm_module = _make_helm_module("mod", services=[_make_service("web", image="nginx:alpine")])
        mod_service = _make_mod_service(module=helm_module)
        mod_ref = _module_ref("mod", "module.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path, dry_run=True)

        assert ok is True
        assert not (tmp_path / "ns1" / "mod" / "values.yaml").exists()
        assert not (tmp_path / "ns1" / "mod" / "meta.yaml").exists()


# ---------------------------------------------------------------------------
# build — error paths
# ---------------------------------------------------------------------------


class TestHelmBuilderBuildErrors:
    def test_module_file_not_found(self, tmp_path):
        mod_ref = _module_ref("mod", "missing.yaml")
        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        builder = HelmBuilder()
        with patch("strata.builders.helm_builder.resolve_path") as mock_rp:
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

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=invalid_mod_svc),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        assert ok is False
        assert any("validation failed" in e for e in builder.get_errors())
