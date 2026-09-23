#!/usr/bin/env python3
"""Tests for `ComposeIntegration` (ADR-0021 Phase 6)."""

from pathlib import Path

import pytest

from strata.integrations.compose import ComposeIntegration
from strata.integrations.registry import get
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
