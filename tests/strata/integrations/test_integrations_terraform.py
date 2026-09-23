#!/usr/bin/env python3
"""Tests for `TerraformIntegration` (ADR-0021 Phase 5)."""

from pathlib import Path

import pytest

from strata.integrations.registry import get
from strata.integrations.terraform import TerraformIntegration
from strata.utils.transport import CommandResult


def _capture(monkeypatch):
    """Stub `run_command` where `Integration.run()` actually looks it up
    (`strata.integrations.base`), returning the captured argv via a mutable box."""
    captured: dict[str, object] = {}

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["env"] = env
        captured["timeout"] = timeout
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    return captured


def test_class_declares_its_contract():
    assert TerraformIntegration.TYPE == "terraform"
    assert TerraformIntegration.CAPABILITIES == {"infrastructure"}
    assert TerraformIntegration.TRANSPORTS == {"cli"}
    assert TerraformIntegration.COMMAND == "terraform"


def test_registered_in_the_registry():
    instance = get("terraform")
    assert isinstance(instance, TerraformIntegration)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Terraform v1.7.0\non linux_amd64", "1.7.0"),
        ("Terraform v1.9.8", "1.9.8"),
        ("garbage output", "garbage output"),
    ],
)
def test_parse_version(raw, expected):
    assert TerraformIntegration().parse_version(raw) == expected


def test_init_argv(monkeypatch):
    captured = _capture(monkeypatch)
    tf = TerraformIntegration()
    tf.init(Path("/work"), backend_config={"bucket": "my-bucket"}, upgrade=True, reconfigure=True)
    assert captured["args"] == [
        "terraform",
        "init",
        "-backend-config",
        "bucket=my-bucket",
        "-upgrade",
        "-reconfigure",
    ]
    assert captured["cwd"] == Path("/work")


def test_validate_argv(monkeypatch):
    captured = _capture(monkeypatch)
    TerraformIntegration().validate(Path("/work"), json_output=True)
    assert captured["args"] == ["terraform", "validate", "-json"]


def test_plan_argv_with_var_file_and_out(monkeypatch):
    captured = _capture(monkeypatch)
    TerraformIntegration().plan(Path("/work"), var_file="prd.tfvars", out_file="prd.tfplan")
    assert captured["args"] == ["terraform", "plan", "-var-file", "prd.tfvars", "-out", "prd.tfplan"]


def test_plan_argv_with_variables_and_target(monkeypatch):
    captured = _capture(monkeypatch)
    TerraformIntegration().plan(Path("/work"), variables={"region": "westeurope"}, target=["module.db"])
    assert captured["args"] == ["terraform", "plan", "-var", "region=westeurope", "-target", "module.db"]


def test_plan_destroy_is_plan_with_destroy_flag(monkeypatch):
    """v1's 'plan_destroy' deployer step — not a separate method."""
    captured = _capture(monkeypatch)
    TerraformIntegration().plan(Path("/work"), destroy=True)
    assert captured["args"] == ["terraform", "plan", "-destroy"]


def test_drift_is_plan_with_detailed_exitcode(monkeypatch):
    """v1's 'drift' deployer step — not a separate method. Exit code 2 means
    'changes present', not failure; caller must read returncode directly."""
    captured = _capture(monkeypatch)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        captured["args"] = args
        return CommandResult(returncode=2, stdout="", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)

    result = TerraformIntegration().plan(Path("/work"), detailed_exitcode=True)
    assert captured["args"] == ["terraform", "plan", "-detailed-exitcode"]
    assert result.returncode == 2
    assert result.is_successful is False  # unchanged — caller reads returncode, not is_successful


def test_deploy_with_plan_file_ignores_variables(monkeypatch):
    captured = _capture(monkeypatch)
    TerraformIntegration().deploy(
        Path("/work"), plan_file="prd.tfplan", auto_approve=True, variables={"ignored": "yes"}
    )
    assert captured["args"] == ["terraform", "apply", "-auto-approve", "prd.tfplan"]


def test_deploy_without_plan_file_uses_variables(monkeypatch):
    captured = _capture(monkeypatch)
    TerraformIntegration().deploy(Path("/work"), var_file="prd.tfvars", target=["module.db"])
    assert captured["args"] == ["terraform", "apply", "-var-file", "prd.tfvars", "-target", "module.db"]


def test_destroy_argv(monkeypatch):
    captured = _capture(monkeypatch)
    TerraformIntegration().destroy(Path("/work"), auto_approve=True, variables={"region": "westeurope"})
    assert captured["args"] == ["terraform", "destroy", "-auto-approve", "-var", "region=westeurope"]


def test_output_argv(monkeypatch):
    captured = _capture(monkeypatch)
    TerraformIntegration().output(Path("/work"), output_name="endpoint", json_format=True, raw=False)
    assert captured["args"] == ["terraform", "output", "-json", "endpoint"]


def test_show_argv_defaults_to_json(monkeypatch):
    captured = _capture(monkeypatch)
    TerraformIntegration().show(Path("/work"), plan_file="prd.tfplan")
    assert captured["args"] == ["terraform", "show", "-json", "prd.tfplan"]


def test_env_is_forwarded_not_injected_as_var_flags(monkeypatch):
    """Secrets travel as TF_VAR_ env vars, never -var argv (ADR-0021 Phase 5 design note)."""
    captured = _capture(monkeypatch)
    TerraformIntegration().deploy(Path("/work"), env={"TF_VAR_db_password": "hunter2"}, auto_approve=True)
    assert "hunter2" not in captured["args"]
    assert "-var" not in captured["args"]
    assert captured["env"] == {"TF_VAR_db_password": "hunter2"}
