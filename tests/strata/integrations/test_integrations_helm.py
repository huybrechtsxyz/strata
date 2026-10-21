#!/usr/bin/env python3
"""Tests for `HelmIntegration` (ADR-0021 Phase 6)."""

import re
import shutil
from pathlib import Path

import pytest
import yaml

from strata.integrations.errors import CheckPhaseUnsupportedError, IntegrationError, RegistryAuthError
from strata.integrations.helm import (
    HelmIntegration,
    RegistryCredentials,
    _repo_alias,
    _resolve_chart,
    _set_string_args,
    check_configuration_keys,
    credentials_from_authentication,
    read_chart_values,
    resolve_module_values,
)
from strata.integrations.registry import get
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.auth_models import (
    AuthenticationModel,
    CLIAuthenticationModel,
    SSHKeyAuthenticationModel,
    TokenAuthenticationModel,
)
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
from strata.utils.diagnostics import Diagnostics
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


def test_prepare_namespace_writes_inline_chart_repository_instead_of_chart_remote(tmp_path: Path):
    module = _module(
        source=SourceModel(chart_repository="https://charts.goauthentik.io", chart_name="authentik"),
    )
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)

    HelmIntegration().prepare_namespace(
        _namespace(), [_resolved_module("authentik", module, module_dir)], resolved=ValueResolution(deployment="app")
    )

    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    assert meta["chartName"] == "authentik"
    assert meta["chartRepository"] == "https://charts.goauthentik.io"
    assert "chartRemote" not in meta


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
# resolve_module_values() — docs/design/gap_fit_v1.md gap #9, Full Solution Phase 4.
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
# deploy_namespace() — docs/design/gap_fit_v1.md gap #13
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


def _token_auth(*, token: str = "PROGET_API_KEY", username: str | None = "api") -> AuthenticationModel:
    return AuthenticationModel(method="token", token=TokenAuthenticationModel(token=token, username=username))


def test_registry_credentials_reads_the_username_and_the_secret_the_token_key_names():
    credentials = credentials_from_authentication(
        _token_auth(), {"PROGET_API_KEY": "s3cret-key-123"}, owner="remote 'p'"
    )

    assert credentials == RegistryCredentials(username="api", password="s3cret-key-123")


def test_registry_credentials_is_none_for_no_authentication_or_ambient_cli():
    cli = AuthenticationModel(method="cli", cli=CLIAuthenticationModel())

    assert credentials_from_authentication(None, {}, owner="remote 'p'") is None
    assert credentials_from_authentication(cli, {}, owner="remote 'p'") is None


@pytest.mark.parametrize(
    ("auth", "values", "expected"),
    [
        (_token_auth(username=None), {"PROGET_API_KEY": "s3cret-key-123"}, "needs 'username'"),
        (_token_auth(), {}, "no value for 'PROGET_API_KEY'"),
        (_token_auth(), {"PROGET_API_KEY": ""}, "no value for 'PROGET_API_KEY'"),
        (
            AuthenticationModel(method="ssh_key", ssh_key=SSHKeyAuthenticationModel(private_key="K")),
            {"K": "pem"},
            "method 'ssh_key' cannot authenticate a chart registry (supported: token, cli)",
        ),
    ],
)
def test_registry_credentials_rejects_what_cannot_authenticate_a_chart_registry(auth, values, expected):
    with pytest.raises(IntegrationError, match=re.escape(expected)) as caught:
        credentials_from_authentication(auth, values, owner="remote 'p' (integration 'proget')")

    assert str(caught.value).startswith("remote 'p' (integration 'proget'): ")
    assert "s3cret-key-123" not in str(caught.value)


def test_registry_credentials_repr_and_str_hide_the_password():
    credentials = RegistryCredentials(username="api", password="s3cret-key-123")

    assert "s3cret-key-123" not in repr(credentials) + str(credentials)
    assert "api" in repr(credentials)


def test_repo_alias_is_a_readable_prefix_plus_a_url_hash():
    alias = _repo_alias("https://charts.example.com/goauthentik")
    assert re.fullmatch(r"charts-examp-[0-9a-f]{8}", alias)
    assert alias == _repo_alias("https://charts.example.com/goauthentik/")  # trailing slash is the same repo


def test_repo_alias_differs_for_urls_sharing_a_long_prefix():
    first = _repo_alias("https://charts.example.com/team-a")
    second = _repo_alias("https://charts.example.com/team-b")
    assert first != second
    assert first.split("-")[:-1] == second.split("-")[:-1]


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
    alias = _repo_alias("https://charts.goauthentik.io")
    assert chart == f"{alias}/authentik"
    assert captured["args"] == ["helm", "repo", "add", alias, "https://charts.goauthentik.io", "--force-update"]


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


# ---------------------------------------------------------------------------
# _resolve_chart()/inline chart_repository — docs/design/gap_fit_v1.md gap #2 Option 1.
# ---------------------------------------------------------------------------


