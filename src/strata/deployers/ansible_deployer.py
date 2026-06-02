"""Ansible deployer — step-based provisioner for configuration management.

Supported steps (in execution order):
  setup    — ansible-galaxy collection install -r requirements.yml
  check    — ansible-playbook --syntax-check
  plan     — ansible-playbook --check --diff
  apply    — ansible-playbook
  destroy  — ansible-playbook (destroy playbook, requires force=True)
  output   — returns empty dict (no structured output)

Typical caller sequences:
  dry-run  : setup → check → plan
  deploy   : setup → check → plan → apply
  destroy  : setup → destroy  (force=True required)

``line_callback`` parameter (step methods):
  Optional ``Callable[[str, str], None]`` — called for each subprocess output
  line as it arrives.  First arg is the stream name (``"stdout"`` / ``"stderr"``),
  second is the raw text line.
"""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from strata.controllers.value_controller import ResolvedValues, inject_compose_env
from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_OUTPUT,
    STEP_PLAN,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
    STEP_SHOW_PLAN,
    BaseDeployer,
)
from strata.integrations.ansible import AnsibleIntegration
from strata.models.integration_model import IntegrationModel
from strata.models.workspace_model import WorkspaceIacModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService

try:
    from strata.models.deployment_model import DeploymentStageModel
except ImportError:  # pragma: no cover
    pass


