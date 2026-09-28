#!/usr/bin/env python3
"""Tests for `HelmIntegration` (ADR-0021 Phase 6)."""

from pathlib import Path

import pytest
import yaml

from strata.integrations.helm import (
    HelmIntegration,
    _resolve_chart,
    _sanitize_repo_name,
    _set_string_args,
    resolve_module_values,
)
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
from strata.models.solution_model import RemoteType, SolutionRemoteModel
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
    # Helm has no env-var substitution mechanism at all for values.yaml —
    # confirmed in this class's own module docstring — so it inherits the
    # base `None`, never declares its own prefix.
    assert HelmIntegration.ENV_VAR_PREFIX is None


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

    HelmIntegration().prepare_namespace(_namespace(), [resolved_module], resolved=ValueResolution(deployment="app"))

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


# ---------------------------------------------------------------------------
# resolve_module_values() — docs/_gap_v1.md gap #9, Full Solution Phase 4.
# Deliberately pure (no disk I/O) — mirrors `_render_values()`'s own
# testable-without-touching-disk convention. Wiring a real `helm upgrade`
# invocation per module is gap #13, not this function's job.
# ---------------------------------------------------------------------------


def test_resolve_module_values_resolves_non_secret_leaves():
    module = _module(
        services=[
            ModuleServiceModel(name="server", environment=[ModuleServiceEnvironmentModel(key="TZ", value="${var:tz}")])
        ]
    )
    resolved, secrets = resolve_module_values(module, {"tz": "Europe/Brussels"})
    assert resolved == {"authentik-server": {"env": {"TZ": "Europe/Brussels"}}}
    assert secrets == {}


def test_resolve_module_values_leaves_a_secret_shaped_leaf_unresolved_and_tracks_it():
    module = _module(
        services=[
            ModuleServiceModel(
                name="server",
                environment=[ModuleServiceEnvironmentModel(key="DB_PASSWORD", value="${secret:db_password}")],
            )
        ]
    )
    resolved, secrets = resolve_module_values(module, {"db_password": "hunter2"})
    assert resolved == {"authentik-server": {"env": {"DB_PASSWORD": "${secret:db_password}"}}}
    assert secrets == {"authentik-server.env.DB_PASSWORD": "hunter2"}


def test_resolve_module_values_matches_the_real_immich_style_configuration_path():
    """Real gap #8 shape: a raw `configuration` passthrough (not `services`),
    e.g. `controllers.main.containers.main.env.DB_PASSWORD`."""
    module = _module(
        services=None,
        configuration={
            "controllers": {"main": {"containers": {"main": {"env": {"DB_PASSWORD": "${secret:db_password}"}}}}}
        },
    )
    resolved, secrets = resolve_module_values(module, {"db_password": "hunter2"})
    assert resolved["controllers"]["main"]["containers"]["main"]["env"]["DB_PASSWORD"] == "${secret:db_password}"
    assert secrets == {"controllers.main.containers.main.env.DB_PASSWORD": "hunter2"}


def test_resolve_module_values_does_not_touch_disk():
    """Pure — no ResolvedModule/source_path needed at all, unlike prepare_namespace()."""
    module = _module(services=[ModuleServiceModel(name="server")])
    resolved, secrets = resolve_module_values(module, {})
    assert resolved == {"authentik-server": {}}
    assert secrets == {}


# ---------------------------------------------------------------------------
# deploy_namespace() — docs/_gap_v1.md gap #13
# ---------------------------------------------------------------------------


def _remote(name: str, remote_type: RemoteType, url: str) -> SolutionRemoteModel:
    reference = "main" if remote_type in (RemoteType.GIT, RemoteType.OCI) else None
    return SolutionRemoteModel(name=name, type=remote_type, url=url, reference=reference)


def test_set_string_args_escapes_special_characters():
    args = _set_string_args([("authentik-server.env.DB_PASSWORD", "a,b=c.d{e}[f]\\g")])
    assert args == ["--set-string", "authentik-server.env.DB_PASSWORD=a\\,b\\=c\\.d\\{e\\}\\[f\\]\\\\g"]


def test_set_string_args_empty_when_no_secrets():
    assert _set_string_args(None) == []
    assert _set_string_args([]) == []


def test_sanitize_repo_name_strips_scheme_and_truncates():
    assert _sanitize_repo_name("https://charts.example.com/goauthentik") == "charts-example-com-g"
    assert len(_sanitize_repo_name("https://charts.example.com/goauthentik")) == 20


def test_resolve_chart_oci_remote_needs_no_repo_add(monkeypatch):
    captured = _capture(monkeypatch)
    remotes = {"goauthentik": _remote("goauthentik", RemoteType.OCI, "oci://registry.example.com/charts")}
    chart, error = _resolve_chart(
        HelmIntegration(), {"chartName": "authentik", "chartRemote": "goauthentik"}, remotes, env=None
    )
    assert error is None
    assert chart == "oci://registry.example.com/charts/authentik"
    assert "args" not in captured


def test_resolve_chart_helm_remote_adds_a_repo(monkeypatch):
    captured = _capture(monkeypatch)
    remotes = {"goauthentik": _remote("goauthentik", RemoteType.HELM, "https://charts.goauthentik.io")}
    chart, error = _resolve_chart(
        HelmIntegration(), {"chartName": "authentik", "chartRemote": "goauthentik"}, remotes, env=None
    )
    assert error is None
    assert chart == "charts-goauthentik-i/authentik"
    assert captured["args"] == ["helm", "repo", "add", "charts-goauthentik-i", "https://charts.goauthentik.io"]