def test_resolve_chart_inline_oci_chart_repository_needs_no_repo_add(monkeypatch):
    captured = _capture(monkeypatch)
    chart, error = _resolve_chart(
        HelmIntegration(),
        {"chartName": "authentik", "chartRepository": "oci://ghcr.io/org/charts"},
        {},
        env=None,
    )
    assert error is None
    assert chart == "oci://ghcr.io/org/charts/authentik"
    assert "args" not in captured


def test_resolve_chart_inline_http_chart_repository_adds_a_repo(monkeypatch):
    captured = _capture(monkeypatch)
    chart, error = _resolve_chart(
        HelmIntegration(),
        {"chartName": "authentik", "chartRepository": "https://charts.goauthentik.io"},
        {},
        env=None,
    )
    assert error is None
    alias = _repo_alias("https://charts.goauthentik.io")
    assert chart == f"{alias}/authentik"
    assert captured["args"] == ["helm", "repo", "add", alias, "https://charts.goauthentik.io", "--force-update"]


def test_resolve_chart_failed_repo_refresh_is_a_warning_and_still_resolves(monkeypatch):
    import strata.integrations.base as base_module

    def _failing(args, **kwargs):
        return CommandResult(returncode=1, stdout="", stderr="connection refused")

    monkeypatch.setattr(base_module, "run_command", _failing)
    diagnostics = Diagnostics()

    chart, error = _resolve_chart(
        HelmIntegration(),
        {"chartName": "gatus", "chartRepository": "https://twin.github.io/helm-charts"},
        {},
        env=None,
        diagnostics=diagnostics,
        location="gatus",
    )

    assert error is None
    assert chart == f"{_repo_alias('https://twin.github.io/helm-charts')}/gatus"
    assert diagnostics.ok
    [warning] = diagnostics.items
    assert warning.code == "helm_repo_refresh_failed"
    assert warning.location == "gatus"
    assert "connection refused" in warning.message


def test_resolve_chart_oci_repository_records_no_warning(monkeypatch):
    _capture(monkeypatch)
    diagnostics = Diagnostics()

    _resolve_chart(
        HelmIntegration(),
        {"chartName": "immich", "chartRepository": "oci://ghcr.io/immich-app/immich-charts"},
        {},
        env=None,
        diagnostics=diagnostics,
    )

    assert diagnostics.items == []


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
        "--version",
        "2024.12.0",
    ]
    # Secret-shaped leaf is left as a literal token on disk, never written resolved.
    assert yaml.safe_load((module_dir / "values.yaml").read_text()) == {
        "authentik-server": {"env": {"DB_PASSWORD": "${secret:db_password}"}}
    }


def test_check_configuration_keys_suggests_the_closest_declared_key():
    chart = {"replicaCount": 1, "image": {"repository": "x", "tag": "y"}}

    findings = check_configuration_keys({"replicaCnt": 2, "image": {"tagg": "1"}, "image2": 1}, chart)

    assert findings == [
        "configuration key 'replicaCnt' is not in the chart's values.yaml (did you mean 'replicaCount'?)",
        "configuration key 'image.tagg' is not in the chart's values.yaml (did you mean 'image.tag'?)",
        "configuration key 'image2' is not in the chart's values.yaml (did you mean 'image'?)",
    ]


def test_check_configuration_keys_omits_the_suggestion_when_nothing_is_close():
    assert check_configuration_keys({"zzzz": 1}, {"replicaCount": 1}) == [
        "configuration key 'zzzz' is not in the chart's values.yaml"
    ]


def test_check_configuration_keys_accepts_declared_keys_and_ignores_deeper_levels():
    chart = {"image": {"tag": "y", "pull": {"policy": "Always"}}}

    assert check_configuration_keys({"image": {"tag": "1", "pull": {"anything": 1}}}, chart) == []


def test_check_configuration_keys_treats_an_empty_default_mapping_as_free_form():
    assert check_configuration_keys({"env": {"TZ": "UTC"}}, {"env": {}}) == []


def test_check_configuration_keys_has_nothing_to_check_against_without_chart_defaults():
    assert check_configuration_keys({"anything": 1}, {}) == []


def test_read_chart_values_returns_an_empty_dict_when_missing_unparseable_or_not_a_mapping(tmp_path: Path):
    assert read_chart_values(tmp_path) == {}
    (tmp_path / "values.yaml").write_text("a: [unclosed", encoding="utf-8")
    assert read_chart_values(tmp_path) == {}
    (tmp_path / "values.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    assert read_chart_values(tmp_path) == {}
    (tmp_path / "values.yaml").write_text("replicaCount: 1\n", encoding="utf-8")
    assert read_chart_values(tmp_path) == {"replicaCount": 1}


def test_deploy_namespace_failed_repo_refresh_warns_and_still_deploys(monkeypatch, tmp_path: Path):
    import strata.integrations.base as base_module

    calls: list[list[str]] = []

    def _fake(args, **kwargs):
        calls.append(list(args))
        if args[1] == "repo":
            return CommandResult(returncode=1, stdout="", stderr="no route to host")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(base_module, "run_command", _fake)
    module = _module(source=SourceModel(chart_repository="https://twin.github.io/helm-charts", chart_name="gatus"))
    module_dir = tmp_path / "apps" / "gatus"
    module_dir.mkdir(parents=True)
    (module_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            {
                "releaseName": "gatus",
                "namespace": "apps",
                "chartName": "gatus",
                "chartVersion": "1.0.0",
                "chartRepository": "https://twin.github.io/helm-charts",
            }
        )
    )
    (module_dir / "values.yaml").write_text(yaml.safe_dump({"replicas": 1}))

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(), [_resolved_module("gatus", module, module_dir)], tokens={}, dry_run=False
    )

    assert diagnostics.ok
    assert [item.code for item in diagnostics.items] == ["helm_repo_refresh_failed"]
    assert [call[1] for call in calls] == ["repo", "upgrade"]


