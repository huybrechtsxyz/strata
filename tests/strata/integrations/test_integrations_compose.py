#!/usr/bin/env python3
"""Tests for `ComposeIntegration` (ADR-0021 Phase 6)."""

from pathlib import Path

import pytest
import yaml

from strata.integrations.compose import ComposeIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.registry import get
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.common_models import ModuleReferenceModel, SourceModel
from strata.models.module_model import (
    ModuleCheckModel,
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
    assert ComposeIntegration.TYPE == "compose"
    assert ComposeIntegration.CAPABILITIES == {"container"}
    assert ComposeIntegration.TRANSPORTS == {"cli"}
    assert ComposeIntegration.COMMAND == "docker"


def test_registered_in_the_registry():
    assert isinstance(get("compose"), ComposeIntegration)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Docker version 24.0.7, build afdd53b", "24.0.7"),
        ("garbage", "garbage"),
    ],
)
def test_parse_version(raw, expected):
    assert ComposeIntegration().parse_version(raw) == expected


def test_plan_renders_config_with_no_stack_name(monkeypatch):
    """No true dry-run exists for Swarm — closest analog takes no stack argument."""
    captured = _capture(monkeypatch)
    ComposeIntegration().plan(Path("/work/ns/docker-compose.yml"))
    assert captured["args"] == ["docker", "stack", "config", "-c", str(Path("/work/ns/docker-compose.yml"))]
    assert captured["cwd"] == Path("/work/ns")


def test_deploy_argv_with_registry_auth(monkeypatch):
    captured = _capture(monkeypatch)
    ComposeIntegration().deploy(Path("/work/ns/docker-compose.yml"), namespace="ns")
    assert captured["args"] == [
        "docker",
        "stack",
        "deploy",
        "--with-registry-auth",
        "-c",
        str(Path("/work/ns/docker-compose.yml")),
        "ns",
    ]


def test_deploy_argv_without_registry_auth(monkeypatch):
    captured = _capture(monkeypatch)
    ComposeIntegration().deploy(Path("/work/ns/docker-compose.yml"), namespace="ns", with_registry_auth=False)
    assert captured["args"] == ["docker", "stack", "deploy", "-c", str(Path("/work/ns/docker-compose.yml")), "ns"]


def test_destroy_argv(monkeypatch):
    captured = _capture(monkeypatch)
    ComposeIntegration().destroy(Path("/work/ns/docker-compose.yml"), namespace="ns")
    assert captured["args"] == ["docker", "stack", "rm", "ns"]


def test_output_argv(monkeypatch):
    captured = _capture(monkeypatch)
    ComposeIntegration().output(Path("/work/ns/docker-compose.yml"), namespace="ns")
    assert captured["args"] == ["docker", "stack", "services", "ns"]


# ---------------------------------------------------------------------------
# prepare_namespace() — ADR-0022 D6/D7
# ---------------------------------------------------------------------------


def _namespace(name: str = "apps") -> NamespaceModel:
    return NamespaceModel(
        meta=NamespaceMetaModel(name=name),
        # NamespaceSpecModel requires lifecycle and/or modules - this
        # placeholder ref is never resolved by these tests, which pass
        # their own ResolvedModule list directly.
        spec=NamespaceSpecModel(
            default_labels={}, lifecycle=None, modules=[ModuleReferenceModel(name="placeholder", module="placeholder")]
        ),
    )


def _module(
    name: str,
    *,
    services: list[ModuleServiceModel] | None = None,
    compose_file: str | None = None,
) -> ModuleModel:
    return ModuleModel(
        meta=ModuleMetaModel(name=name),
        spec=ModuleSpecModel(
            source=SourceModel(source_path=f"services/{name}"),
            type="compose",
            services=services,
            compose_file=compose_file,
            default_labels={},
        ),
    )


def _resolved(reference_name: str, module: ModuleModel, source_path: Path) -> ResolvedModule:
    return ResolvedModule(
        reference=ModuleReferenceModel(name=reference_name, module=module.meta.name),
        module=module,
        source_path=source_path,
    )


