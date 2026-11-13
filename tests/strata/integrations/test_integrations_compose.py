#!/usr/bin/env python3
"""Tests for `ComposeIntegration` (ADR-0021 Phase 6)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from strata.integrations.compose import (
    DOCKER_CONTEXT_ENV,
    DOCKER_HOST_ENV,
    ComposeIntegration,
    resolve_compose_values,
)
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
    # Compose uses unprefixed `${KEY}` interpolation - a different
    # mechanism entirely, not "an empty prefix" - so it inherits the base
    # `None`, never declares its own.
    assert ComposeIntegration.ENV_VAR_PREFIX is None


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
    # --detach=false: a successful destroy means the tasks have stopped, not just that removal was requested.
    assert captured["args"] == ["docker", "stack", "rm", "--detach=false", "ns"]


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
                    name="pg-ready",
                    type="command",
                    command=["pg_isready", "-U", "authentik"],
                    interval="30s",
                    timeout="5s",
                    retries=5,
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

    healthcheck = yaml.safe_load((namespace_dir / "docker-compose.yml").read_text())["services"]["authentik-db"][
        "healthcheck"
    ]
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


# ---------------------------------------------------------------------------
# resolve_compose_values() — docs/work/gap_fit_v1.md gap #9, Full Solution Phase 5.
# Deliberately pure (no disk I/O) — mirrors resolve_module_values()'s own
# testable-without-touching-disk convention.
# ---------------------------------------------------------------------------


def test_resolve_compose_values_resolves_non_secret_leaves():
    document = {"services": {"portainer": {"environment": {"TZ": "${var:tz}"}}}}
    resolved, secrets = resolve_compose_values(document, {"tz": "Europe/Brussels"})
    assert resolved == {"services": {"portainer": {"environment": {"TZ": "Europe/Brussels"}}}}
    assert secrets == {}


def test_resolve_compose_values_renames_a_secret_token_to_bare_dollar_brace_key():
    document = {"services": {"portainer": {"environment": {"DB_PASSWORD": "${secret:db_password}"}}}}
    resolved, secrets = resolve_compose_values(document, {"db_password": "hunter2"})
    assert resolved == {"services": {"portainer": {"environment": {"DB_PASSWORD": "${db_password}"}}}}
    assert secrets == {"db_password": "hunter2"}


def test_resolve_compose_values_does_not_touch_disk():
    document = {"services": {"portainer": {"image": "portainer/portainer-ce"}}}
    resolved, secrets = resolve_compose_values(document, {})
    assert resolved == document
    assert secrets == {}


# ---------------------------------------------------------------------------
# deploy_namespace() — docs/work/gap_fit_v1.md gap #13
# ---------------------------------------------------------------------------


def test_deploy_namespace_writes_the_deploy_file_and_deploys_the_stack(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    module = _module("portainer", services=[ModuleServiceModel(name="portainer")])
    namespace_dir = tmp_path / "hearth"
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "docker-compose.yml").write_text(
        yaml.safe_dump(
            {
                "services": {
                    "portainer": {
                        "environment": {"TZ": "${var:tz}", "DB_PASSWORD": "${secret:db_password}"},
                    }
                }
            }
        )
    )
    resolved_module = _resolved("portainer", module, namespace_dir / "portainer")

    diagnostics = ComposeIntegration().deploy_namespace(
        _namespace("hearth"),
        [resolved_module],
        tokens={"tz": "Europe/Brussels", "db_password": "hunter2"},
        dry_run=False,
    )

    assert diagnostics.ok
    assert captured["args"] == [
        "docker",
        "stack",
        "deploy",
        "--with-registry-auth",
        "-c",
        str(namespace_dir / "docker-compose.deploy.yml"),
        "hearth",
    ]
    assert captured["env"] == {"db_password": "hunter2"}
    document = yaml.safe_load((namespace_dir / "docker-compose.deploy.yml").read_text())
    assert document["services"]["portainer"]["environment"] == {
        "TZ": "Europe/Brussels",
        "DB_PASSWORD": "${db_password}",
    }
    # The build's file keeps its tokens, so the next deploy of this build resolves them again.
    built = yaml.safe_load((namespace_dir / "docker-compose.yml").read_text())
    assert built["services"]["portainer"]["environment"]["DB_PASSWORD"] == "${secret:db_password}"


def test_deploy_namespace_twice_delivers_the_secrets_and_current_values_both_times(monkeypatch, tmp_path: Path):
    """A retry, or a deploy after a dry run, of the same build: the second run starts from the built file too."""
    captured = _capture(monkeypatch)
    module = _module("portainer", services=[ModuleServiceModel(name="portainer")])
    namespace_dir = tmp_path / "hearth"
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "docker-compose.yml").write_text(
        yaml.safe_dump({"services": {"portainer": {"environment": {"HOST": "${var:host}", "PWD": "${secret:pwd}"}}}})
    )
    resolved_module = _resolved("portainer", module, namespace_dir / "portainer")
    integration = ComposeIntegration()

    integration.deploy_namespace(
        _namespace("hearth"), [resolved_module], tokens={"host": "old", "pwd": "hunter2"}, dry_run=True
    )
    diagnostics = integration.deploy_namespace(
        _namespace("hearth"), [resolved_module], tokens={"host": "new", "pwd": "hunter2"}, dry_run=False
    )

    assert diagnostics.ok
    assert captured["env"] == {"pwd": "hunter2"}
    document = yaml.safe_load((namespace_dir / "docker-compose.deploy.yml").read_text())
    assert document["services"]["portainer"]["environment"] == {"HOST": "new", "PWD": "${pwd}"}


def test_deploy_namespace_merges_caller_env_with_secrets(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    module = _module("portainer", services=[ModuleServiceModel(name="portainer")])
    namespace_dir = tmp_path / "hearth"
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "docker-compose.yml").write_text(
        yaml.safe_dump({"services": {"portainer": {"environment": {"DB_PASSWORD": "${secret:db_password}"}}}})
    )
    resolved_module = _resolved("portainer", module, namespace_dir / "portainer")

    ComposeIntegration().deploy_namespace(
        _namespace("hearth"),
        [resolved_module],
        tokens={"db_password": "hunter2"},
        dry_run=False,
        env={"TF_VAR_unrelated": "x"},
    )

    assert captured["env"] == {"TF_VAR_unrelated": "x", "db_password": "hunter2"}


def test_deploy_namespace_missing_compose_file_reports_a_diagnostic(monkeypatch, tmp_path: Path):
    _capture(monkeypatch)
    module = _module("portainer", services=[ModuleServiceModel(name="portainer")])
    namespace_dir = tmp_path / "hearth"
    namespace_dir.mkdir(parents=True)
    resolved_module = _resolved("portainer", module, namespace_dir / "portainer")

    diagnostics = ComposeIntegration().deploy_namespace(
        _namespace("hearth"), [resolved_module], tokens={}, dry_run=False
    )

    assert not diagnostics.ok
    assert "docker-compose.yml not found" in diagnostics.errors[0].message


def test_deploy_namespace_unresolvable_token_reports_a_diagnostic(monkeypatch, tmp_path: Path):
    _capture(monkeypatch)
    module = _module("portainer", services=[ModuleServiceModel(name="portainer")])
    namespace_dir = tmp_path / "hearth"
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "docker-compose.yml").write_text(
        yaml.safe_dump({"services": {"portainer": {"environment": {"TZ": "${var:missing}"}}}})
    )
    resolved_module = _resolved("portainer", module, namespace_dir / "portainer")

    diagnostics = ComposeIntegration().deploy_namespace(
        _namespace("hearth"), [resolved_module], tokens={}, dry_run=False
    )

    assert not diagnostics.ok
    assert "did not resolve to a value" in diagnostics.errors[0].message


def test_deploy_namespace_output_preflight_aborts_the_whole_namespace(monkeypatch, tmp_path: Path):
    """docs/design/output-declarations.md Phase 3 — a missing '${output:...}'
    key in the merged compose document aborts the whole namespace, same
    granularity the existing `ValueError`-catch path above already uses
    (unlike Helm, Compose has already merged every module into one
    document by this point — there is no single offending module to skip)."""
    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)

    module = _module("portainer", services=[ModuleServiceModel(name="portainer")])
    namespace_dir = tmp_path / "hearth"
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "docker-compose.yml").write_text(
        yaml.safe_dump({"services": {"portainer": {"environment": {"IP": "${output:infra.other_ip}"}}}})
    )
    resolved_module = _resolved("portainer", module, namespace_dir / "portainer")

    diagnostics = ComposeIntegration().deploy_namespace(
        _namespace("hearth"), [resolved_module], tokens={"infra.vm_ip": "10.0.0.1"}, dry_run=False
    )

    assert not diagnostics.ok
    message = diagnostics.errors[0].message
    assert "'${output:infra.other_ip}'" in message
    assert "Available from 'infra': ['vm_ip']" in message
    assert calls == []


def test_deploy_namespace_output_preflight_runs_under_dry_run_too(monkeypatch, tmp_path: Path):
    """The output pre-flight check is unconditional, placed before the
    `if dry_run:` branch — a missing key must be reported, and `docker`
    must never run at all, under a dry-run preview exactly as under a
    real deploy."""
    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)

    module = _module("portainer", services=[ModuleServiceModel(name="portainer")])
    namespace_dir = tmp_path / "hearth"
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "docker-compose.yml").write_text(
        yaml.safe_dump({"services": {"portainer": {"environment": {"IP": "${output:infra.other_ip}"}}}})
    )
    resolved_module = _resolved("portainer", module, namespace_dir / "portainer")

    diagnostics = ComposeIntegration().deploy_namespace(
        _namespace("hearth"), [resolved_module], tokens={"infra.vm_ip": "10.0.0.1"}, dry_run=True
    )

    assert not diagnostics.ok
    assert "'${output:infra.other_ip}'" in diagnostics.errors[0].message
    assert calls == []


def test_deploy_namespace_dry_run_calls_plan_not_deploy(monkeypatch, tmp_path: Path):
    """`dry_run` is a real preview now: it writes the resolved
    `docker-compose.yml` (same real input a deploy would use) and calls
    `docker stack config` via `plan_or_warn()`, never `self.deploy()`."""
    captured = _capture(monkeypatch)
    module = _module("portainer", services=[ModuleServiceModel(name="portainer")])
    namespace_dir = tmp_path / "hearth"
    namespace_dir.mkdir(parents=True)
    contents_before = yaml.safe_dump({"services": {"portainer": {"environment": {"TZ": "${var:tz}"}}}})
    (namespace_dir / "docker-compose.yml").write_text(contents_before)
    resolved_module = _resolved("portainer", module, namespace_dir / "portainer")

    diagnostics = ComposeIntegration().deploy_namespace(
        _namespace("hearth"), [resolved_module], tokens={"tz": "Europe/Brussels"}, dry_run=True
    )

    assert diagnostics.ok
    assert captured["args"] == ["docker", "stack", "config", "-c", str(namespace_dir / "docker-compose.deploy.yml")]
    # A real preview needs the same real, resolved input a deploy would use.
    assert (namespace_dir / "docker-compose.yml").read_text() == contents_before
    assert (namespace_dir / "docker-compose.deploy.yml").read_text() != contents_before


def test_deploy_namespace_dry_run_plan_failure_is_reported_as_an_error(monkeypatch, tmp_path: Path):
    module = _module("portainer", services=[ModuleServiceModel(name="portainer")])
    namespace_dir = tmp_path / "hearth"
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "docker-compose.yml").write_text(yaml.safe_dump({"services": {"portainer": {}}}))
    resolved_module = _resolved("portainer", module, namespace_dir / "portainer")

    def _failing_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=1, stdout="", stderr="invalid compose file")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _failing_run_command)

    diagnostics = ComposeIntegration().deploy_namespace(
        _namespace("hearth"), [resolved_module], tokens={}, dry_run=True
    )

    assert not diagnostics.ok
    assert "docker stack config (dry-run) failed" in diagnostics.errors[0].message


def test_deploy_namespace_does_nothing_for_an_empty_group():
    diagnostics = ComposeIntegration().deploy_namespace(_namespace("hearth"), [], tokens={}, dry_run=False)
    assert diagnostics.ok


# ---------------------------------------------------------------------------
# destroy()/destroy_namespace() (docs/design/deploy-destroy.md)
# ---------------------------------------------------------------------------


def test_destroy_forwards_line_callback_and_timeout(monkeypatch):
    seen: dict[str, object] = {}

    def _fake(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        seen["timeout"] = timeout
        seen["line_callback"] = line_callback
        return CommandResult(returncode=0, stdout="", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake)

    def callback(stream: str, text: str) -> None:
        return None

    ComposeIntegration().destroy(
        Path("/work/ns/docker-compose.yml"), namespace="ns", timeout=42, line_callback=callback
    )
    assert seen["timeout"] == 42
    assert seen["line_callback"] is callback


def _script_docker(monkeypatch, responder):
    """Stub `run_command`, recording every call; `responder(args)` returns the `CommandResult`."""
    calls: list[dict] = []

    def _fake(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append({"args": list(args), "timeout": timeout, "line_callback": line_callback})
        return responder(list(args))

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake)
    return calls


def _ok(stdout: str = "") -> CommandResult:
    return CommandResult(returncode=0, stdout=stdout, stderr="")


def _destroy_group(tmp_path: Path) -> list[ResolvedModule]:
    return [_resolved("caddy", _module("caddy"), tmp_path / "hearth" / "caddy")]


def test_destroy_namespace_removes_the_stack_and_waits_for_it(tmp_path: Path, monkeypatch):
    calls = _script_docker(monkeypatch, lambda args: _ok())
    steps: list[str] = []

    diagnostics = ComposeIntegration().destroy_namespace(
        _namespace("hearth"), _destroy_group(tmp_path), dry_run=False, on_step=steps.append
    )

    assert diagnostics.ok
    assert [call["args"] for call in calls] == [["docker", "stack", "rm", "--detach=false", "hearth"]]
    assert any("hearth" in step for step in steps)


def test_destroy_namespace_reports_a_failed_removal_with_its_stderr(tmp_path: Path, monkeypatch):
    _script_docker(
        monkeypatch,
        lambda args: CommandResult(returncode=1, stdout="", stderr="This node is not a swarm manager."),
    )

    diagnostics = ComposeIntegration().destroy_namespace(_namespace("hearth"), _destroy_group(tmp_path), dry_run=False)

    assert not diagnostics.ok
    assert "docker stack rm failed" in diagnostics.errors[0].message
    assert "not a swarm manager" in diagnostics.errors[0].message


def test_destroy_namespace_forwards_timeout_and_line_callback(tmp_path: Path, monkeypatch):
    calls = _script_docker(monkeypatch, lambda args: _ok())

    def callback(stream: str, text: str) -> None:
        return None

    ComposeIntegration().destroy_namespace(
        _namespace("hearth"), _destroy_group(tmp_path), dry_run=False, timeout=42, line_callback=callback
    )

    assert calls[0]["timeout"] == 42
    assert calls[0]["line_callback"] is callback


def test_deploy_namespace_forwards_the_stage_timeout_to_deploy_and_to_the_preview(tmp_path: Path, monkeypatch):
    calls = _script_docker(monkeypatch, lambda args: _ok())
    group = _destroy_group(tmp_path)
    group[0].source_path.parent.mkdir(parents=True)
    (group[0].source_path.parent / "docker-compose.yml").write_text("services: {}\n")

    for dry_run in (False, True):
        assert (
            ComposeIntegration()
            .deploy_namespace(_namespace("hearth"), group, tokens={}, dry_run=dry_run, timeout=42)
            .ok
        )

    assert [(call["args"][2], call["timeout"]) for call in calls] == [("deploy", 42), ("config", 42)]


def test_destroy_namespace_does_nothing_for_an_empty_group(monkeypatch):
    calls = _script_docker(monkeypatch, lambda args: _ok())

    assert ComposeIntegration().destroy_namespace(_namespace("hearth"), [], dry_run=False).ok
    assert calls == []


def test_destroy_namespace_dry_run_lists_the_running_services_and_removes_nothing(tmp_path: Path, monkeypatch):
    calls = _script_docker(monkeypatch, lambda args: _ok("ID   NAME           MODE\nabc  hearth_caddy  replicated\n"))
    steps: list[str] = []

    diagnostics = ComposeIntegration().destroy_namespace(
        _namespace("hearth"), _destroy_group(tmp_path), dry_run=True, on_step=steps.append
    )

    assert diagnostics.ok
    assert [call["args"] for call in calls] == [["docker", "stack", "services", "hearth"]]
    assert any("hearth_caddy" in step for step in steps)


def test_destroy_namespace_dry_run_says_a_missing_stack_has_nothing_to_remove(tmp_path: Path, monkeypatch):
    _script_docker(monkeypatch, lambda args: _ok(""))
    steps: list[str] = []

    diagnostics = ComposeIntegration().destroy_namespace(
        _namespace("hearth"), _destroy_group(tmp_path), dry_run=True, on_step=steps.append
    )

    assert diagnostics.ok
    assert any("not found, nothing to remove" in step for step in steps)


def test_destroy_namespace_dry_run_warns_when_the_stack_cannot_be_listed(tmp_path: Path, monkeypatch):
    _script_docker(
        monkeypatch,
        lambda args: CommandResult(returncode=1, stdout="", stderr="This node is not a swarm manager."),
    )

    diagnostics = ComposeIntegration().destroy_namespace(_namespace("hearth"), _destroy_group(tmp_path), dry_run=True)

    assert diagnostics.ok
    assert len(diagnostics.warnings) == 1
    assert "not a swarm manager" in diagnostics.warnings[0].message


# ---------------------------------------------------------------------------
# The docker context a deployment names (docs/design/deploy-command.md)
# ---------------------------------------------------------------------------


def _docker_knowing(monkeypatch, *contexts: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        if args[1:3] == ["context", "inspect"]:
            return CommandResult(returncode=0 if args[3] in contexts else 1, stdout="", stderr="not found")
        return CommandResult(returncode=0, stdout="\n".join(contexts), stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    return calls


def test_compose_is_pinned_through_docker_context():
    integration = ComposeIntegration()

    assert integration.TARGET_CONTEXT_FIELD == "docker_context"
    assert integration.target_context_env("hearth-prd") == {DOCKER_CONTEXT_ENV: "hearth-prd"}


def test_a_docker_context_docker_knows_has_no_problem(monkeypatch):
    calls = _docker_knowing(monkeypatch, "default", "hearth-prd")

    assert ComposeIntegration().target_context_problem("hearth-prd", {}) is None
    assert calls == [["docker", "context", "inspect", "hearth-prd"]]


def test_a_docker_context_docker_does_not_know_names_the_ones_it_does(monkeypatch):
    _docker_knowing(monkeypatch, "default", "hearth-dev")

    assert ComposeIntegration().target_context_problem("hearth-prd", {}) == (
        "compose_docker_context_missing",
        "Docker has no such context (contexts: 'default', 'hearth-dev').",
    )


def test_docker_host_or_another_docker_context_is_a_conflict_without_asking_docker(monkeypatch):
    calls = _docker_knowing(monkeypatch, "hearth-prd")
    integration = ComposeIntegration()

    host = integration.target_context_problem("hearth-prd", {DOCKER_HOST_ENV: "ssh://deploy@hearth"})
    other = integration.target_context_problem("hearth-prd", {DOCKER_CONTEXT_ENV: "hearth-dev"})

    assert host is not None and host[0] == "compose_docker_context_conflict"
    assert other is not None and other[0] == "compose_docker_context_conflict"
    assert calls == []
    assert integration.target_context_problem("hearth-prd", {DOCKER_CONTEXT_ENV: "hearth-prd"}) is None


@pytest.mark.skipif(not shutil.which("docker"), reason="docker is not installed")
@pytest.mark.slow
def test_real_docker_resolves_contexts_the_way_the_pin_relies_on(tmp_path: Path):
    """`docker context inspect` fails for an unknown context, and DOCKER_HOST silently beats DOCKER_CONTEXT.

    The second is why a pinned deployment rejects DOCKER_HOST; a Docker release that changes either shows up here.
    Runs in a scratch DOCKER_CONFIG, so the user's own contexts are not touched.
    """
    environ = {k: v for k, v in os.environ.items() if k not in (DOCKER_HOST_ENV, DOCKER_CONTEXT_ENV)}
    environ["DOCKER_CONFIG"] = str(tmp_path)

    def docker(*args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["docker", *args], capture_output=True, text=True, env={**environ, **extra}, timeout=60)

    assert docker("context", "inspect", "strata-probe").returncode != 0
    assert docker("context", "create", "strata-probe", "--docker", "host=tcp://127.0.0.1:2").returncode == 0
    assert docker("context", "inspect", "strata-probe").returncode == 0
    assert docker("context", "show", DOCKER_CONTEXT="strata-probe").stdout.strip() == "strata-probe"
    shown = docker("context", "show", DOCKER_CONTEXT="strata-probe", DOCKER_HOST="tcp://127.0.0.1:1")
    assert shown.stdout.strip() == "default"


def test_referenced_secret_keys_scans_the_merged_compose_file(tmp_path: Path):
    """docs/design/deployment-documentation.md: the merged docker-compose.yml `deploy_namespace()` resolves."""
    namespace_dir = tmp_path / "hearth"
    (namespace_dir / "caddy").mkdir(parents=True)
    (namespace_dir / "docker-compose.yml").write_text(
        "services:\n  caddy:\n    environment:\n      TOKEN: ${secret:CADDY_TOKEN}\n      TZ: ${var:TZ}\n",
        encoding="utf-8",
    )
    caddy = _module("caddy", services=[ModuleServiceModel(name="caddy", image="caddy:2-alpine")])
    modules = [_resolved("caddy", caddy, namespace_dir / "caddy")]

    assert ComposeIntegration().referenced_secret_keys(_namespace("hearth"), modules) == {"CADDY_TOKEN"}


def test_referenced_secret_keys_without_the_merged_file_cannot_tell(tmp_path: Path):
    caddy = _module("caddy", services=[ModuleServiceModel(name="caddy", image="caddy:2-alpine")])
    modules = [_resolved("caddy", caddy, tmp_path / "hearth" / "caddy")]
    assert ComposeIntegration().referenced_secret_keys(_namespace("hearth"), modules) is None
