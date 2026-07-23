"""Base class for GCP-aware lifecycle scripts in .strata/scripts/.

Mirrors ``AzureScript`` and ``AWSScript`` for the GCP ecosystem.

Usage::

    # .strata/scripts/pre_deploy_gke.py
    from strata.utils.gcloud_script_base import GCloudScript

    class GkeCredentials(GCloudScript):
        def run(self):
            cluster = self.require_env("GKE_CLUSTER")
            zone = self.require_env("GKE_ZONE")
            project = self.project()             # GOOGLE_CLOUD_PROJECT or gcloud config
            result = self.run_gcloud([
                "container", "clusters", "get-credentials", cluster,
                "--zone", zone, "--project", project,
            ])
            self.exit_on_failure(result, "gcloud container clusters get-credentials")

    if __name__ == "__main__":
        GkeCredentials().execute()
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


class GCloudScript:
    """Base class for Google Cloud lifecycle scripts.

    Subclass and implement ``run()``.  Call ``execute()`` from
    ``if __name__ == "__main__"`` to run and handle exit codes.

    Provides:
    - ``run_gcloud(args)``          → subprocess result
    - ``project()``                 → active GCP project ID or exit(1)
    - ``account()``                 → active account from gcloud config
    - ``get_access_token()``        → bearer token via gcloud auth print-access-token
    - ``env(name, default)``        → os.environ.get with optional default
    - ``require_env(name)``         → os.environ[name] or exit(1) with clear error
    - ``exit_on_failure(result)``   → sys.exit(1) if returncode != 0
    - ``log(msg)``                  → prints to stderr (visible in strata output)
    - ``builtin_scripts_dir()``     → Path to strata's built-in GCP scripts
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
    # GCP CLI helpers
    # ------------------------------------------------------------------

    def run_gcloud(self, args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
        """Run a ``gcloud`` subcommand and return the CompletedProcess result."""
        cmd = ["gcloud"] + args
        self.log(f"Running: {' '.join(cmd)}")
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def project(self) -> str:
        """Return the active GCP project ID.

        Resolution order:
        1. ``GOOGLE_CLOUD_PROJECT`` environment variable
        2. ``CLOUDSDK_CORE_PROJECT`` environment variable
        3. ``gcloud config get-value project``
        → exits with error if none resolves
        """
        p = (os.environ.get("GOOGLE_CLOUD_PROJECT")
             or os.environ.get("CLOUDSDK_CORE_PROJECT")
             or os.environ.get("GCLOUD_PROJECT"))
        if p:
            return p
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        self.log(
            "GCP project not set. Set GOOGLE_CLOUD_PROJECT or run: "
            "gcloud config set project <PROJECT_ID>"
        )
        sys.exit(1)

    def account(self) -> Optional[str]:
        """Return the active account email from gcloud config."""
        result = subprocess.run(
            ["gcloud", "config", "get-value", "account"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None

    def get_access_token(self) -> Optional[str]:
        """Return a bearer token from ``gcloud auth print-access-token``."""
        result = self.run_gcloud(["auth", "print-access-token"])
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None

    # ------------------------------------------------------------------
    # Environment helpers
    # ------------------------------------------------------------------

    def env(self, name: str, default: str = "") -> str:
        return os.environ.get(name, default)

    def require_env(self, name: str) -> str:
        value = os.environ.get(name)
        if not value:
            self.log(f"Required environment variable '{name}' is not set.")
            self.log("Set it in your deployment YAML under spec.variables[] or as a secret.")
            sys.exit(1)
        return value

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

    def exit_on_failure(self, result: subprocess.CompletedProcess, label: str = "Command") -> None:
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
        print(f"[gcloud] {msg}", file=sys.stderr)

    @staticmethod
    def builtin_scripts_dir() -> Path:
        return Path(__file__).parent.parent / "data" / "scripts"