def _check_fixture(tmp_path: Path, *, name: str = "authentik", meta: dict | None = None, **module_kwargs):
    module = _module(name, **module_kwargs)
    module_dir = tmp_path / "apps" / name
    module_dir.mkdir(parents=True)
    (module_dir / "meta.yaml").write_text(yaml.safe_dump(meta or {"releaseName": name, "namespace": "apps"}))
    (module_dir / "values.yaml").write_text("built: values\n")
    return _resolved_module(name, module, module_dir), module_dir


def _record_lint(monkeypatch, *, returncode_for=lambda args: 0, stdout: str = "ok"):
    """Stub `run_command`, capturing each lint's argv and the overlay file's text at call time (it is temporary)."""
    calls: list[dict] = []

    def _fake(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        overlay = Path(args[3])
        calls.append({"args": list(args), "overlay": overlay.read_text(), "overlay_path": overlay, "timeout": timeout})
        return CommandResult(returncode=returncode_for(args), stdout=stdout, stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake)
    return calls


def test_check_namespace_lints_the_built_chart_with_a_resolved_overlay_and_no_secret(monkeypatch, tmp_path: Path):
    calls = _record_lint(monkeypatch)
    item, module_dir = _check_fixture(
        tmp_path,
        services=[
            ModuleServiceModel(
                name="server",
                environment=[
                    ModuleServiceEnvironmentModel(key="TZ", value="${var:tz}"),
                    ModuleServiceEnvironmentModel(key="DB_PASSWORD", value="${secret:db_password}"),
                ],
            )
        ],
    )

    diagnostics = HelmIntegration().check_namespace(
        _namespace(), [item], tokens={"tz": "Europe/Brussels", "db_password": "hunter2"}
    )

    assert diagnostics.ok
    [call] = calls
    assert call["args"][:3] == ["helm", "lint", "-f"]
    assert call["args"][-1] == str(module_dir)
    assert yaml.safe_load(call["overlay"]) == {
        "authentik-server": {"env": {"TZ": "Europe/Brussels", "DB_PASSWORD": "${secret:db_password}"}}
    }
    assert "hunter2" not in call["overlay"] + " ".join(call["args"])
    assert module_dir not in call["overlay_path"].parents
    assert not call["overlay_path"].exists()
    assert (module_dir / "values.yaml").read_text() == "built: values\n"


def test_check_namespace_uses_a_placeholder_for_an_output_that_does_not_exist_yet(monkeypatch, tmp_path: Path):
    calls = _record_lint(monkeypatch)
    item, _ = _check_fixture(
        tmp_path,
        services=[
            ModuleServiceModel(
                name="server", environment=[ModuleServiceEnvironmentModel(key="IP", value="${output:infra.ip}")]
            )
        ],
    )

    diagnostics = HelmIntegration().check_namespace(_namespace(), [item], tokens={})

    assert diagnostics.ok
    assert yaml.safe_load(calls[0]["overlay"]) == {"authentik-server": {"env": {"IP": "lint"}}}


def test_check_namespace_skips_registry_charts(monkeypatch, tmp_path: Path):
    calls = _record_lint(monkeypatch)
    item, _ = _check_fixture(
        tmp_path,
        meta={"releaseName": "gatus", "namespace": "apps", "chartName": "gatus", "chartRepository": "oci://x/y"},
    )

    assert HelmIntegration().check_namespace(_namespace(), [item], tokens={}).items == []
    assert calls == []


def test_check_namespace_reports_only_error_findings_and_keeps_checking_other_modules(monkeypatch, tmp_path: Path):
    calls = _record_lint(
        monkeypatch,
        returncode_for=lambda args: 1 if args[-1].endswith("first") else 0,
        stdout="==> Linting\n[INFO] icon is recommended\n[ERROR] templates/: nil pointer\n",
    )
    first, _ = _check_fixture(tmp_path, name="first")
    second, _ = _check_fixture(tmp_path, name="second")

    diagnostics = HelmIntegration().check_namespace(_namespace(), [first, second], tokens={})

    assert len(calls) == 2
    [error] = diagnostics.items
    assert error.code == "helm_lint_failed"
    assert error.location == "first"
    assert error.message.endswith("helm lint failed \u2014 [ERROR] templates/: nil pointer")


def test_check_namespace_reports_a_missing_meta_file_and_an_unresolvable_token(monkeypatch, tmp_path: Path):
    calls = _record_lint(monkeypatch)
    no_meta, module_dir = _check_fixture(tmp_path, name="nometa")
    (module_dir / "meta.yaml").unlink()
    unresolved, _ = _check_fixture(
        tmp_path,
        name="unresolved",
        services=[
            ModuleServiceModel(name="server", environment=[ModuleServiceEnvironmentModel(key="TZ", value="${var:tz}")])
        ],
    )

    diagnostics = HelmIntegration().check_namespace(_namespace(), [no_meta, unresolved], tokens={})

    assert [item.location for item in diagnostics.items] == ["nometa", "unresolved"]
    assert "meta.yaml not found" in diagnostics.items[0].message
    assert calls == []


def test_check_namespace_reports_lints_own_message_when_there_is_no_error_line(monkeypatch, tmp_path: Path):
    """A missing Chart.yaml prints `Error unable to check ...` on stdout, with no `[ERROR]` marker."""
    _record_lint(
        monkeypatch,
        returncode_for=lambda args: 1,
        stdout="==> Linting build/apps/x\n[INFO] Chart.yaml: icon is recommended\nError unable to check Chart.yaml file\n",
    )
    item, _ = _check_fixture(tmp_path)

    [error] = HelmIntegration().check_namespace(_namespace(), [item], tokens={}).items

    assert error.message.endswith("helm lint failed \u2014 Error unable to check Chart.yaml file")


def test_deploy_namespace_a_remote_named_none_does_not_credential_a_module_without_a_remote(monkeypatch, tmp_path):
    calls = _record_helm(monkeypatch)
    item, _ = _check_fixture(tmp_path, name="local")

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(), [item], tokens={}, dry_run=False, registry_credentials={"None": _CREDENTIALS}
    )

    assert diagnostics.ok, diagnostics.messages()
    assert [call["args"][1] for call in calls] == ["upgrade"]
    assert calls[0]["env"] is None


