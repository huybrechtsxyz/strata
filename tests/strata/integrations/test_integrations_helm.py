#!/usr/bin/env python3
"""Tests for `HelmIntegration` (ADR-0021 Phase 6)."""

from pathlib import Path

import pytest
import yaml

from strata.integrations.helm import HelmIntegration
from strata.integrations.registry import get
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.common_models import ModuleReferenceModel, SourceModel
from strata.models.module_model import (
    ModuleMetaModel,
    ModuleModel,
    ModuleMountModel,
    ModuleServiceEnvironmentModel,
    ModuleServiceModel,
    ModuleSpecModel,
)
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
from strata.utils.transport import CommandResult


def _capture(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["env"] = env
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    return captured


def test_class_declares_its_contract():
    assert HelmIntegration.TYPE == "helm"
    assert HelmIntegration.CAPABILITIES == {"container"}
    assert HelmIntegration.TRANSPORTS == {"cli"}
    assert HelmIntegration.COMMAND == "helm"


def test_registered_in_the_registry():
    assert isinstance(get("helm"), HelmIntegration)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('version.BuildInfo{Version:"v3.14.0", GitCommit:"..."}', "3.14.0"),
        ("3.14.0", "3.14.0"),
        ("garbage", "garbage"),
    ],
)
def test_parse_version(raw, expected):
    assert HelmIntegration().parse_version(raw) == expected


def test_plan_argv(monkeypatch):
    captured = _capture(monkeypatch)
    HelmIntegration().plan(
        Path("/work/values.yaml"), release="app", namespace="apps", chart="repo/app", version="1.2.3"
    )
    assert captured["cwd"] == Path("/work")
    assert captured["args"] == [
        "helm",
        "upgrade",
        "--dry-run",
        "--install",
        "--namespace",
        "apps",
        "-f",
        str(Path("/work/values.yaml")),
        "app",
        "repo/app",
        "--version",
        "1.2.3",
    ]


def test_deploy_argv_defaults(monkeypatch):
    captured = _capture(monkeypatch)
    HelmIntegration().deploy(Path("/work/values.yaml"), release="app", namespace="apps", chart="repo/app")
    assert captured["cwd"] == Path("/work")
    assert captured["args"] == [
        "helm",
        "upgrade",
        "--install",
        "--create-namespace",
        "--wait",
        "--atomic",
        "--timeout",
        "5m",
        "--namespace",
        "apps",
        "-f",
        str(Path("/work/values.yaml")),
        "app",
        "repo/app",
    ]


def test_deploy_argv_flags_can_be_disabled(monkeypatch):
    captured = _capture(monkeypatch)
    HelmIntegration().deploy(
        Path("/work/values.yaml"),
        release="app",
        namespace="apps",
        chart="repo/app",
        create_namespace=False,
        wait=False,
        atomic=False,
        deploy_timeout="10m",
    )
    assert captured["args"] == [
        "helm",
        "upgrade",
        "--install",
        "--timeout",
        "10m",
        "--namespace",
        "apps",
        "-f",
        str(Path("/work/values.yaml")),
        "app",
        "repo/app",
    ]


def test_destroy_argv(monkeypatch):
    captured = _capture(monkeypatch)
    HelmIntegration().destroy(Path("/work/values.yaml"), release="app", namespace="apps")
    assert captured["args"] == ["helm", "uninstall", "--namespace", "apps", "app"]


def test_lint_argv(monkeypatch):
    captured = _capture(monkeypatch)
    HelmIntegration().lint(Path("/work/values.yaml"), chart="repo/app")
    assert captured["cwd"] == Path("/work")
    assert captured["args"] == ["helm", "lint", "-f", str(Path("/work/values.yaml")), "repo/app"]


def test_plan_reports_exactly_which_params_are_missing(monkeypatch):
    _capture(monkeypatch)
    with pytest.raises(Exception, match="namespace, chart are required"):
        HelmIntegration().plan(Path("/work/values.yaml"), release="app")


def test_repo_update_argv(monkeypatch):
    captured = _capture(monkeypatch)
    HelmIntegration().repo_update()
    assert captured["args"] == ["helm", "repo", "update"]


