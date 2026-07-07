"""Ansible integration for configuration management and deployment operations."""

import re
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IInfrastructureTool
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel
from strata.utils.system import CommandResult, run_command

logger = get_logger(__name__)


class AnsibleIntegration(BaseIntegration):
    """
    Ansible integration for configuration management.

    Wraps ansible-playbook for running playbooks against target hosts.
    Implements singleton pattern per config.
    """

    # Command executable name
    COMMAND = "ansible-playbook"

    # Declare supported capabilities
    CAPABILITIES = [IInfrastructureTool]

    # Singleton instance keying based on config name
    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """Get instance key based on integration name."""
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"
        return config.name or "default"

    def __init__(self, config: IntegrationModel):
        """Initialize Ansible integration."""
        super().__init__(config)
        logger.debug("Ansible integration initialized", name=self.integration_name)

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve ansible version."""
        return [self.command, "--version"]

    def parse_version(self, version_output: str) -> str:
        """Parse version string from ansible-playbook output.

        Example output: "ansible-playbook [core 2.15.4]"
        """
        match = re.search(r"(\d+\.\d+\.\d+)", version_output)
        if match:
            return match.group(1)
        return version_output.strip()

    def get_setup_info(self) -> dict:
        """Return setup metadata for ansible."""
        return {
            "name": "ansible",
            "command": "ansible-playbook",
            "install_url": "https://docs.ansible.com/ansible/latest/installation_guide/",
            "env_vars": [
                {
                    "name": "ANSIBLE_CONFIG",
                    "purpose": "Path to ansible.cfg configuration file",
                    "required": False,
                },
                {
                    "name": "ANSIBLE_INVENTORY",
                    "purpose": "Path to default inventory file or directory",
                    "required": False,
                },
            ],
            "auth_methods": [
                {
                    "method": "SSH keys",
                    "description": "Use SSH key-based authentication for remote hosts.",
                },
                {
                    "method": "Vault password",
                    "description": "Set ANSIBLE_VAULT_PASSWORD_FILE or use --vault-password-file.",
                },
            ],
            "yaml_example": "type: ansible\nspec:\n  inventory: inventory/hosts.yml\n  playbook: site.yml",
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """Ensure integration is available."""
        if not self.is_available():
            self._info = f"{self.integration_name} CLI is not installed or not in PATH."
            logger.warning("Ansible CLI not found", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. Install with: pip install ansible",
            )

        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "Ansible version validation failed",
                name=self.integration_name,
                error=version_error,
            )
            return False, version_error

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "Ansible is available",
            name=self.integration_name,
            version=self.get_version(),
        )
        return True, ""

    # Ansible command methods (IInfrastructureTool implementation)

    def init(
        self,
        working_dir: str,
        requirements_file: Optional[str] = None,
        timeout: int = 300,
        **kwargs,
    ) -> Any:
        """Install Galaxy collections/roles.

        Args:
            working_dir: Working directory containing playbooks
            requirements_file: Path to requirements.yml for Galaxy dependencies
            timeout: Command timeout in seconds
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(f"Ansible not available: {error}")

        if requirements_file is None:
            # Nothing to install — skip silently
            return CommandResult(returncode=0, stdout="", stderr="", command="", duration_ms=0.0)

        # ansible-galaxy is a separate binary, not ansible-playbook; use run_command directly
        galaxy_cmd = ["ansible-galaxy", "collection", "install", "-r", requirements_file]
        logger.info("Installing Ansible Galaxy dependencies", working_dir=working_dir)
        return run_command(galaxy_cmd, cwd=working_dir, timeout=timeout)

    def plan(
        self,
        working_dir: str,
        playbook: str = "site.yml",
        inventory: Optional[str] = None,
        extra_vars: Optional[Dict[str, str]] = None,
        extra_vars_files: Optional[List[str]] = None,
        private_key_file: Optional[str] = None,
        timeout: int = 600,
        **kwargs,
    ) -> Any:
        """Run playbook in check mode (dry run).

        Args:
            working_dir: Working directory containing playbooks
            playbook: Playbook filename
            inventory: Inventory file path
            extra_vars: Additional variables as key=value pairs
            extra_vars_files: YAML variable files passed as ``-e @file.yml``
            private_key_file: Path to SSH private key file
            timeout: Command timeout in seconds
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(f"Ansible not available: {error}")

        args = [playbook, "--check", "--diff"]
        if inventory:
            args.extend(["-i", inventory])
        if private_key_file:
            args.extend(["--private-key", private_key_file])
        if extra_vars_files:
            for fpath in extra_vars_files:
                args.extend(["-e", f"@{fpath}"])
        if extra_vars:
            for k, v in extra_vars.items():
                args.extend(["-e", f"{k}={v}"])

        logger.info("Running Ansible check mode", working_dir=working_dir, playbook=playbook)
        return self._run_integration(args, cwd=working_dir, timeout=timeout)

    def apply(
        self,
        working_dir: str,
        playbook: str = "site.yml",
        inventory: Optional[str] = None,
        extra_vars: Optional[Dict[str, str]] = None,
        extra_vars_files: Optional[List[str]] = None,
        private_key_file: Optional[str] = None,
        timeout: int = 1800,
        **kwargs,
    ) -> Any:
        """Run playbook (apply changes).

        Args:
            working_dir: Working directory containing playbooks
            playbook: Playbook filename
            inventory: Inventory file path
            extra_vars: Additional variables as key=value pairs
            extra_vars_files: YAML variable files passed as ``-e @file.yml``
            private_key_file: Path to SSH private key file
            timeout: Command timeout in seconds
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(f"Ansible not available: {error}")

        args = [playbook]
        if inventory:
            args.extend(["-i", inventory])
        if private_key_file:
            args.extend(["--private-key", private_key_file])
        if extra_vars_files:
            for fpath in extra_vars_files:
                args.extend(["-e", f"@{fpath}"])
        if extra_vars:
            for k, v in extra_vars.items():
                args.extend(["-e", f"{k}={v}"])

        logger.info("Running Ansible playbook", working_dir=working_dir, playbook=playbook)
        return self._run_integration(args, cwd=working_dir, timeout=timeout)

    def syntax_check(
        self,
        working_dir: str,
        playbook: str = "site.yml",
        timeout: int = 60,
        **kwargs,
    ) -> Any:
        """Validate playbook syntax.

        Args:
            working_dir: Working directory containing playbooks
            playbook: Playbook filename
            timeout: Command timeout in seconds
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(f"Ansible not available: {error}")

        args = [playbook, "--syntax-check"]
        logger.info("Running Ansible syntax check", working_dir=working_dir, playbook=playbook)
        return self._run_integration(args, cwd=working_dir, timeout=timeout)