def test_prepare_namespace_merges_every_module_into_one_file(tmp_path: Path):
    """Unlike Helm (one pair of files per module), Compose merges the whole
    group into a single docker-compose.yml at the namespace level."""
    caddy = _module("caddy", services=[ModuleServiceModel(name="caddy", image="caddy:2-alpine")])
    portainer = _module(
        "portainer", services=[ModuleServiceModel(name="portainer", image="portainer/portainer-ce:2.39.3-alpine")]
    )
    namespace_dir = tmp_path / "hearth"
    modules = [
        _resolved("caddy", caddy, namespace_dir / "caddy"),
        _resolved("portainer", portainer, namespace_dir / "portainer"),
    ]

    ComposeIntegration().prepare_namespace(_namespace("hearth"), modules, resolved=ValueResolution(deployment="app"))

    compose_file = namespace_dir / "docker-compose.yml"
    assert compose_file.exists()
    assert not (namespace_dir / "caddy" / "docker-compose.yml").exists()
    document = yaml.safe_load(compose_file.read_text())
    assert set(document["services"]) == {"caddy", "portainer"}


def test_prepare_namespace_prefixes_service_names_by_module(tmp_path: Path):
    module = _module(
        "authentik",
        services=[
            ModuleServiceModel(name="db", image="postgres:16-alpine"),
            ModuleServiceModel(name="server", image="ghcr.io/goauthentik/server:2026.5.2"),
        ],
    )
    namespace_dir = tmp_path / "hearth"

    ComposeIntegration().prepare_namespace(
        _namespace("hearth"),
        [_resolved("authentik", module, namespace_dir / "authentik")],
        resolved=ValueResolution(deployment="app"),
    )

    document = yaml.safe_load((namespace_dir / "docker-compose.yml").read_text())
    assert set(document["services"]) == {"authentik-db", "authentik-server"}


def test_prepare_namespace_omits_prefix_when_service_name_equals_module_name(tmp_path: Path):
    module = _module("portainer", services=[ModuleServiceModel(name="portainer", image="portainer/portainer-ce")])
    namespace_dir = tmp_path / "hearth"

    ComposeIntegration().prepare_namespace(
        _namespace("hearth"),
        [_resolved("portainer", module, namespace_dir / "portainer")],
        resolved=ValueResolution(deployment="app"),
    )

    document = yaml.safe_load((namespace_dir / "docker-compose.yml").read_text())
    assert list(document["services"]) == ["portainer"]


def test_prepare_namespace_writes_environment_ports_command_restart_verbatim(tmp_path: Path):
    module = _module(
        "portainer",
        services=[
            ModuleServiceModel(
                name="portainer",
                image="portainer/portainer-ce:2.39.3-alpine",
                restart="unless-stopped",
                command=["--http-disabled"],
                ports=["8000:8000"],
                environment=[
                    ModuleServiceEnvironmentModel(key="TZ", value="Europe/Brussels"),
                    ModuleServiceEnvironmentModel(key="TRUSTED_ORIGINS", value="portainer.huybrechts.xyz"),
                ],
            )
        ],
    )
    namespace_dir = tmp_path / "hearth"

    ComposeIntegration().prepare_namespace(
        _namespace("hearth"),
        [_resolved("portainer", module, namespace_dir / "portainer")],
        resolved=ValueResolution(deployment="app"),
    )

    entry = yaml.safe_load((namespace_dir / "docker-compose.yml").read_text())["services"]["portainer"]
    assert entry["image"] == "portainer/portainer-ce:2.39.3-alpine"
    assert entry["restart"] == "unless-stopped"
    assert entry["command"] == ["--http-disabled"]
    assert entry["ports"] == ["8000:8000"]
    assert entry["environment"] == {"TZ": "Europe/Brussels", "TRUSTED_ORIGINS": "portainer.huybrechts.xyz"}


def test_prepare_namespace_renders_bind_and_named_volume_mounts(tmp_path: Path):
    module = _module(
        "portainer",
        services=[
            ModuleServiceModel(
                name="portainer",
                mounts=[
                    ModuleMountModel(name="data", source_path="/opt/haven/var/data/portainer", target_path="/data"),
                    ModuleMountModel(name="socket", volume_ref="docker-socket", target_path="/var/run/docker.sock"),
                ],
            )
        ],
    )
    namespace_dir = tmp_path / "hearth"

    ComposeIntegration().prepare_namespace(
        _namespace("hearth"),
        [_resolved("portainer", module, namespace_dir / "portainer")],
        resolved=ValueResolution(deployment="app"),
    )

    document = yaml.safe_load((namespace_dir / "docker-compose.yml").read_text())
    entry = document["services"]["portainer"]
    assert "/opt/haven/var/data/portainer:/data" in entry["volumes"]
    assert "hearth_portainer_docker-socket:/var/run/docker.sock" in entry["volumes"]
    assert "hearth_portainer_docker-socket" in document["volumes"]