def test_get_manifest_argv(monkeypatch):
    captured = _capture(monkeypatch)
    HelmIntegration().get_manifest(release="app", namespace="apps")
    assert captured["args"] == ["helm", "get", "manifest", "--namespace", "apps", "app"]


def test_get_values_argv(monkeypatch):
    captured = _capture(monkeypatch)
    HelmIntegration().get_values(release="app", namespace="apps")
    assert captured["args"] == ["helm", "get", "values", "--namespace", "apps", "app"]


# ---------------------------------------------------------------------------
# prepare_namespace() — ADR-0022 D6/D7
# ---------------------------------------------------------------------------


def _namespace(name: str = "apps") -> NamespaceModel:
    return NamespaceModel(
        meta=NamespaceMetaModel(name=name),
        # NamespaceSpecModel requires lifecycle and/or modules (empty
        # namespaces are rejected) - this placeholder ref is never resolved
        # by these tests, which pass their own ResolvedModule list directly.
        spec=NamespaceSpecModel(
            default_labels={}, lifecycle=None, modules=[ModuleReferenceModel(name="placeholder", module="placeholder")]
        ),
    )


def _module(
    name: str = "authentik",
    *,
    source: SourceModel | None = None,
    services: list[ModuleServiceModel] | None = None,
    configuration: dict[str, object] | None = None,
    release_name: str | None = None,
    kubernetes_namespace: str | None = None,
) -> ModuleModel:
    return ModuleModel(
        meta=ModuleMetaModel(name=name),
        spec=ModuleSpecModel(
            source=source or SourceModel(source_path="charts/authentik"),
            type="helm",
            services=services,
            configuration=configuration,
            default_labels={},
            release_name=release_name,
            kubernetes_namespace=kubernetes_namespace,
        ),
    )


def _resolved_module(reference_name: str, module: ModuleModel, source_path: Path) -> ResolvedModule:
    return ResolvedModule(
        reference=ModuleReferenceModel(name=reference_name, module=module.meta.name),
        module=module,
        source_path=source_path,
    )


def test_prepare_namespace_writes_one_values_and_meta_file_per_module(tmp_path: Path):
    module = _module(
        services=[
            ModuleServiceModel(
                name="server",
                environment=[
                    ModuleServiceEnvironmentModel(key="DB_PASSWORD", value="${secret:DB_PASSWORD}"),
                    ModuleServiceEnvironmentModel(key="TZ", value="Europe/Brussels"),
                ],
            )
        ],
    )
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)
    resolved_module = _resolved_module("authentik", module, module_dir)

    HelmIntegration().prepare_namespace(
        _namespace(), [resolved_module], resolved=ValueResolution(deployment="app")
    )

    values = yaml.safe_load((module_dir / "values.yaml").read_text())
    assert values == {
        "authentik-server": {
            "env": {"DB_PASSWORD": "${secret:DB_PASSWORD}", "TZ": "Europe/Brussels"},
        }
    }
    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    assert meta == {"releaseName": "authentik", "namespace": "apps"}


def test_prepare_namespace_never_merges_across_modules(tmp_path: Path):
    """Unlike Compose (D6), Helm writes one pair of files per module - never merged."""
    module_a = _module("a", services=[ModuleServiceModel(name="a")])
    module_b = _module("b", services=[ModuleServiceModel(name="b")])
    dir_a, dir_b = tmp_path / "apps" / "a", tmp_path / "apps" / "b"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)

    HelmIntegration().prepare_namespace(
        _namespace(),
        [_resolved_module("a", module_a, dir_a), _resolved_module("b", module_b, dir_b)],
        resolved=ValueResolution(deployment="app"),
    )

    assert (dir_a / "values.yaml").exists()
    assert (dir_b / "values.yaml").exists()
    assert yaml.safe_load((dir_a / "values.yaml").read_text()) == {"a": {}}
    assert yaml.safe_load((dir_b / "values.yaml").read_text()) == {"b": {}}


