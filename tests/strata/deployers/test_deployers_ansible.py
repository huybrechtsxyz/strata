"""Unit tests for AnsibleDeployer."""

from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

from strata.deployers.ansible_deployer import AnsibleDeployer
from strata.models.common_models import ProvisionerType


def _make_stage(name="configure", provisioner=None, stage_type="configure"):
    stage = MagicMock()
    stage.name = name
    stage.provisioner = provisioner
    stage.type = stage_type
    return stage


def _make_workspace_service(provisioners=None):
    ws = MagicMock()
    ws.model.spec.provisioners = provisioners or []
    return ws


def _make_provisioner(name="ansible", ptype=ProvisionerType.ANSIBLE, source_path=None):
    p = MagicMock()
    p.name = name
    p.provisioner = ptype
    p.source = MagicMock()
    p.source.target_path = source_path  # None → falls back to "ansible/<name>"
    p.configuration = None
    return p


def _make_deployer(
    stage=None,
    provisioners=None,
    build_path: Optional[Path] = None,
    work_path: Optional[Path] = None,
    verbose: bool = False,
    force: bool = False,
) -> AnsibleDeployer:
    """Build an AnsibleDeployer backed by mock services."""
    if stage is None:
        stage = _make_stage()

    deployment_service = MagicMock()
    ws_service = _make_workspace_service(provisioners)
    deployment_service.get_workspace_service.return_value = ws_service

    configuration_service = MagicMock()
    bp = build_path or Path("/build")
    wp = work_path or Path("/work")

    return AnsibleDeployer(
        stage=stage,
        deployment_service=deployment_service,
        configuration_service=configuration_service,
        build_path=bp,
        work_path=wp,
        verbose=verbose,
        force=force,
    )


class TestAnsibleDeployerMetadata:
    def test_deployer_name(self):
        d = _make_deployer()
        assert d.get_deployer_name() == "ansible"

    def test_supported_steps(self):
        d = _make_deployer()
        steps = d.get_supported_steps()
        assert "setup" in steps
        assert "check" in steps
        assert "plan" in steps
        assert "apply" in steps
        assert "destroy" in steps
        assert "plan_destroy" in steps
        assert "show_plan" in steps
        assert "output" in steps


class TestAnsibleDeployerValidateWorkspace:
    def test_no_workspace_service_returns_false(self):
        d = _make_deployer()
        d.deployment_service.get_workspace_service.return_value = None
        ok, msgs = d.validate_workspace()
        assert ok is False
        assert any("No workspace service" in m for m in msgs)

    def test_no_matching_provisioner_returns_false(self):
        # Stage references a provisioner that doesn't exist
        stage = _make_stage(provisioner="nonexistent")
        d = _make_deployer(stage=stage, provisioners=[])
        ok, msgs = d.validate_workspace()
        assert ok is False
        assert any("no ansible provisioner" in m for m in msgs)

    def test_source_path_not_exists_returns_false(self):
        prov = _make_provisioner(source_path="ansible/")
        d = _make_deployer(provisioners=[prov])
        # Path doesn't exist on disk
        ok, msgs = d.validate_workspace()
        assert ok is False
        assert any("does not exist" in m for m in msgs)

    def test_valid_workspace_sets_working_dir(self, tmp_path):
        ansible_dir = tmp_path / "ansible"
        ansible_dir.mkdir()
        prov = _make_provisioner(source_path="ansible")
        d = _make_deployer(provisioners=[prov], build_path=tmp_path)
        ok, msgs = d.validate_workspace()
        assert ok is True
        assert d._working_dir == ansible_dir

    def test_convention_resolves_first_ansible_provisioner(self, tmp_path):
        """When stage.provisioner is None, picks the first ANSIBLE-typed provisioner."""
        ansible_dir = tmp_path / "playbooks"
        ansible_dir.mkdir()
        prov = _make_provisioner(name="my_ansible", source_path="playbooks")
        stage = _make_stage(provisioner=None)
        d = _make_deployer(stage=stage, provisioners=[prov], build_path=tmp_path)
        ok, msgs = d.validate_workspace()
        assert ok is True
        assert d._iac_model is prov


