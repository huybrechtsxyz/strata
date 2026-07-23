"""Base class for AWS-aware lifecycle scripts in .strata/scripts/.

Mirrors ``AzureScript`` for the AWS ecosystem.

Usage::

    # .strata/scripts/pre_deploy_eks.py
    from strata.utils.aws_script_base import AWSScript

    class EksCredentials(AWSScript):
        def run(self):
            cluster = self.require_env("EKS_CLUSTER")
            region = self.region()                # AWS_DEFAULT_REGION or aws configure
            result = self.run_aws([
                "eks", "update-kubeconfig",
                "--name", cluster,
                "--region", region,
            ])
            self.exit_on_failure(result, "aws eks update-kubeconfig")
            self.log(f"EKS kubeconfig updated for cluster '{cluster}'")

    if __name__ == "__main__":
        EksCredentials().execute()
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


class AWSScript:
    """Base class for AWS CLI lifecycle scripts.

    Subclass and implement ``run()``.  Call ``execute()`` from
    ``if __name__ == "__main__"`` to run the script and handle exit codes.

    Provides:
    - ``run_aws(args)``             → subprocess result (returncode, stdout, stderr)
    - ``region()``                  → active region (env var or aws configure)
    - ``account_id()``              → active account ID via aws sts get-caller-identity
    - ``env(name, default)``        → os.environ.get with optional default
    - ``require_env(name)``         → os.environ[name] or exit(1) with clear error
    - ``exit_on_failure(result)``   → sys.exit(1) if returncode != 0
    - ``log(msg)``                  → prints to stderr (visible in strata output)
    - ``builtin_scripts_dir()``     → Path to strata's built-in AWS scripts
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
    # AWS CLI helpers
    # ------------------------------------------------------------------

    def run_aws(self, args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
        """Run an ``aws`` subcommand and return the CompletedProcess result."""
        cmd = ["aws"] + args
        self.log(f"Running: {' '.join(cmd)}")
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def region(self) -> str:
        """Return the active AWS region.

        Resolution order: AWS_DEFAULT_REGION → AWS_REGION →
        ``aws configure get region`` → exit(1) with error.
        """
        r = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
        if r:
            return r
        result = subprocess.run(
            ["aws", "configure", "get", "region"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        self.log(
            "AWS region not set. Set AWS_DEFAULT_REGION or configure a profile with "
            "'aws configure'."
        )
        sys.exit(1)

    def account_id(self) -> str:
        """Return the active AWS account ID via ``aws sts get-caller-identity``."""
        import json
        result = self.run_aws(["sts", "get-caller-identity", "--output", "json"])
        if result.returncode != 0:
            self.log("Could not determine AWS account ID (aws sts get-caller-identity failed).")
            sys.exit(1)
        return json.loads(result.stdout).get("Account", "")

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
        print(f"[aws] {msg}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Built-in script discovery
    # ------------------------------------------------------------------

    @staticmethod
    def builtin_scripts_dir() -> Path:
        """Return the path to strata's built-in AWS lifecycle scripts."""
        return Path(__file__).parent.parent / "data" / "scripts"