def test_an_integration_without_a_check_phase_raises_the_specific_unsupported_error():
    from strata.integrations.compose import ComposeIntegration

    with pytest.raises(CheckPhaseUnsupportedError):
        ComposeIntegration().check_namespace(_namespace(), [], tokens={})


def test_check_namespace_passes_the_check_timeout_to_lint(monkeypatch, tmp_path: Path):
    calls = _record_lint(monkeypatch)
    item, _ = _check_fixture(tmp_path)

    HelmIntegration().check_namespace(_namespace(), [item], tokens={}, timeout=7)

    assert calls[0]["timeout"] == 7


@pytest.mark.skipif(not shutil.which("helm"), reason="helm is not installed")
def test_check_namespace_against_real_helm_catches_a_template_error_the_overlay_can_fix(tmp_path: Path):
    """The overlay is what makes the chart renderable: the built values.yaml alone leaves `.Values.cfg` nil."""
    module = _module("real", configuration={"cfg": {"name": "x"}})
    module_dir = tmp_path / "apps" / "real"
    (module_dir / "templates").mkdir(parents=True)
    (module_dir / "Chart.yaml").write_text("apiVersion: v2\nname: real\nversion: 0.1.0\n")
    (module_dir / "templates" / "cm.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: real\ndata:\n  name: {{ .Values.cfg.name | quote }}\n"
    )
    (module_dir / "values.yaml").write_text("{}\n")
    (module_dir / "meta.yaml").write_text(yaml.safe_dump({"releaseName": "real", "namespace": "apps"}))
    item = _resolved_module("real", module, module_dir)

    assert HelmIntegration().check_namespace(_namespace(), [item], tokens={}).ok

    broken = _resolved_module("real", _module("real"), module_dir)
    diagnostics = HelmIntegration().check_namespace(_namespace(), [broken], tokens={})
    assert [item.code for item in diagnostics.items] == ["helm_lint_failed"]
    assert "nil pointer" in diagnostics.items[0].message


_CREDENTIALS = RegistryCredentials(username="api", password="s3cret-key-123")
_HELM_STATE_VARS = {"HELM_REPOSITORY_CONFIG", "HELM_REPOSITORY_CACHE", "HELM_REGISTRY_CONFIG", "HELM_CACHE_HOME"}


def _record_helm(monkeypatch, *, failing=lambda args: None):
    """Stub `run_command`, recording argv, env and stdin text per call; `failing(args)` returns stderr to fail a call."""
    calls: list[dict] = []

    def _fake(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append({"args": list(args), "env": dict(env) if env is not None else None, "input": input})
        stderr = failing(list(args))
        return CommandResult(returncode=1 if stderr else 0, stdout="", stderr=stderr or "")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake)
    return calls


_PROGET_META = {
    "releaseName": "gatus",
    "namespace": "apps",
    "chartName": "gatus",
    "chartVersion": "1.0.0",
    "chartRemote": "proget-charts",
}