def test_resolve_chart_unknown_remote_reports_an_error():
    chart, error = _resolve_chart(HelmIntegration(), {"chartName": "authentik", "chartRemote": "missing"}, {}, env=None)
    assert chart is None
    assert error is not None
    assert "not declared" in error


def test_resolve_chart_missing_chart_remote_reports_an_error():
    chart, error = _resolve_chart(HelmIntegration(), {"chartName": "authentik"}, {}, env=None)
    assert chart is None
    assert error is not None
    assert "no 'chartRemote'" in error


def test_resolve_chart_git_or_local_typed_remote_is_rejected():
    remotes = {"infra": _remote("infra", RemoteType.GIT, "https://github.com/org/infra.git")}
    chart, error = _resolve_chart(
        HelmIntegration(), {"chartName": "authentik", "chartRemote": "infra"}, remotes, env=None
    )
    assert chart is None
    assert error is not None
    assert "not a valid Helm chart source" in error


def test_deploy_namespace_local_chart_uses_source_path_directly(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    module = _module(
        services=[
            ModuleServiceModel(name="server", environment=[ModuleServiceEnvironmentModel(key="TZ", value="${var:tz}")])
        ]
    )
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)
    (module_dir / "meta.yaml").write_text(yaml.safe_dump({"releaseName": "authentik", "namespace": "apps"}))
    (module_dir / "values.yaml").write_text(yaml.safe_dump({"authentik-server": {"env": {"TZ": "${var:tz}"}}}))
    resolved_module = _resolved_module("authentik", module, module_dir)

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(), [resolved_module], tokens={"tz": "Europe/Brussels"}, dry_run=False
    )

    assert diagnostics.ok
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
        str(module_dir / "values.yaml"),
        "authentik",
        str(module_dir),
    ]
    assert yaml.safe_load((module_dir / "values.yaml").read_text()) == {
        "authentik-server": {"env": {"TZ": "Europe/Brussels"}}
    }


def test_deploy_namespace_registry_chart_resolves_chart_ref_and_delivers_secrets_via_set_string(
    monkeypatch, tmp_path: Path
):
    captured = _capture(monkeypatch)
    module = _module(
        source=SourceModel(remote="goauthentik", chart_name="authentik", chart_version="2024.12.0"),
        services=[
            ModuleServiceModel(
                name="server",
                environment=[ModuleServiceEnvironmentModel(key="DB_PASSWORD", value="${secret:db_password}")],
            )
        ],
    )
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)
    (module_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            {
                "releaseName": "authentik",
                "namespace": "apps",
                "chartName": "authentik",
                "chartVersion": "2024.12.0",
                "chartRemote": "goauthentik",
            }
        )
    )
    (module_dir / "values.yaml").write_text(
        yaml.safe_dump({"authentik-server": {"env": {"DB_PASSWORD": "${secret:db_password}"}}})
    )
    resolved_module = _resolved_module("authentik", module, module_dir)
    remotes = {"goauthentik": _remote("goauthentik", RemoteType.OCI, "oci://registry.example.com/charts")}

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(), [resolved_module], tokens={"db_password": "hunter2"}, dry_run=False, remotes=remotes
    )

    assert diagnostics.ok
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
        str(module_dir / "values.yaml"),
        "--set-string",
        "authentik-server.env.DB_PASSWORD=hunter2",
        "authentik",
        "oci://registry.example.com/charts/authentik",
    ]
    # Secret-shaped leaf is left as a literal token on disk, never written resolved.
    assert yaml.safe_load((module_dir / "values.yaml").read_text()) == {
        "authentik-server": {"env": {"DB_PASSWORD": "${secret:db_password}"}}
    }


def test_deploy_namespace_missing_meta_file_reports_a_diagnostic_and_skips(monkeypatch, tmp_path: Path):
    _capture(monkeypatch)
    module = _module()
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)
    resolved_module = _resolved_module("authentik", module, module_dir)

    diagnostics = HelmIntegration().deploy_namespace(_namespace(), [resolved_module], tokens={}, dry_run=False)

    assert not diagnostics.ok
    assert "meta.yaml not found" in diagnostics.errors[0].message


def test_deploy_namespace_unresolvable_chart_remote_reports_a_diagnostic_and_skips(monkeypatch, tmp_path: Path):
    _capture(monkeypatch)
    module = _module(source=SourceModel(remote="missing", chart_name="authentik"))
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)
    (module_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            {"releaseName": "authentik", "namespace": "apps", "chartName": "authentik", "chartRemote": "missing"}
        )
    )
    resolved_module = _resolved_module("authentik", module, module_dir)

    diagnostics = HelmIntegration().deploy_namespace(_namespace(), [resolved_module], tokens={}, dry_run=False)

    assert not diagnostics.ok
    assert "not declared" in diagnostics.errors[0].message


def test_deploy_namespace_dry_run_never_touches_disk_or_runs_a_command(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    module = _module(
        services=[
            ModuleServiceModel(name="server", environment=[ModuleServiceEnvironmentModel(key="TZ", value="${var:tz}")])
        ]
    )
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)
    (module_dir / "meta.yaml").write_text(yaml.safe_dump({"releaseName": "authentik", "namespace": "apps"}))
    values_before = yaml.safe_dump({"authentik-server": {"env": {"TZ": "${var:tz}"}}})
    (module_dir / "values.yaml").write_text(values_before)
    resolved_module = _resolved_module("authentik", module, module_dir)

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(), [resolved_module], tokens={"tz": "Europe/Brussels"}, dry_run=True
    )

    assert diagnostics.ok
    assert "args" not in captured
    assert (module_dir / "values.yaml").read_text() == values_before