class AnsibleDeployer(BaseDeployer):
    """Runs a deployment stage using Ansible (galaxy install → syntax-check → check → apply).

    Context is passed once to the constructor; step methods carry no arguments
    besides the optional line_callback.
    Call validate_workspace() then validate_environment() before running steps.
    """

    def __init__(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        configuration_service: "ConfigurationService",
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
        resolved_values: Optional[ResolvedValues] = None,
        solution_controller=None,
    ) -> None:
        super().__init__(
            stage=stage,
            deployment_service=deployment_service,
            configuration_service=configuration_service,
            build_path=build_path,
            work_path=work_path,
            verbose=verbose,
            force=force,
            solution_controller=solution_controller,
        )
        self.resolved_values = resolved_values
        self._iac_model: Optional[WorkspaceIacModel] = None
        self._working_dir: Optional[Path] = None
        self._ansible: Optional[AnsibleIntegration] = None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_deployer_name(self) -> str:
        return "ansible"

    def get_supported_steps(self) -> List[str]:
        return [
            STEP_SETUP,
            STEP_CHECK,
            STEP_PLAN,
            STEP_APPLY,
            STEP_DESTROY,
            STEP_PLAN_DESTROY,
            STEP_SHOW_PLAN,
            STEP_OUTPUT,
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_workspace(self) -> Tuple[bool, List[str]]:
        """Verify the workspace has a matching IaC entry and playbook source exists."""
        messages: List[str] = []

        workspace_service = self.deployment_service.get_workspace_service()
        if workspace_service is None:
            messages.append("No workspace service available")
            return False, messages

        spec = workspace_service.model.spec  # type: ignore[union-attr]
        provisioners = spec.provisioners or []

        # Find the ansible IaC entry matching the stage provisioner name
        iac: Optional[WorkspaceIacModel] = None
        if self.stage.provisioner:
            iac = next((p for p in provisioners if p.name == self.stage.provisioner), None)
        else:
            # Convention: first ansible provisioner
            from strata.models.common_models import ProvisionerType

            iac = next((p for p in provisioners if p.provisioner == ProvisionerType.ANSIBLE), None)

        if iac is None:
            messages.append(
                f"Stage '{self.stage.name}': no ansible provisioner found in workspace "
                f"(looked for provisioner='{self.stage.provisioner}')."
            )
            return False, messages

        # Resolve working directory from source path via canonical helper
        if self.solution_controller is not None:
            source_path = self.solution_controller.get_provisioner_path(self.deployment_service, self.build_path, iac)
        else:
            target = Path(iac.source.target_path) if iac.source.target_path else (Path("ansible") / iac.name)
            source_path = self.build_path / target
        if not source_path.exists():
            messages.append(f"Ansible source path does not exist: {source_path}")
            return False, messages

        self._iac_model = iac
        self._working_dir = source_path
        messages.append(f"Ansible workspace validated: {source_path}")
        return True, messages

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Verify ansible-playbook is available on PATH."""
        messages: List[str] = []

        # Create a minimal IntegrationModel for the ansible integration
        config = IntegrationModel(
            name="ansible",
            type="ansible",
        )
        ansible = AnsibleIntegration(config=config)
        available, error = ansible.ensure_available()
        if not available:
            messages.append(error)
            return False, messages

        self._ansible = ansible
        messages.append(f"ansible-playbook {ansible.get_version()} available")
        return True, messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ready(self, messages: List[str]) -> bool:
        """Guard: workspace + environment must be validated first."""
        if self._working_dir is None or self._ansible is None:
            messages.append("Deployer not initialized — call validate_workspace/validate_environment first.")
            return False
        return True

    def describe_plan(self) -> List[str]:
        """Return the resolved playbook and inventory paths for dry-run display."""
        if self._working_dir is None:
            return []
        playbook = self._get_playbook()
        inventory = self._get_inventory()
        lines = [f"playbook:   {self._working_dir / playbook}"]
        if inventory:
            lines.append(f"inventory:  {self._working_dir / inventory}")
        else:
            lines.append("inventory:  (none — Ansible will use its default discovery)")
        return lines

    def _get_configuration(self) -> Optional[Dict[str, Any]]:
        """Return the provisioner configuration dict, or None."""
        if self._iac_model is not None and self._iac_model.configuration:
            return self._iac_model.configuration
        return None

    def _get_playbook(self) -> str:
        """Resolve the playbook filename from configuration or default to site.yml."""
        cfg = self._get_configuration()
        if cfg and "playbook" in cfg:
            return cfg["playbook"]
        return "site.yml"

    def _get_inventory(self) -> Optional[str]:
        """Resolve the inventory path from configuration or auto-discover."""
        cfg = self._get_configuration()
        if cfg and "inventory" in cfg:
            return cfg["inventory"]
        # Auto-discover common inventory filenames in the working directory
        if self._working_dir:
            for candidate in ("inventory", "inventory.yml", "hosts.yml", "hosts"):
                if (self._working_dir / candidate).exists():
                    return candidate
        return None

    def _get_extra_vars(self) -> Optional[Dict[str, str]]:
        """Return extra_vars from configuration as a flat str->str dict."""
        cfg = self._get_configuration()
        if cfg and "extra_vars" in cfg:
            ev = cfg["extra_vars"]
            if isinstance(ev, dict):
                return {k: str(v) for k, v in ev.items()}
        return None

    def _get_requirements_file(self) -> Optional[str]:
        """Resolve the Galaxy requirements file if present."""
        if self._working_dir:
            for candidate in ("requirements.yml", "collections/requirements.yml"):
                if (self._working_dir / candidate).exists():
                    return candidate
        return None

    def _get_ssh_key_content(self) -> Optional[str]:
        """Look up SSH private key content from resolved_values.secrets.

        The secret name defaults to ``ssh_private_key`` but can be overridden
        via ``configuration["ssh_private_key_secret"]``.
        """
        if not self.resolved_values:
            return None
        cfg = self._get_configuration()
        secret_name = cfg.get("ssh_private_key_secret", "ssh_private_key") if cfg else "ssh_private_key"
        return self.resolved_values.secrets.get(secret_name)

    @contextmanager
    def _ssh_key_context(self) -> Generator[Optional[str], None, None]:
        """Write SSH private key to a temp file (chmod 600), yield its path, delete on exit."""
        key_content = self._get_ssh_key_content()
        if not key_content:
            yield None
            return
        fd, key_path = tempfile.mkstemp(suffix=".pem", prefix="ansible_ssh_")
        try:
            os.close(fd)
            with open(key_path, "w") as f:
                f.write(key_content)
            os.chmod(key_path, 0o600)
            yield key_path
        finally:
            try:
                os.unlink(key_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Step methods
    # ------------------------------------------------------------------

    def setup(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Install Galaxy collections/roles from requirements.yml."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._ansible is not None

        requirements = self._get_requirements_file()
        if requirements is None:
            messages.append("No requirements.yml found — skipping Galaxy install.")
            return True, messages

        messages.append(f"ansible-galaxy install -r {requirements}")
        try:
            result = self._ansible.init(
                str(self._working_dir),
                requirements_file=requirements,
                timeout=self._get_timeout("setup", 300),
            )
            if result.get("returncode", 0) != 0:
                messages.append(f"ansible-galaxy install failed:\n{result.get('stderr', '')}")
                return False, messages
        except RuntimeError as exc:
            messages.append(f"ansible-galaxy install error: {exc}")
            return False, messages

        return True, messages

    def check(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """ansible-playbook --syntax-check"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._ansible is not None

        playbook = self._get_playbook()
        messages.append(f"ansible-playbook --syntax-check {playbook}")

        try:
            result = self._ansible.syntax_check(
                str(self._working_dir),
                playbook=playbook,
                timeout=self._get_timeout("check", 60),
            )
            if result.returncode != 0:
                messages.append(f"Syntax check failed:\n{result.stderr}")
                return False, messages
        except RuntimeError as exc:
            messages.append(f"Syntax check error: {exc}")
            return False, messages

        return True, messages

    def plan(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """ansible-playbook --check --diff (dry run)"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._ansible is not None

        playbook = self._get_playbook()
        inventory = self._get_inventory()
        messages.append(f"ansible-playbook --check --diff {playbook}")

        try:
            with inject_compose_env(self.resolved_values):
                with self._ssh_key_context() as key_file:
                    result = self._ansible.plan(
                        str(self._working_dir),
                        playbook=playbook,
                        inventory=inventory,
                        extra_vars=self._get_extra_vars(),
                        private_key_file=key_file,
                        timeout=self._get_timeout("plan", 600),
                    )
            if result.returncode != 0:
                messages.append(f"Check mode failed:\n{result.stderr}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"Check mode error: {exc}")
            return False, messages

        return True, messages

    def apply(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """ansible-playbook (apply changes)"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._ansible is not None

        playbook = self._get_playbook()
        inventory = self._get_inventory()
        messages.append(f"ansible-playbook {playbook}")

        try:
            with inject_compose_env(self.resolved_values):
                with self._ssh_key_context() as key_file:
                    result = self._ansible.apply(
                        str(self._working_dir),
                        playbook=playbook,
                        inventory=inventory,
                        extra_vars=self._get_extra_vars(),
                        private_key_file=key_file,
                        timeout=self._get_timeout("apply", 1800),
                    )
            if result.returncode != 0:
                messages.append(f"Playbook execution failed:\n{result.stderr}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"Playbook execution error: {exc}")
            return False, messages

        return True, messages

    def destroy(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Run destroy playbook (convention: destroy.yml)."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._ansible is not None

        if not self.force:
            messages.append("Ansible destroy requires --force flag.")
            return False, messages

        destroy_playbook = "destroy.yml"
        if not (self._working_dir / destroy_playbook).exists():
            messages.append(f"Destroy playbook not found: {destroy_playbook}")
            return False, messages

        inventory = self._get_inventory()
        messages.append(f"ansible-playbook {destroy_playbook}")

        try:
            with inject_compose_env(self.resolved_values):
                with self._ssh_key_context() as key_file:
                    result = self._ansible.apply(
                        str(self._working_dir),
                        playbook=destroy_playbook,
                        inventory=inventory,
                        private_key_file=key_file,
                        timeout=self._get_timeout("destroy", 1800),
                    )
            if result.returncode != 0:
                messages.append(f"Destroy playbook failed:\n{result.stderr}")
                return False, messages
        except RuntimeError as exc:
            messages.append(f"Destroy playbook error: {exc}")
            return False, messages

        return True, messages

    def plan_destroy(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Preview destroy in check mode."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._ansible is not None

        destroy_playbook = "destroy.yml"
        if not (self._working_dir / destroy_playbook).exists():
            messages.append(f"Destroy playbook not found: {destroy_playbook}")
            return False, messages

        inventory = self._get_inventory()
        messages.append(f"ansible-playbook --check --diff {destroy_playbook}")

        try:
            with inject_compose_env(self.resolved_values):
                with self._ssh_key_context() as key_file:
                    result = self._ansible.plan(
                        str(self._working_dir),
                        playbook=destroy_playbook,
                        inventory=inventory,
                        private_key_file=key_file,
                        timeout=self._get_timeout("plan", 600),
                    )
            if result.returncode != 0:
                messages.append(f"Destroy check mode failed:\n{result.stderr}")
                return False, messages
        except RuntimeError as exc:
            messages.append(f"Destroy check mode error: {exc}")
            return False, messages

        return True, messages

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Not applicable for Ansible — returns empty dict."""
        return True, {}, []

    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Not applicable for Ansible — returns empty dict."""
        return True, {}, []
