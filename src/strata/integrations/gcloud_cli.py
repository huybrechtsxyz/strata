"""Google Cloud CLI (`gcloud`) integration — availability, authentication, and project context.

Mirrors ``AzureCLIIntegration`` and ``AWSCLIIntegration`` for the GCP ecosystem.
Serves as the shared foundation for GKE credential fetching, Artifact Registry
login, and any other gcloud-based operations in lifecycle scripts and future deployers.

- **Availability check** — confirms ``gcloud`` is installed AND authenticated
  (active account + active project both set).
- **Project context** — exposes the active project id and account via ``get_project()``
  and ``get_account()``.
- **Token caching** — ``get_access_token()`` caches the bearer token for the session,
  avoids repeated ``gcloud auth print-access-token`` spawns.

Install Google Cloud CLI:
  https://cloud.google.com/sdk/docs/install

Configuration YAML::

    integrations:
      - name: gcloud
        type: gcloud_cli
        capabilities: [gcloud]
        required: true
        validation:
          command: gcloud config get-value account
"""

import re
import threading
from typing import Any, Dict, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger

logger = get_logger(__name__)


class GCloudCLIIntegration(BaseIntegration):
    """Google Cloud CLI integration — availability, authentication, and project context."""

    COMMAND = "gcloud"
    CAPABILITIES: list = []  # capability name: "gcloud"

    # Token cache: process-scoped, cleared when project/account changes
    _token_cache: Optional[str] = None
    _token_lock = threading.Lock()

    def get_version_command(self):
        return [self.command, "--version"]

    def parse_version(self, version_output: str) -> str:
        """Parse version from ``gcloud --version`` output.

        Example: ``Google Cloud SDK 498.0.0`` → ``498.0.0``
        """
        m = re.search(r"Google Cloud SDK\s+(\d+\.\d+\.\d+)", version_output)
        if m:
            return m.group(1)
        m = re.search(r"(\d+\.\d+\.\d+)", version_output)
        return m.group(1) if m else version_output.strip().split("\n")[0]

    def get_setup_info(self) -> Dict[str, Any]:
        account = self.get_account()
        project = self.get_project()
        if account and project:
            status = f"Authenticated ({account} / project: {project})"
        elif account:
            status = f"Authenticated ({account}) — no project set"
        else:
            status = "Not authenticated"
        return {
            "name": "gcloud_cli",
            "command": "gcloud",
            "install_url": "https://cloud.google.com/sdk/docs/install",
            "env_vars": [
                {
                    "name": "GOOGLE_APPLICATION_CREDENTIALS",
                    "purpose": "Path to service account key JSON",
                    "required": False,
                },
                {"name": "GOOGLE_CLOUD_PROJECT", "purpose": "Override active project ID", "required": False},
                {"name": "CLOUDSDK_CORE_PROJECT", "purpose": "Alternative project override", "required": False},
            ],
            "auth_methods": [
                {"method": "gcloud auth login", "description": "Interactive browser login. Preferred for local dev."},
                {
                    "method": "gcloud auth application-default login",
                    "description": "ADC for Terraform google provider — run separately.",
                },
                {
                    "method": "Service account key",
                    "description": "Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json.",
                },
                {"method": "Workload Identity", "description": "Automatic on GKE/Cloud Run — no env vars needed."},
            ],
            "yaml_example": ("- name: gcloud\n  type: gcloud_cli\n  capabilities: [gcloud]\n  required: true"),
            "info": status,
        }

    # ------------------------------------------------------------------
    # Availability and authentication
    # ------------------------------------------------------------------

    def ensure_available(self) -> Tuple[bool, str]:
        """Check that ``gcloud`` is installed, authenticated, and has an active project.

        Checks in order:
        1. Binary in PATH
        2. Active account (``gcloud config get-value account``)
        3. Active project (``gcloud config get-value project``)
        """
        if not self.is_available():
            msg = "Google Cloud CLI is not installed or not in PATH. Install: https://cloud.google.com/sdk/docs/install"
            self._info = msg
            return False, msg

        account = self.get_account()
        if not account:
            msg = (
                "gcloud is installed but not authenticated. "
                "Run: gcloud auth login  (interactive) or "
                "gcloud auth activate-service-account  (service account)"
            )
            self._info = msg
            return False, msg

        project = self.get_project()
        if not project:
            msg = (
                f"gcloud is authenticated as {account} but no project is set. "
                "Run: gcloud config set project <PROJECT_ID>"
            )
            self._info = msg
            return False, msg

        self._info = f"Authenticated — {account} / project: {project}"
        return True, ""

    # ------------------------------------------------------------------
    # Project and account context
    # ------------------------------------------------------------------

    def get_project(self) -> Optional[str]:
        """Return the active GCP project ID from ``gcloud config get-value project``."""
        import os

        # Environment variable overrides
        project = (
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("CLOUDSDK_CORE_PROJECT")
            or os.environ.get("GCLOUD_PROJECT")
        )
        if project:
            return project
        try:
            result = self._run_integration(["config", "get-value", "project"], timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def get_account(self) -> Optional[str]:
        """Return the active account email from ``gcloud config get-value account``."""
        try:
            result = self._run_integration(["config", "get-value", "account"], timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Token caching
    # ------------------------------------------------------------------

    def get_access_token(self) -> Optional[str]:
        """Return a cached bearer token from ``gcloud auth print-access-token``.

        Token is cached in-process until ``clear_token_cache()`` is called.
        Use for REST API calls to GCP services (Secret Manager, Cloud Storage, etc.).
        """
        with self._token_lock:
            if self.__class__._token_cache:
                return self.__class__._token_cache

        try:
            result = self._run_integration(["auth", "print-access-token"], timeout=30)
            if result.returncode != 0 or not result.stdout.strip():
                return None
            token = result.stdout.strip()
            with self._token_lock:
                self.__class__._token_cache = token
            return token
        except Exception as exc:
            logger.debug("gcloud_get_access_token_failed", error=str(exc))
            return None

    def clear_token_cache(self) -> None:
        """Clear the in-process token cache (call after ``gcloud auth login`` or project switch)."""
        with self._token_lock:
            self.__class__._token_cache = None

    # ------------------------------------------------------------------
    # Convenience: run arbitrary gcloud subcommands
    # ------------------------------------------------------------------

    def run_gcloud(self, args, timeout: int = 120):
        """Run an arbitrary ``gcloud`` subcommand and return the CommandResult."""
        return self._run_integration(args, timeout=timeout)