def _proget_module(tmp_path: Path, name: str = "gatus"):
    return _check_fixture(
        tmp_path,
        name=name,
        meta={**_PROGET_META, "releaseName": name},
        source=SourceModel(remote="proget-charts", chart_name="gatus", chart_version="1.0.0"),
    )


def test_resolve_chart_http_remote_with_credentials_authenticates_the_repo_add_on_stdin(monkeypatch):
    calls = _record_helm(monkeypatch)
    url = "https://proget.example.com/helm/charts"
    remotes = {"proget-charts": _remote("proget-charts", RemoteType.HELM, url)}

    chart, error = _resolve_chart(
        HelmIntegration(),
        {"chartName": "gatus", "chartRemote": "proget-charts"},
        remotes,
        env=None,
        credentials=_CREDENTIALS,
    )

    alias = _repo_alias(url)
    assert (chart, error) == (f"{alias}/gatus", None)
    [call] = calls
    assert call["args"] == [
        "helm", "repo", "add", alias, url, "--force-update", "--username", "api", "--password-stdin"
    ]  # fmt: skip
    assert call["input"] == "s3cret-key-123"
    assert "s3cret-key-123" not in " ".join(call["args"])


def test_resolve_chart_oci_remote_with_credentials_logs_in_to_the_registry_host_on_stdin(monkeypatch):
    calls = _record_helm(monkeypatch)
    remotes = {"proget-charts": _remote("proget-charts", RemoteType.OCI, "oci://proget.example.com:8443/feeds/charts")}

    chart, error = _resolve_chart(
        HelmIntegration(),
        {"chartName": "gatus", "chartRemote": "proget-charts"},
        remotes,
        env=None,
        credentials=_CREDENTIALS,
    )

    assert (chart, error) == ("oci://proget.example.com:8443/feeds/charts/gatus", None)
    [call] = calls
    assert call["args"] == [
        "helm", "registry", "login", "proget.example.com:8443", "--username", "api", "--password-stdin"
    ]  # fmt: skip
    assert call["input"] == "s3cret-key-123"


@pytest.mark.parametrize("remote_type", [RemoteType.HELM, RemoteType.OCI])
def test_resolve_chart_failed_credentialed_login_is_an_error_without_the_password(monkeypatch, remote_type):
    _record_helm(monkeypatch, failing=lambda args: "401 Unauthorized for api:s3cret-key-123")
    url = "oci://proget.example.com/feeds/charts" if remote_type is RemoteType.OCI else "https://proget.example.com/h"
    remotes = {"proget-charts": _remote("proget-charts", remote_type, url)}

    with pytest.raises(RegistryAuthError) as caught:
        _resolve_chart(
            HelmIntegration(),
            {"chartName": "gatus", "chartRemote": "proget-charts"},
            remotes,
            env=None,
            credentials=_CREDENTIALS,
        )

    error = str(caught.value)
    assert "401 Unauthorized" in error
    assert "s3cret-key-123" not in error and "***" in error


def test_resolve_chart_a_non_auth_problem_with_credentials_present_is_not_an_auth_error():
    """Credentials for a remote the solution does not declare: the error is about the remote, not the login."""
    chart, error = _resolve_chart(
        HelmIntegration(),
        {"chartName": "gatus", "chartRemote": "ghost"},
        {},
        env=None,
        credentials=_CREDENTIALS,
    )

    assert chart is None and "not declared" in str(error)


def test_deploy_namespace_only_a_failed_login_is_labelled_helm_registry_auth_failed(monkeypatch, tmp_path):
    """A remote missing from `remotes` fails with no code, even though credentials were given for it."""
    _record_helm(monkeypatch)
    item, _ = _proget_module(tmp_path)

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(), [item], tokens={}, dry_run=False, registry_credentials={"proget-charts": _CREDENTIALS}
    )

    [error] = diagnostics.items
    assert "not declared" in error.message
    assert error.code is None


@pytest.mark.parametrize(("username", "password"), [("", "s3cret-key-123"), ("api", "")])
def test_registry_credentials_must_have_a_username_and_a_password(username, password):
    with pytest.raises(ValueError, match="non-empty username and password"):
        RegistryCredentials(username=username, password=password)


def test_resolve_chart_inline_repository_never_receives_credentials(monkeypatch):
    calls = _record_helm(monkeypatch)

    _resolve_chart(
        HelmIntegration(),
        {"chartName": "gatus", "chartRepository": "https://charts.example.com"},
        {},
        env=None,
        credentials=_CREDENTIALS,
    )

    assert "--username" not in calls[0]["args"] and calls[0]["input"] is None