def test_prepare_namespace_resolves_intra_module_depends_on(tmp_path: Path):
    module = _module(
        "authentik",
        services=[
            ModuleServiceModel(name="db"),
            ModuleServiceModel(name="server", depends_on=["db"]),
        ],
    )
    namespace_dir = tmp_path / "hearth"

    ComposeIntegration().prepare_namespace(
        _namespace("hearth"),
        [_resolved("authentik", module, namespace_dir / "authentik")],
        resolved=ValueResolution(deployment="app"),
    )

    document = yaml.safe_load((namespace_dir / "docker-compose.yml").read_text())
    assert document["services"]["authentik-server"]["depends_on"] == ["authentik-db"]


def test_prepare_namespace_resolves_cross_module_depends_on(tmp_path: Path):
    auth = _module("authentik", services=[ModuleServiceModel(name="server")])
    caddy = _module("caddy", services=[ModuleServiceModel(name="caddy", depends_on=["@authentik/server"])])
    namespace_dir = tmp_path / "hearth"

    ComposeIntegration().prepare_namespace(
        _namespace("hearth"),
        [_resolved("authentik", auth, namespace_dir / "authentik"), _resolved("caddy", caddy, namespace_dir / "caddy")],
        resolved=ValueResolution(deployment="app"),
    )

    document = yaml.safe_load((namespace_dir / "docker-compose.yml").read_text())
    assert document["services"]["caddy"]["depends_on"] == ["authentik-server"]


def test_prepare_namespace_cross_module_depends_on_unknown_module_raises(tmp_path: Path):
    caddy = _module("caddy", services=[ModuleServiceModel(name="caddy", depends_on=["@ghost/server"])])
    namespace_dir = tmp_path / "hearth"

    with pytest.raises(IntegrationError, match="ghost"):
        ComposeIntegration().prepare_namespace(
            _namespace("hearth"),
            [_resolved("caddy", caddy, namespace_dir / "caddy")],
            resolved=ValueResolution(deployment="app"),
        )


def test_prepare_namespace_renders_healthcheck_from_command(tmp_path: Path):
    module = _module(
        "authentik",
        services=[
            ModuleServiceModel(
                name="db",
                healthcheck=ModuleCheckModel(
                    name="pg-ready", type="command", command=["pg_isready", "-U", "authentik"],
                    interval="30s", timeout="5s", retries=5,
                ),
            )
        ],
    )
    namespace_dir = tmp_path / "hearth"

    ComposeIntegration().prepare_namespace(
        _namespace("hearth"),
        [_resolved("authentik", module, namespace_dir / "authentik")],
        resolved=ValueResolution(deployment="app"),
    )

    healthcheck = yaml.safe_load((namespace_dir / "docker-compose.yml").read_text())["services"]["authentik-db"]["healthcheck"]
    assert healthcheck == {
        "test": ["CMD", "pg_isready", "-U", "authentik"],
        "interval": "30s",
        "timeout": "5s",
        "retries": 5,
    }


def test_prepare_namespace_merges_service_configuration_verbatim(tmp_path: Path):
    module = _module(
        "portainer", services=[ModuleServiceModel(name="portainer", configuration={"cap_add": ["NET_ADMIN"]})]
    )
    namespace_dir = tmp_path / "hearth"

    ComposeIntegration().prepare_namespace(
        _namespace("hearth"),
        [_resolved("portainer", module, namespace_dir / "portainer")],
        resolved=ValueResolution(deployment="app"),
    )

    entry = yaml.safe_load((namespace_dir / "docker-compose.yml").read_text())["services"]["portainer"]
    assert entry["cap_add"] == ["NET_ADMIN"]


def test_prepare_namespace_compose_file_passthrough_raises_clearly(tmp_path: Path):
    module = _module("legacy", services=None, compose_file="legacy/docker-compose.yml")
    namespace_dir = tmp_path / "hearth"

    with pytest.raises(IntegrationError, match="compose_file pass-through is not implemented"):
        ComposeIntegration().prepare_namespace(
            _namespace("hearth"),
            [_resolved("legacy", module, namespace_dir / "legacy")],
            resolved=ValueResolution(deployment="app"),
        )


def test_prepare_namespace_does_nothing_for_an_empty_group():
    # No modules, no directory to require - must not raise or touch disk.
    ComposeIntegration().prepare_namespace(_namespace("hearth"), [], resolved=ValueResolution(deployment="app"))
