"""Tests for BicepDeployer — all az CLI calls mocked."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    from strata.deployers.bicep_deployer import BicepDeployer, _SCOPE_CMD
    from strata.models.common_models import ProvisionerType

    IMPL_MISSING = False
except ImportError:
    BicepDeployer = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="BicepDeployer not available")


# ===========================================================================
# Helpers
# ===========================================================================

def _make_deployer(tmp_path: Path, config: dict = None) -> BicepDeployer:
    """Build a BicepDeployer with minimal mocked services."""
    stage = MagicMock()
    stage.name = "infra"
    stage.provisioner = "infrastructure"
    stage.topology = None
    stage.timeouts = None

    dep_svc = MagicMock()
    dep_svc.get_workspace_service.return_value = MagicMock()
    cfg_svc = MagicMock()

    d = BicepDeployer(
        stage=stage,
        deployment_service=dep_svc,
        configuration_service=cfg_svc,
        build_path=tmp_path / "build",
        work_path=tmp_path,
        verbose=False,
        force=False,
    )
    # Pre-populate internals as if validate_workspace/validate_environment passed
    iac = MagicMock()
    iac.name = "infrastructure"
    iac.configuration = config or {
        "scope": "resourceGroup",
        "resource_group": "my-rg",
        "deployment_name": "strata-infra",
    }
    d._iac_model = iac
    d._working_dir = tmp_path / "bicep"
    d._az = MagicMock()
    return d


def _ok(stdout: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def _fail(stderr: str = "ERROR") -> MagicMock:
    r = MagicMock()
    r.returncode = 1
    r.stdout = ""
    r.stderr = stderr
    return r


# ===========================================================================
# ProvisionerType.BICEP enum
# ===========================================================================

class TestProvisionerType:
    def test_bicep_in_enum(self):
        assert ProvisionerType.BICEP == "bicep"
        assert ProvisionerType("bicep") == ProvisionerType.BICEP


# ===========================================================================
# _scope_cmd mapping
# ===========================================================================

class TestScopeCmdMapping:
    def test_all_scopes_present(self):
        assert _SCOPE_CMD["resourceGroup"] == "group"
        assert _SCOPE_CMD["subscription"] == "sub"
        assert _SCOPE_CMD["managementGroup"] == "mg"
        assert _SCOPE_CMD["tenant"] == "tenant"


# ===========================================================================
# validate_workspace
# ===========================================================================

class TestValidateWorkspace:
    def test_fails_when_no_workspace_service(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._iac_model = None
        d.deployment_service.get_workspace_service.return_value = None

        with patch.object(d, "_resolve_iac_model", return_value=None):
            ok, msgs = d.validate_workspace()

        assert not ok
        assert any("workspace service" in m.lower() or "bicep provisioner" in m.lower() for m in msgs)

    def test_fails_when_no_bicep_files(self, tmp_path):
        d = _make_deployer(tmp_path)
        (tmp_path / "bicep").mkdir()  # dir exists but no .bicep files
        d._working_dir = tmp_path / "bicep"

        ok, msgs = d._check_working_dir()

        assert not ok
        assert any(".bicep" in m for m in msgs)

    def test_passes_when_bicep_files_exist(self, tmp_path):
        d = _make_deployer(tmp_path)
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("param name string")
        d._working_dir = tmp_path / "bicep"

        ok, _ = d._check_working_dir()

        assert ok


# ===========================================================================
# validate_environment
# ===========================================================================

class TestValidateEnvironment:
    def test_fails_when_az_not_available(self, tmp_path):
        d = _make_deployer(tmp_path)
        with patch("strata.integrations.azure_cli.AzureCLIIntegration.ensure_available",
                   return_value=(False, "not logged in")), \
             patch("strata.integrations.azure_cli.AzureCLIIntegration.is_available",
                   return_value=True):
            ok, msgs = d.validate_environment()
        assert not ok
        assert "not logged in" in msgs[0]

    def test_passes_when_az_available(self, tmp_path):
        d = _make_deployer(tmp_path)
        with patch("strata.integrations.azure_cli.AzureCLIIntegration.ensure_available",
                   return_value=(True, "")), \
             patch("strata.integrations.azure_cli.AzureCLIIntegration.is_available",
                   return_value=True):
            ok, _ = d.validate_environment()
        assert ok


# ===========================================================================
# setup / check
# ===========================================================================

class TestSetup:
    def test_calls_az_bicep_build(self, tmp_path):
        d = _make_deployer(tmp_path)
        (tmp_path / "bicep").mkdir()
        main = tmp_path / "bicep" / "main.bicep"
        main.write_text("param env string")

        d._az.run_az.return_value = _ok()
        ok, msgs = d.setup()

        assert ok
        call_args = d._az.run_az.call_args[0][0]
        assert "bicep" in call_args
        assert "build" in call_args

    def test_fails_when_build_fails(self, tmp_path):
        d = _make_deployer(tmp_path)
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("INVALID")

        d._az.run_az.return_value = _fail("syntax error")
        ok, msgs = d.setup()

        assert not ok
        assert any("build failed" in m for m in msgs)

    def test_fails_when_no_template(self, tmp_path):
        d = _make_deployer(tmp_path)
        (tmp_path / "bicep").mkdir()  # empty — no .bicep files
        ok, msgs = d.setup()
        assert not ok
        assert any("main.bicep" in m for m in msgs)


# ===========================================================================
# plan (what-if)
# ===========================================================================

class TestPlan:
    def test_calls_what_if(self, tmp_path):
        d = _make_deployer(tmp_path)
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("param x string")

        d._az.run_az.return_value = _ok('{"status": "Succeeded"}')
        ok, _ = d.plan()

        assert ok
        call_args = d._az.run_az.call_args[0][0]
        assert "what-if" in call_args
        assert "--resource-group" in call_args
        assert "my-rg" in call_args

    def test_caches_what_if_result(self, tmp_path):
        d = _make_deployer(tmp_path)
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("")

        payload = {"status": "Succeeded", "changes": []}
        d._az.run_az.return_value = _ok(json.dumps(payload))
        d.plan()

        assert d._last_whatif == payload

    def test_subscription_scope_uses_location(self, tmp_path):
        d = _make_deployer(tmp_path, config={
            "scope": "subscription",
            "location": "westeurope",
        })
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("")

        d._az.run_az.return_value = _ok("{}")
        d.plan()

        call_args = d._az.run_az.call_args[0][0]
        assert "sub" in call_args
        assert "--location" in call_args
        assert "westeurope" in call_args


# ===========================================================================
# apply
# ===========================================================================

class TestApply:
    def test_calls_deployment_create(self, tmp_path):
        d = _make_deployer(tmp_path)
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("")

        d._az.run_az.return_value = _ok('{"id": "/subscriptions/..."}')
        ok, msgs = d.apply()

        assert ok
        call_args = d._az.run_az.call_args[0][0]
        assert "create" in call_args
        assert "group" in call_args
        assert "--resource-group" in call_args
        assert "strata-infra" in call_args  # deployment_name
        assert any("deployed successfully" in m for m in msgs)

    def test_fails_on_az_error(self, tmp_path):
        d = _make_deployer(tmp_path)
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("")

        d._az.run_az.return_value = _fail("AuthorizationFailed")
        ok, msgs = d.apply()

        assert not ok
        assert any("failed" in m for m in msgs)

    def test_parameters_file_included(self, tmp_path):
        d = _make_deployer(tmp_path, config={
            "scope": "resourceGroup",
            "resource_group": "rg",
            "parameters_file": "params.json",
        })
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("")
        (tmp_path / "bicep" / "params.json").write_text('{}')

        d._az.run_az.return_value = _ok("{}")
        d.apply()

        call_args = d._az.run_az.call_args[0][0]
        assert "--parameters" in call_args

    def test_parameters_file_skipped_if_missing(self, tmp_path):
        d = _make_deployer(tmp_path, config={
            "scope": "resourceGroup",
            "resource_group": "rg",
            "parameters_file": "nonexistent.json",
        })
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("")

        d._az.run_az.return_value = _ok("{}")
        d.apply()

        call_args = d._az.run_az.call_args[0][0]
        assert "--parameters" not in call_args


# ===========================================================================
# destroy
# ===========================================================================

class TestDestroy:
    def test_calls_deployment_delete(self, tmp_path):
        d = _make_deployer(tmp_path)
        d.force = True
        d._az.run_az.return_value = _ok()
        ok, msgs = d.destroy()

        assert ok
        call_args = d._az.run_az.call_args[0][0]
        assert "delete" in call_args
        assert "strata-infra" in call_args
        assert "--yes" in call_args

    def test_no_yes_when_force_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d.force = False
        d._az.run_az.return_value = _ok()
        d.destroy()

        call_args = d._az.run_az.call_args[0][0]
        assert "--yes" not in call_args


# ===========================================================================
# output
# ===========================================================================

class TestOutput:
    def test_parses_arm_outputs(self, tmp_path):
        d = _make_deployer(tmp_path)
        arm_outputs = {
            "storageAccountName": {"type": "String", "value": "mystorage"},
            "connectionString": {"type": "SecureString", "value": "secret"},
        }
        d._az.run_az.return_value = _ok(json.dumps(arm_outputs))

        ok, outputs, _ = d.output()

        assert ok
        assert outputs["storageAccountName"] == "mystorage"
        assert outputs["connectionString"] == "secret"

    def test_returns_empty_on_failure(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._az.run_az.return_value = _fail()
        ok, outputs, _ = d.output()
        assert not ok
        assert outputs == {}


# ===========================================================================
# show_plan
# ===========================================================================

class TestShowPlan:
    def test_returns_last_whatif(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._last_whatif = {"status": "Succeeded"}
        ok, data, _ = d.show_plan()
        assert ok
        assert data["status"] == "Succeeded"

    def test_fails_when_no_plan_run(self, tmp_path):
        d = _make_deployer(tmp_path)
        ok, data, msgs = d.show_plan()
        assert not ok
        assert any("what-if" in m.lower() or "plan" in m.lower() for m in msgs)


# ===========================================================================
# _deployment_cmd scope routing
# ===========================================================================

class TestDeploymentCmd:
    def test_resource_group_scope(self, tmp_path):
        d = _make_deployer(tmp_path, config={"scope": "resourceGroup", "resource_group": "my-rg"})
        cmd = d._deployment_cmd("resourceGroup", "create")
        assert cmd[:3] == ["deployment", "group", "create"]
        assert "--resource-group" in cmd
        assert "my-rg" in cmd

    def test_subscription_scope(self, tmp_path):
        d = _make_deployer(tmp_path, config={"scope": "subscription", "location": "eastus"})
        cmd = d._deployment_cmd("subscription", "create")
        assert "sub" in cmd
        assert "--location" in cmd and "eastus" in cmd

    def test_management_group_scope(self, tmp_path):
        d = _make_deployer(tmp_path, config={
            "scope": "managementGroup",
            "location": "westeurope",
            "management_group_id": "mg-root",
        })
        cmd = d._deployment_cmd("managementGroup", "create")
        assert "mg" in cmd
        assert "--management-group-id" in cmd
        assert "mg-root" in cmd

    def test_invalid_scope_defaults_to_resource_group(self, tmp_path):
        d = _make_deployer(tmp_path, config={"scope": "invalid"})
        assert d._scope() == "resourceGroup"