def test_deploy_namespace_credentialed_module_shares_one_isolated_helm_config_and_removes_it(monkeypatch, tmp_path):
    calls = _record_helm(monkeypatch)
    item, _ = _proget_module(tmp_path)
    remotes = {"proget-charts": _remote("proget-charts", RemoteType.HELM, "https://proget.example.com/helm/charts")}

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(),
        [item],
        tokens={},
        dry_run=False,
        remotes=remotes,
        env={"KUBECONFIG": "/k"},
        registry_credentials={"proget-charts": _CREDENTIALS},
    )

    assert diagnostics.ok, diagnostics.messages()
    assert [call["args"][1] for call in calls] == ["repo", "upgrade"]
    assert calls[1]["args"][-2:] == ["--version", "1.0.0"]
    for call in calls:
        assert _HELM_STATE_VARS <= call["env"].keys()
        assert call["env"]["KUBECONFIG"] == "/k"
        assert "s3cret-key-123" not in " ".join(call["args"]) + " ".join(call["env"].values())
    assert {call["env"]["HELM_REPOSITORY_CONFIG"] for call in calls} == {calls[0]["env"]["HELM_REPOSITORY_CONFIG"]}
    assert [call["input"] for call in calls] == ["s3cret-key-123", None]
    assert not Path(calls[0]["env"]["HELM_REPOSITORY_CONFIG"]).parent.exists()


def test_deploy_namespace_dry_run_plan_uses_the_same_isolated_config(monkeypatch, tmp_path):
    calls = _record_helm(monkeypatch)
    item, _ = _proget_module(tmp_path)
    remotes = {"proget-charts": _remote("proget-charts", RemoteType.HELM, "https://proget.example.com/helm/charts")}

    HelmIntegration().deploy_namespace(
        _namespace(),
        [item],
        tokens={},
        dry_run=True,
        remotes=remotes,
        registry_credentials={"proget-charts": _CREDENTIALS},
    )

    assert "--dry-run" in calls[1]["args"]
    assert calls[0]["env"]["HELM_REPOSITORY_CONFIG"] == calls[1]["env"]["HELM_REPOSITORY_CONFIG"]


def test_deploy_namespace_failed_login_errors_that_module_only_and_still_removes_the_config(monkeypatch, tmp_path):
    calls = _record_helm(monkeypatch, failing=lambda args: "401 Unauthorized" if args[1] == "repo" else None)
    bad, _ = _proget_module(tmp_path)
    local, _ = _check_fixture(tmp_path, name="local")
    remotes = {"proget-charts": _remote("proget-charts", RemoteType.HELM, "https://proget.example.com/helm/charts")}

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(),
        [bad, local],
        tokens={},
        dry_run=False,
        remotes=remotes,
        registry_credentials={"proget-charts": _CREDENTIALS},
    )

    [error] = diagnostics.items
    assert error.code == "helm_registry_auth_failed" and error.location == "gatus"
    assert "s3cret-key-123" not in error.message
    assert [call["args"][1] for call in calls] == ["repo", "upgrade"]  # the failed module never upgrades
    assert calls[1]["env"] is None  # the uncredentialed local chart is untouched
    assert not Path(calls[0]["env"]["HELM_REPOSITORY_CONFIG"]).parent.exists()


def test_deploy_namespace_removes_the_isolated_config_when_helm_itself_raises(monkeypatch, tmp_path):
    seen: list[str] = []

    def _boom(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        seen.append(env["HELM_REPOSITORY_CONFIG"])
        raise RuntimeError("helm crashed")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _boom)
    item, _ = _proget_module(tmp_path)
    remotes = {"proget-charts": _remote("proget-charts", RemoteType.HELM, "https://proget.example.com/helm/charts")}

    with pytest.raises(RuntimeError):
        HelmIntegration().deploy_namespace(
            _namespace(),
            [item],
            tokens={},
            dry_run=False,
            remotes=remotes,
            registry_credentials={"proget-charts": _CREDENTIALS},
        )

    assert not Path(seen[0]).parent.exists()


def test_deploy_namespace_remote_without_credentials_keeps_the_caller_env_and_gap_2_behaviour(monkeypatch, tmp_path):
    calls = _record_helm(monkeypatch, failing=lambda args: "no route" if args[1] == "repo" else None)
    item, _ = _proget_module(tmp_path)
    remotes = {"proget-charts": _remote("proget-charts", RemoteType.HELM, "https://proget.example.com/helm/charts")}

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(),
        [item],
        tokens={},
        dry_run=False,
        remotes=remotes,
        env={"KUBECONFIG": "/k"},
        registry_credentials={"another-remote": _CREDENTIALS},
    )

    assert diagnostics.ok
    assert [item.code for item in diagnostics.items] == ["helm_repo_refresh_failed"]
    assert all(call["env"] == {"KUBECONFIG": "/k"} for call in calls)
    assert all(call["input"] is None for call in calls)