class TestAnsibleDeployerValidateEnvironment:
    def test_unavailable_returns_false(self):
        d = _make_deployer()
        with patch("strata.deployers.ansible_deployer.AnsibleIntegration") as mock_int:
            instance = MagicMock()
            instance.ensure_available.return_value = (False, "not installed")
            mock_int.return_value = instance
            ok, msgs = d.validate_environment()
        assert ok is False
        assert any("not installed" in m for m in msgs)

    def test_available_sets_ansible_instance(self):
        d = _make_deployer()
        with patch("strata.deployers.ansible_deployer.AnsibleIntegration") as mock_int:
            instance = MagicMock()
            instance.ensure_available.return_value = (True, "")
            instance.get_version.return_value = "2.15.4"
            mock_int.return_value = instance
            ok, msgs = d.validate_environment()
        assert ok is True
        assert d._ansible is instance


class TestAnsibleDeployerStepsNotReady:
    """Steps must fail gracefully if validate_* hasn't been called."""

    def test_setup_fails_when_not_ready(self):
        d = _make_deployer()
        ok, msgs = d.setup()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_check_fails_when_not_ready(self):
        d = _make_deployer()
        ok, msgs = d.check()
        assert ok is False

    def test_plan_fails_when_not_ready(self):
        d = _make_deployer()
        ok, msgs = d.plan()
        assert ok is False

    def test_apply_fails_when_not_ready(self):
        d = _make_deployer()
        ok, msgs = d.apply()
        assert ok is False

    def test_destroy_fails_when_not_ready(self):
        d = _make_deployer()
        ok, msgs = d.destroy()
        assert ok is False


class TestAnsibleDeployerSetup:
    def test_setup_skips_when_no_requirements(self, tmp_path):
        d = _make_deployer()
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        ok, msgs = d.setup()
        assert ok is True
        assert any("skipping" in m.lower() for m in msgs)

    def test_setup_calls_galaxy_install(self, tmp_path):
        (tmp_path / "requirements.yml").write_text("collections: []")
        d = _make_deployer()
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        d._ansible.init.return_value = {"returncode": 0, "stdout": "", "stderr": ""}
        ok, msgs = d.setup()
        assert ok is True
        d._ansible.init.assert_called_once()

    def test_setup_fails_on_galaxy_error(self, tmp_path):
        (tmp_path / "requirements.yml").write_text("collections: []")
        d = _make_deployer()
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        d._ansible.init.return_value = {"returncode": 1, "stdout": "", "stderr": "timeout"}
        ok, msgs = d.setup()
        assert ok is False
        assert any("failed" in m.lower() for m in msgs)


class TestAnsibleDeployerCheck:
    def test_check_calls_syntax_check(self, tmp_path):
        d = _make_deployer()
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        d._ansible.syntax_check.return_value = MagicMock(returncode=0, stderr="")
        ok, msgs = d.check()
        assert ok is True
        d._ansible.syntax_check.assert_called_once()

    def test_check_fails_on_syntax_error(self, tmp_path):
        d = _make_deployer()
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        d._ansible.syntax_check.return_value = MagicMock(returncode=4, stderr="syntax error")
        ok, msgs = d.check()
        assert ok is False


class TestAnsibleDeployerPlan:
    def test_plan_calls_check_mode(self, tmp_path):
        d = _make_deployer()
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        d._ansible.plan.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, msgs = d.plan()
        assert ok is True
        d._ansible.plan.assert_called_once()

    def test_plan_fails_on_nonzero(self, tmp_path):
        d = _make_deployer()
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        d._ansible.plan.return_value = MagicMock(returncode=2, stdout="", stderr="host unreachable")
        ok, msgs = d.plan()
        assert ok is False


class TestAnsibleDeployerApply:
    def test_apply_runs_playbook(self, tmp_path):
        d = _make_deployer()
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        d._ansible.apply.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        ok, msgs = d.apply()
        assert ok is True
        d._ansible.apply.assert_called_once()


class TestAnsibleDeployerDestroy:
    def test_destroy_requires_force(self, tmp_path):
        d = _make_deployer(force=False)
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        ok, msgs = d.destroy()
        assert ok is False
        assert any("--force" in m for m in msgs)

    def test_destroy_requires_destroy_playbook(self, tmp_path):
        d = _make_deployer(force=True)
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        ok, msgs = d.destroy()
        assert ok is False
        assert any("not found" in m.lower() for m in msgs)

    def test_destroy_runs_destroy_playbook(self, tmp_path):
        (tmp_path / "destroy.yml").write_text("- hosts: all")
        d = _make_deployer(force=True)
        d._working_dir = tmp_path
        d._ansible = MagicMock()
        d._ansible.apply.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, msgs = d.destroy()
        assert ok is True
        d._ansible.apply.assert_called_once()


