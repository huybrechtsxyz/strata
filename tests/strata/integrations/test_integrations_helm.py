#!/usr/bin/env python3
"""Tests for `HelmIntegration` (ADR-0021 Phase 6)."""

from pathlib import Path

import pytest

from strata.integrations.helm import HelmIntegration
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