@pytest.mark.skipif(not shutil.which("helm"), reason="helm is not installed")
def test_credentialed_chart_pull_against_a_real_basic_auth_server_in_an_isolated_helm_config(tmp_path):
    """The whole chain with real helm: credentials, isolated config, `repo add`, pull; the user's config is untouched."""
    import base64
    import hashlib
    import http.server
    import os
    import subprocess
    import threading
    from contextlib import ExitStack

    from strata.integrations.helm import _isolated_helm_env

    def helm(*args: str, env: dict | None = None, cwd: Path | None = None):
        return subprocess.run(
            ["helm", *args], capture_output=True, text=True, env={**os.environ, **(env or {})}, cwd=cwd
        )

    helm("create", "demo", cwd=tmp_path)
    helm("package", "demo", cwd=tmp_path)
    served = tmp_path / "served"
    served.mkdir()
    (tmp_path / "demo-0.1.0.tgz").replace(served / "demo-0.1.0.tgz")
    good = "Basic " + base64.b64encode(b"api:s3cret-key-123").decode()

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(served), **kwargs)

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get("Authorization") != good:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="proget"')
                self.end_headers()
                return
            super().do_GET()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    helm("repo", "index", str(served), "--url", url)
    user_config = Path(helm("env", "HELM_REPOSITORY_CONFIG").stdout.strip())
    before = hashlib.sha256(user_config.read_bytes()).hexdigest() if user_config.exists() else None
    remotes = {"proget": _remote("proget", RemoteType.HELM, url)}
    meta = {"chartName": "demo", "chartRemote": "proget"}
    try:
        with ExitStack() as stack:
            env = _isolated_helm_env(stack)
            chart, error = _resolve_chart(HelmIntegration(), meta, remotes, env=env, credentials=_CREDENTIALS)
            assert error is None and chart is not None
            pulled = helm("pull", chart, "--version", "0.1.0", "--destination", str(tmp_path), env=env)
            assert pulled.returncode == 0, pulled.stderr
        with ExitStack() as stack:
            anonymous_env = _isolated_helm_env(stack)
            _resolve_chart(HelmIntegration(), meta, remotes, env=anonymous_env)
            assert helm(
                "pull", chart, "--version", "0.1.0", "--destination", str(tmp_path), env=anonymous_env
            ).returncode
        after = hashlib.sha256(user_config.read_bytes()).hexdigest() if user_config.exists() else None
        assert before == after
    finally:
        server.shutdown()
        server.server_close()


def test_deploy_namespace_dry_run_passes_the_pinned_chart_version(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    module = _module(source=SourceModel(chart_repository="https://charts.example.com", chart_name="gatus"))
    module_dir = tmp_path / "apps" / "gatus"
    module_dir.mkdir(parents=True)
    (module_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            {
                "releaseName": "gatus",
                "namespace": "apps",
                "chartName": "gatus",
                "chartVersion": "1.0.0",
                "chartRepository": "https://charts.example.com",
            }
        )
    )
    (module_dir / "values.yaml").write_text(yaml.safe_dump({"replicas": 1}))
    resolved_module = _resolved_module("gatus", module, module_dir)

    diagnostics = HelmIntegration().deploy_namespace(_namespace(), [resolved_module], tokens={}, dry_run=True)

    assert diagnostics.ok
    assert captured["args"][-2:] == ["--version", "1.0.0"]
    assert "--dry-run" in captured["args"]


def test_deploy_namespace_inline_chart_repository_resolves_chart_ref_without_a_declared_remote(
    monkeypatch, tmp_path: Path
):
    captured = _capture(monkeypatch)
    module = _module(
        source=SourceModel(chart_repository="https://charts.goauthentik.io", chart_name="authentik"),
        services=[
            ModuleServiceModel(name="server", environment=[ModuleServiceEnvironmentModel(key="TZ", value="${var:tz}")])
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
                "chartRepository": "https://charts.goauthentik.io",
            }
        )
    )
    (module_dir / "values.yaml").write_text(yaml.safe_dump({"authentik-server": {"env": {"TZ": "${var:tz}"}}}))
    resolved_module = _resolved_module("authentik", module, module_dir)

    # No `remotes=` passed at all — an inline chart_repository needs no solution-level declaration.
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
        f"{_repo_alias('https://charts.goauthentik.io')}/authentik",
    ]


def test_deploy_namespace_missing_meta_file_reports_a_diagnostic_and_skips(monkeypatch, tmp_path: Path):
    _capture(monkeypatch)
    module = _module()
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)
    resolved_module = _resolved_module("authentik", module, module_dir)

    diagnostics = HelmIntegration().deploy_namespace(_namespace(), [resolved_module], tokens={}, dry_run=False)

    assert not diagnostics.ok
    assert "meta.yaml not found" in diagnostics.errors[0].message


def test_deploy_namespace_unresolvable_token_reports_a_diagnostic_and_skips(monkeypatch, tmp_path: Path):
    """A missing Value token key must not escape as a bare `ValueError` —
    `resolve_module_values()` raises one, and this call site must catch it
    exactly like `ComposeIntegration.deploy_namespace()` already does
    (`test_deploy_namespace_unresolvable_token_reports_a_diagnostic` in
    test_integrations_compose.py)."""
    _capture(monkeypatch)
    module = _module(
        services=[
            ModuleServiceModel(name="server", environment=[ModuleServiceEnvironmentModel(key="TZ", value="${var:tz}")])
        ]
    )
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)
    (module_dir / "meta.yaml").write_text(yaml.safe_dump({"releaseName": "authentik", "namespace": "apps"}))
    resolved_module = _resolved_module("authentik", module, module_dir)

    diagnostics = HelmIntegration().deploy_namespace(_namespace(), [resolved_module], tokens={}, dry_run=False)

    assert not diagnostics.ok
    assert "did not resolve to a value" in diagnostics.errors[0].message


