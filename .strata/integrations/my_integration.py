"""
Custom integration template for Strata.

Copy / rename this file, implement the class, then register the type so strata
can load it via the YAML ``type:`` field in your configuration files.

Drop-in location: .strata/integrations/<your_name>.py
The platform auto-discovers and loads all .py files in that directory at
startup, so no changes to core code are required.

Minimal checklist
-----------------
1. Rename the class and the COMMAND / integration_type constants.
2. Implement get_version_command() and parse_version().
3. Implement get_setup_info() with install URL and env vars.
4. Implement ensure_available() to validate runtime requirements.
5. Add any operation methods your integration exposes.
6. Call register() at the bottom so IntegrationFactory can create instances.

How it works
------------
Auto-discovery: At startup the platform scans ``.strata/integrations/`` and
imports every ``.py`` file it finds.  Each file's ``register()`` function is
called, which inserts the type→class mapping into ``IntegrationFactory``.
Afterwards, any YAML entry whose ``type:`` matches the registered string will
be instantiated via ``IntegrationFactory.create(config)``.

Singleton lifecycle: ``BaseIntegration.__new__`` enforces one instance per
(class, key) pair, where the key is computed by
``_get_instance_key_static()``.  By default the key is the integration's
``name`` field from config, so two YAML entries with different names produce
two independent instances while repeated calls with the same name return the
same object.  Override ``_get_instance_key_static()`` to key on something
else (e.g. endpoint URL) if your integration needs multiple simultaneous
connections.

Capability protocols (optional, from strata.integrations.capabilities)
-----------------------------------------------------------------------------
Declare CAPABILITIES to signal what your integration can do:
  IRepositoryTool      - git-style clone/fetch/push
  IInfrastructureTool  - terraform-style plan/apply
  IContainerTool       - docker-style build/run
  ISecretStore         - read secrets by key
  IVariableStore       - read key/value configuration
  IFeatureFlagStore    - read feature-flag values

Example YAML usage (after registration)
----------------------------------------
  type: my_integration
  spec:
    endpoint: https://my-service.example.com
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.factory import IntegrationFactory

# from strata.integrations.capabilities import ISecretStore  # uncomment if needed
from strata.models.integration_model import IntegrationModel


class MyIntegration(BaseIntegration):
    """Replace this docstring with a description of your integration."""

    # The CLI binary name, or None for SDK-only integrations.
    COMMAND: Optional[str] = "my-tool"

    # Declare capability protocols this integration satisfies (optional).
    # CAPABILITIES = [ISecretStore]

    # ------------------------------------------------------------------ #
    # Singleton keying                                                     #
    # ------------------------------------------------------------------ #

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """Return a unique key per config instance.

        The default uses the integration name from the config, giving one
        singleton per named entry in your solution.  Override only if you
        need to key on something else (e.g. endpoint URL).
        """
        config: Optional[IntegrationModel] = kwargs.get("config") or (args[0] if args else None)
        return config.name if config else "default"

    # ------------------------------------------------------------------ #
    # Initialisation                                                       #
    # ------------------------------------------------------------------ #

    def __init__(self, config: IntegrationModel) -> None:
        super().__init__(config)
        # Read any custom fields from config.spec if defined in your model.
        # self._endpoint: str = config.spec.get("endpoint", "") if config.spec else ""

    # ------------------------------------------------------------------ #
    # Required abstract methods                                            #
    # ------------------------------------------------------------------ #

    def get_version_command(self) -> List[str]:
        """Return the command used to retrieve the tool version.

        Example: ["my-tool", "--version"]
        The output is passed to parse_version() below.
        """
        return [self.COMMAND or "my-tool", "--version"]

    def parse_version(self, version_output: str) -> str:
        """Extract a clean version string from the raw command output.

        Example: "my-tool version 1.2.3" -> "1.2.3"
        """
        # Adapt the parsing logic to match your tool's output format.
        parts = version_output.strip().split()
        return parts[-1] if parts else version_output.strip()

    # ------------------------------------------------------------------ #
    # Availability override (optional — base class already handles this)  #
    # ------------------------------------------------------------------ #

    def ensure_available(self) -> Tuple[bool, str]:
        """Validate that the integration is ready to use.

        Called before any operation.  Return (True, "") on success or
        (False, "human-readable error") on failure.
        """
        if not self.is_available():
            msg = f"{self.integration_name} is not installed or not in PATH.  Install from: https://example.com/install"
            return False, msg
        return True, ""

    # ------------------------------------------------------------------ #
    # Setup metadata (used by `strata tools install` and `strata tools check`)  #
    # ------------------------------------------------------------------ #

    def get_setup_info(self) -> Dict[str, Any]:
        """Return metadata used by `strata tools install` and `strata tools check`."""
        return {
            "name": "my_integration",
            "command": self.COMMAND,
            "install_url": "https://example.com/install",
            "env_vars": [
                {
                    "name": "MY_TOOL_API_TOKEN",
                    "purpose": "API token for authentication",
                    "required": True,
                },
                {
                    "name": "MY_TOOL_ENDPOINT",
                    "purpose": "Service endpoint URL",
                    "required": False,
                },
            ],
            "auth_methods": [
                {
                    "method": "Environment variable",
                    "description": "Set MY_TOOL_API_TOKEN before running strata.",
                },
            ],
            "yaml_example": ("type: my_integration\nspec:\n  endpoint: https://my-service.example.com\n"),
        }

    # ------------------------------------------------------------------ #
    # Operations                                                           #
    # ------------------------------------------------------------------ #

    def do_something(self, arg: str) -> Tuple[bool, str]:
        """Example operation method.

        Returns (success, output_or_error_message).
        """
        ok, err = self.ensure_available()
        if not ok:
            return False, err

        result = self._run_integration(["subcommand", arg], cwd=None)
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, result.stdout.strip()

    # ------------------------------------------------------------------ #
    # Capability implementation (uncomment and implement as needed)        #
    # ------------------------------------------------------------------ #

    # def get_secret(self, key: str) -> Optional[str]:
    #     """Implement ISecretStore.get_secret."""
    #     ok, value = self.do_something(key)
    #     return value if ok else None


# --------------------------------------------------------------------------- #
# Registration — called automatically when the file is loaded by the platform  #
# --------------------------------------------------------------------------- #


def register() -> None:
    """Register this integration type with IntegrationFactory.

    The platform calls register() on every .py file it discovers in
    .strata/integrations/.  The type string must match the ``type:``
    field you use in your YAML configuration.
    """
    IntegrationFactory.register_type("my_integration", MyIntegration)
