#!/usr/bin/env python3
"""Tests for `OpenTofuIntegration` (ADR-0060, docs/design/opentofu.md).

OpenTofu takes Terraform's CLI contract unchanged, so the integration is a subclass that differs in its type and its
binary only. These tests pin that: the same argv after the binary, the same environment, the same generated files.
"""

from pathlib import Path

import pytest

from strata.integrations.errors import IntegrationError
from strata.integrations.registry import get, get_class
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueResolution
from strata.integrations.terraform import OpenTofuIntegration, TerraformIntegration
from strata.models.common_models import SourceModel
from strata.models.integration_model import IntegrationModel
from strata.models.provider_model import ProviderMetaModel, ProviderModel, ProviderSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel
from strata.utils.transport import CommandResult


def _capture(monkeypatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append({"args": list(args), "env": env, "cwd": cwd})
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    return calls


def test_class_declares_its_contract():
    assert OpenTofuIntegration.TYPE == "opentofu"
    assert OpenTofuIntegration.COMMAND == "tofu"
    assert OpenTofuIntegration.CAPABILITIES == TerraformIntegration.CAPABILITIES
    assert OpenTofuIntegration.TRANSPORTS == TerraformIntegration.TRANSPORTS
    assert OpenTofuIntegration.ENV_VAR_PREFIX == "TF_VAR_"
    assert OpenTofuIntegration.VERSION_ARGS == ("version",)


def test_registered_in_the_registry_and_no_longer_a_v1_leftover():
    assert get_class("opentofu") is OpenTofuIntegration
    assert isinstance(get("opentofu"), OpenTofuIntegration)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("OpenTofu v1.10.6\non linux_amd64", "1.10.6"), ("OpenTofu v1.6.0", "1.6.0"), ("garbage", "garbage")],
)
def test_parse_version_reads_opentofus_own_numbering(raw, expected):
    assert OpenTofuIntegration().parse_version(raw) == expected


def test_a_terraform_document_cannot_configure_it():
    """The constructor's type check: a `type: terraform` document never runs as OpenTofu, nor the reverse."""
    document = IntegrationModel.model_validate(
        {"meta": {"name": "tf"}, "spec": {"type": "terraform", "capabilities": ["infrastructure"]}}
    )
    with pytest.raises(IntegrationError, match="registered for type 'opentofu'"):
        OpenTofuIntegration(document)


def test_spec_command_still_overrides_the_binary():
    document = IntegrationModel.model_validate(
        {
            "meta": {"name": "tofu"},
            "spec": {"type": "opentofu", "capabilities": ["infrastructure"], "command": "/opt/tofu/tofu"},
        }
    )
    assert OpenTofuIntegration(document).command == "/opt/tofu/tofu"


_WORK = Path("/work")

#: Every CLI call strata makes, with representative arguments.
_CALLS = {
    "init": lambda i: i.init(_WORK, backend_config={"key": "prd.tfstate"}, upgrade=True, reconfigure=True),
    "validate": lambda i: i.validate(_WORK, json_output=True),
    "plan": lambda i: i.plan(
        _WORK, var_file="prd.tfvars", variables={"region": "westeurope"}, out_file="prd.tfplan", target=["module.db"]
    ),
    "plan-destroy": lambda i: i.plan(_WORK, destroy=True, detailed_exitcode=True),
    "apply": lambda i: i.deploy(_WORK, plan_file="prd.tfplan", auto_approve=True),
    "apply-unsaved": lambda i: i.deploy(_WORK, var_file="prd.tfvars", target=["module.db"]),
    "destroy": lambda i: i.destroy(_WORK, auto_approve=True, variables={"region": "westeurope"}),
    "output": lambda i: i.output(_WORK, output_name="endpoint", json_format=True, raw=False),
    "show": lambda i: i.show(_WORK, plan_file="prd.tfplan"),
}


@pytest.mark.parametrize("call", sorted(_CALLS))
def test_every_call_is_terraforms_argv_with_tofu_as_the_binary(monkeypatch, call):
    calls = _capture(monkeypatch)
    _CALLS[call](TerraformIntegration())
    _CALLS[call](OpenTofuIntegration())
    terraform, tofu = calls
    assert terraform["args"][0] == "terraform" and tofu["args"][0] == "tofu"
    assert tofu["args"][1:] == terraform["args"][1:]


def test_secrets_reach_tofu_as_tf_var_environment_variables(monkeypatch):
    calls = _capture(monkeypatch)
    OpenTofuIntegration().deploy(_WORK, env={"TF_VAR_db_password": "hunter2"}, auto_approve=True)
    [call] = calls
    assert call["env"]["TF_VAR_db_password"] == "hunter2"
    assert "hunter2" not in " ".join(call["args"])


def _graph() -> ResolvedWorkspaceGraph:
    return ResolvedWorkspaceGraph(
        workspace=WorkspaceModel(
            meta=WorkspaceMetaModel(name="ws"),
            spec=WorkspaceSpecModel(
                providers=["p1"],
                provisioners=[ProvisionerModel(name="prov", tool="opentofu", source=SourceModel(source_path="infra"))],
            ),
        ),
        providers={
            "p1": ProviderModel(meta=ProviderMetaModel(name="p1"), spec=ProviderSpecModel(type="local", region="x"))
        },
    )


def test_the_generated_files_are_terraforms(tmp_path):
    provisioner = ProvisionerModel(name="prov", tool="opentofu", source=SourceModel(source_path="infra"))
    resolution = ValueResolution(deployment="d")
    tofu = OpenTofuIntegration().default_output(tmp_path, resolution, provisioner, _graph())
    terraform = TerraformIntegration().default_output(tmp_path, resolution, provisioner, _graph())
    assert tofu == terraform
    assert "strata_variables.tf" in tofu