def test_prepare_namespace_omits_values_file_when_nothing_to_render(tmp_path: Path):
    """No services, no module-level configuration - a local chart's own shipped
    values.yaml (already copied into module_dir) is left untouched, per v1's
    own 'omit when there is nothing to render' rule."""
    module = _module(services=None, configuration=None)
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)
    (module_dir / "values.yaml").write_text("shipped: true\n")

    HelmIntegration().prepare_namespace(
        _namespace(), [_resolved_module("authentik", module, module_dir)], resolved=ValueResolution(deployment="app")
    )

    assert yaml.safe_load((module_dir / "values.yaml").read_text()) == {"shipped": True}
    assert (module_dir / "meta.yaml").exists()


def test_prepare_namespace_merges_module_and_service_configuration(tmp_path: Path):
    module = _module(
        services=[ModuleServiceModel(name="server", configuration={"replicas": 2})],
        configuration={"ingress": {"enabled": True}},
    )
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)

    HelmIntegration().prepare_namespace(
        _namespace(), [_resolved_module("authentik", module, module_dir)], resolved=ValueResolution(deployment="app")
    )

    values = yaml.safe_load((module_dir / "values.yaml").read_text())
    assert values == {
        "authentik-server": {"replicas": 2},
        "ingress": {"enabled": True},
    }


def test_prepare_namespace_renders_persistence_only_for_storage_class_mounts(tmp_path: Path):
    module = _module(
        services=[
            ModuleServiceModel(
                name="server",
                mounts=[
                    ModuleMountModel(name="data", storage_class="fast-ssd", storage_size="10Gi"),
                    ModuleMountModel(name="cache", volume_ref="cache-volume"),
                ],
            )
        ],
    )
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)

    HelmIntegration().prepare_namespace(
        _namespace(), [_resolved_module("authentik", module, module_dir)], resolved=ValueResolution(deployment="app")
    )

    values = yaml.safe_load((module_dir / "values.yaml").read_text())
    assert values["authentik-server"]["persistence"] == {
        "data": {"storageClass": "fast-ssd", "accessMode": "ReadWriteOnce", "size": "10Gi"}
    }


def test_prepare_namespace_release_name_defaults_to_the_reference_name(tmp_path: Path):
    """Not module.meta.name (v1's real default) - the same Module document can
    be attached twice under different reference names, and two live Helm
    releases can never share a name."""
    module = _module("authentik")
    module_dir = tmp_path / "apps" / "authentik-2"
    module_dir.mkdir(parents=True)

    HelmIntegration().prepare_namespace(
        _namespace(),
        [_resolved_module("authentik-2", module, module_dir)],
        resolved=ValueResolution(deployment="app"),
    )

    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    assert meta["releaseName"] == "authentik-2"


def test_prepare_namespace_release_name_and_namespace_can_be_overridden(tmp_path: Path):
    module = _module(release_name="authentik-prod", kubernetes_namespace="prod-apps")
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)

    HelmIntegration().prepare_namespace(
        _namespace(), [_resolved_module("authentik", module, module_dir)], resolved=ValueResolution(deployment="app")
    )

    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    assert meta == {"releaseName": "authentik-prod", "namespace": "prod-apps"}


def test_prepare_namespace_writes_chart_coordinates_for_registry_charts(tmp_path: Path):
    module = _module(
        source=SourceModel(remote="goauthentik", chart_name="authentik", chart_version="2024.12.0"),
    )
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)

    HelmIntegration().prepare_namespace(
        _namespace(), [_resolved_module("authentik", module, module_dir)], resolved=ValueResolution(deployment="app")
    )

    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    assert meta["chartName"] == "authentik"
    assert meta["chartVersion"] == "2024.12.0"
    assert meta["chartRemote"] == "goauthentik"


def test_prepare_namespace_omits_chart_coordinates_for_local_charts(tmp_path: Path):
    module = _module(source=SourceModel(source_path="charts/authentik"))
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)

    HelmIntegration().prepare_namespace(
        _namespace(), [_resolved_module("authentik", module, module_dir)], resolved=ValueResolution(deployment="app")
    )

    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    assert "chartName" not in meta