class TestAnsibleDeployerOutputShowPlan:
    def test_output_returns_empty_dict(self):
        d = _make_deployer()
        ok, data, msgs = d.output()
        assert ok is True
        assert data == {}

    def test_show_plan_returns_empty_dict(self):
        d = _make_deployer()
        ok, data, msgs = d.show_plan()
        assert ok is True
        assert data == {}


class TestAnsibleDeployerHelpers:
    def test_get_playbook_default(self):
        d = _make_deployer()
        d._iac_model = MagicMock()
        d._iac_model.configuration = None
        assert d._get_playbook() == "site.yml"

    def test_get_playbook_from_configuration(self):
        d = _make_deployer()
        d._iac_model = MagicMock()
        d._iac_model.configuration = {"playbook": "deploy.yml"}
        assert d._get_playbook() == "deploy.yml"

    def test_get_inventory_from_configuration(self):
        d = _make_deployer()
        d._iac_model = MagicMock()
        d._iac_model.configuration = {"inventory": "hosts.yml"}
        d._working_dir = Path("/nonexistent")
        assert d._get_inventory() == "hosts.yml"

    def test_get_inventory_auto_discovers(self, tmp_path):
        (tmp_path / "inventory.yml").write_text("")
        d = _make_deployer()
        d._iac_model = MagicMock()
        d._iac_model.configuration = None
        d._working_dir = tmp_path
        assert d._get_inventory() == "inventory.yml"

    def test_get_inventory_returns_none_when_missing(self, tmp_path):
        d = _make_deployer()
        d._iac_model = MagicMock()
        d._iac_model.configuration = None
        d._working_dir = tmp_path
        assert d._get_inventory() is None

    def test_get_extra_vars_from_configuration(self):
        d = _make_deployer()
        d._iac_model = MagicMock()
        d._iac_model.configuration = {"extra_vars": {"env": "prod", "version": 2}}
        ev = d._get_extra_vars()
        assert ev == {"env": "prod", "version": "2"}

    def test_get_extra_vars_returns_none_when_absent(self):
        d = _make_deployer()
        d._iac_model = MagicMock()
        d._iac_model.configuration = None
        assert d._get_extra_vars() is None

    def test_get_requirements_file_discovers(self, tmp_path):
        (tmp_path / "requirements.yml").write_text("")
        d = _make_deployer()
        d._working_dir = tmp_path
        assert d._get_requirements_file() == "requirements.yml"

    def test_get_requirements_file_checks_collections_dir(self, tmp_path):
        (tmp_path / "collections").mkdir()
        (tmp_path / "collections" / "requirements.yml").write_text("")
        d = _make_deployer()
        d._working_dir = tmp_path
        assert d._get_requirements_file() == "collections/requirements.yml"


class TestAnsibleDeployerSshKey:
    def test_ssh_key_context_yields_none_when_no_key(self, tmp_path):
        d = _make_deployer()
        d._working_dir = tmp_path
        d._iac_model = MagicMock()
        d._iac_model.configuration = None
        with d._ssh_key_context() as key_file:
            assert key_file is None

    def test_ssh_key_context_writes_and_cleans_up(self, tmp_path):
        import os
        import sys

        from strata.controllers.value_controller import ResolvedValues

        rv = ResolvedValues(secrets={"ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n"})
        d = _make_deployer()
        d.resolved_values = rv
        d._iac_model = MagicMock()
        d._iac_model.configuration = None
        captured = {}
        with d._ssh_key_context() as key_file:
            assert key_file is not None
            assert os.path.exists(key_file)
            # chmod 600 is meaningful only on POSIX — Windows ignores it
            if sys.platform != "win32":
                assert oct(os.stat(key_file).st_mode)[-3:] == "600"
            captured["path"] = key_file
        # File must be deleted after context exits
        assert not os.path.exists(captured["path"])

    def test_ssh_key_context_uses_custom_secret_name(self):
        from strata.controllers.value_controller import ResolvedValues

        rv = ResolvedValues(secrets={"haven_ssh_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n"})
        d = _make_deployer()
        d.resolved_values = rv
        d._iac_model = MagicMock()
        d._iac_model.configuration = {"ssh_private_key_secret": "haven_ssh_key"}
        with d._ssh_key_context() as key_file:
            assert key_file is not None
