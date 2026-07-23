"""Base class for Azure-aware lifecycle scripts in .strata/scripts/.

Usage — create a script that inherits from AzureScript::

    # .strata/scripts/pre_deploy_aks.py
    import sys
    sys.path.insert(0, '')          # ensure strata is importable
    from strata.utils.azure_script_base import AzureScript

    class AksCredentials(AzureScript):
        def run(self):
            cluster = self.require_env("AKS_CLUSTER")
            rg = self.require_env("AKS_RESOURCE_GROUP")
            result = self.run_az(["aks", "get-credentials",
                                  "--resource-group", rg,
                                  "--name", cluster,
                                  "--overwrite-existing"])
            self.exit_on_failure(result, "az aks get-credentials")

    if __name__ == "__main__":
        AksCredentials().execute()

Or use the three built-in scripts directly in workspace YAML::

    lifecycle:
      pre_deploy:
        scripts:
          - ${strata_scripts}/azure_aks_credentials.py

The ``${strata_scripts}`` token is resolved to the built-in scripts directory at
runtime via ``AzureScript.builtin_scripts_dir()``.

Environment variables available to all lifecycle scripts (injected by strata):
    STRATA_PHASE, STRATA_WORKSPACE_PATH, STRATA_BUILD_PATH,
    STRATA_CONFIG_PATH, STRATA_OBJECT_PATH, STRATA_STAGE_NAME
    + all resolved secrets / variables from the active deployment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


class AzureScript:
    """Base class for Azure CLI lifecycle scripts.

    Subclass and implement ``run()``.  Call ``execute()`` from
    ``if __name__ == "__main__"`` to run the script and handle exit codes.

    Provides:
    - ``run_az(args)``            → subprocess result (returncode, stdout, stderr)
    - ``get_token(resource)``     → bearer token via az account get-access-token
    - ``env(name, default)``      → os.environ.get with optional default
    - ``require_env(name)``       → os.environ[name] or exit(1) with clear error
    - ``exit_on_failure(result)`` → sys.exit(1) if returncode != 0
    - ``log(msg)``                → prints to stderr (visible in strata output)
    - ``builtin_scripts_dir()``   → Path to strata's built-in Azure scripts
    """

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def execute(self) -> None:
        """Run the script and exit with appropriate code."""
        try:
            self.run()
            sys.exit(0)
        except SystemExit:
            raise
        except Exception as exc:
            self.log(f"Error: {exc}")
            sys.exit(1)

    def run(self) -> None:
        """Override this method to implement the script logic."""
        raise NotImplementedError("Subclasses must implement run()")

    # ------------------------------------------------------------------
    # Azure CLI helpers
    # ------------------------------------------------------------------

    def run_az(self, args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
        """Run an ``az`` subcommand and return the CompletedProcess result."""
        cmd = ["az"] + args
        self.log(f"Running: {' '.join(cmd)}")
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def get_token(self, resource: str = "https://management.azure.com") -> Optional[str]:
        """Return a bearer token for the given Azure resource scope via az CLI."""
        import json

        result = self.run_az(["account", "get-access-token", "--resource", resource, "--output", "json"])
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout).get("accessToken")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Environment helpers
    # ------------------------------------------------------------------

    def env(self, name: str, default: str = "") -> str:
        """Return an environment variable value (default: empty string)."""
        return os.environ.get(name, default)

    def require_env(self, name: str) -> str:
        """Return an environment variable value, exiting with an error if absent."""
        value = os.environ.get(name)
        if not value:
            self.log(f"Required environment variable '{name}' is not set.")
            self.log("Set it in your deployment YAML under spec.variables[] or as a secret.")
            sys.exit(1)
        return value

    # Strata lifecycle context helpers

    def workspace_path(self) -> Path:
        return Path(self.env("STRATA_WORKSPACE_PATH", "."))

    def build_path(self) -> Path:
        return Path(self.env("STRATA_BUILD_PATH", "."))

    def stage_name(self) -> str:
        return self.env("STRATA_STAGE_NAME", "unknown")

    def phase(self) -> str:
        return self.env("STRATA_PHASE", "unknown")

    # ------------------------------------------------------------------
    # Exit / logging helpers
    # ------------------------------------------------------------------

    def exit_on_failure(
        self,
        result: subprocess.CompletedProcess,
        label: str = "Command",
    ) -> None:
        """Exit with code 1 if the result returncode is non-zero."""
        if result.returncode != 0:
            if result.stdout:
                self.log(result.stdout.strip())
            if result.stderr:
                self.log(result.stderr.strip())
            self.log(f"{label} failed with exit code {result.returncode}")
            sys.exit(1)
        if result.stdout:
            print(result.stdout.strip())

    def log(self, msg: str) -> None:
        """Print a message to stderr (visible in strata console output)."""
        print(f"[azure] {msg}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Built-in script discovery
    # ------------------------------------------------------------------

    @staticmethod
    def builtin_scripts_dir() -> Path:
        """Return the path to strata's built-in Azure lifecycle scripts.

        Reference built-in scripts in workspace YAML as::

            scripts:
              - ${AzureScript.builtin_scripts_dir()}/azure_aks_credentials.py
        """
        return Path(__file__).parent.parent / "data" / "scripts"