def test_deploy_namespace_output_preflight_skips_only_the_offending_module(monkeypatch, tmp_path: Path):
    """docs/design/output-declarations.md Phase 3 — a module referencing a
    missing '${output:...}' key is skipped with a specific diagnostic;
    every OTHER module in the same namespace still deploys (unlike
    deploy_controller.py's Terraform steps, which abort the whole run)."""
    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)

    good_module = _module(
        "good",
        services=[
            ModuleServiceModel(
                name="server", environment=[ModuleServiceEnvironmentModel(key="IP", value="${output:infra.vm_ip}")]
            )
        ],
    )
    bad_module = _module(
        "bad",
        services=[
            ModuleServiceModel(
                name="server", environment=[ModuleServiceEnvironmentModel(key="IP", value="${output:infra.other_ip}")]
            )
        ],
    )
    good_dir = tmp_path / "apps" / "good"
    bad_dir = tmp_path / "apps" / "bad"
    good_dir.mkdir(parents=True)
    bad_dir.mkdir(parents=True)
    (good_dir / "meta.yaml").write_text(yaml.safe_dump({"releaseName": "good", "namespace": "apps"}))
    (bad_dir / "meta.yaml").write_text(yaml.safe_dump({"releaseName": "bad", "namespace": "apps"}))
    resolved_modules = [
        _resolved_module("good", good_module, good_dir),
        _resolved_module("bad", bad_module, bad_dir),
    ]

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(), resolved_modules, tokens={"infra.vm_ip": "10.0.0.1"}, dry_run=False
    )

    assert not diagnostics.ok
    message = diagnostics.errors[0].message
    assert "module 'bad'" in message
    assert "'${output:infra.other_ip}'" in message
    assert "Available from 'infra': ['vm_ip']" in message
    # The good module still deployed — one real `helm upgrade` call, for 'good' only.
    assert len(calls) == 1
    assert "good" in calls[0]
    assert "bad" not in calls[0]


def test_deploy_namespace_output_preflight_runs_under_dry_run_too(monkeypatch, tmp_path: Path):
    """The output pre-flight check is unconditional, placed before the
    `if dry_run:` branch — a missing key must be reported, and `helm`
    must never run at all for that module, under a dry-run preview
    exactly as under a real deploy."""
    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)

    module = _module(
        "bad",
        services=[
            ModuleServiceModel(
                name="server", environment=[ModuleServiceEnvironmentModel(key="IP", value="${output:infra.other_ip}")]
            )
        ],
    )
    module_dir = tmp_path / "apps" / "bad"
    module_dir.mkdir(parents=True)
    (module_dir / "meta.yaml").write_text(yaml.safe_dump({"releaseName": "bad", "namespace": "apps"}))
    resolved_module = _resolved_module("bad", module, module_dir)

    diagnostics = HelmIntegration().deploy_namespace(
        _namespace(), [resolved_module], tokens={"infra.vm_ip": "10.0.0.1"}, dry_run=True
    )

    assert not diagnostics.ok
    assert "'${output:infra.other_ip}'" in diagnostics.errors[0].message
    assert calls == []


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


def test_deploy_namespace_dry_run_calls_plan_not_deploy(monkeypatch, tmp_path: Path):
    """`dry_run` is a real preview now: it writes the resolved
    `values.yaml` (same real input a deploy would use) and calls `helm
    upgrade --dry-run --install` via `plan_or_warn()`, never
    `self.deploy()`."""
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
    assert captured["args"] == [
        "helm",
        "upgrade",
        "--dry-run",
        "--install",
        "--namespace",
        "apps",
        "-f",
        str(module_dir / "values.yaml"),
        "authentik",
        str(module_dir),
    ]
    # A real preview needs the same real, resolved input a deploy would use.
    assert (module_dir / "values.yaml").read_text() != values_before


def test_deploy_namespace_dry_run_plan_failure_is_reported_as_an_error(monkeypatch, tmp_path: Path):
    module = _module(services=[ModuleServiceModel(name="server")])
    module_dir = tmp_path / "apps" / "authentik"
    module_dir.mkdir(parents=True)
    (module_dir / "meta.yaml").write_text(yaml.safe_dump({"releaseName": "authentik", "namespace": "apps"}))
    (module_dir / "values.yaml").write_text(yaml.safe_dump({"authentik-server": {}}))
    resolved_module = _resolved_module("authentik", module, module_dir)

    def _failing_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=1, stdout="", stderr="chart not found")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _failing_run_command)

    diagnostics = HelmIntegration().deploy_namespace(_namespace(), [resolved_module], tokens={}, dry_run=True)

    assert not diagnostics.ok
    assert "helm plan (dry-run) failed" in diagnostics.errors[0].message
