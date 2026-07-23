"""AWS CLI (`aws`) integration — availability, authentication, and identity context.

Mirrors ``AzureCLIIntegration`` for the AWS ecosystem.  Serves as the shared
foundation for EKS credential fetching, ECR login, S3 bucket lifecycle, and any
other AWS CLI-based operations in lifecycle scripts and future deployers.

- **Availability check** — confirms ``aws`` is installed AND authenticated
  (``aws sts get-caller-identity``).  A binary without credentials fails every
  real AWS operation.
- **Identity context** — exposes the active account ID, user ARN, and region via
  ``get_identity()``.
- **Region resolution** — ``get_region()`` reads ``AWS_DEFAULT_REGION`` /
  ``AWS_REGION`` env vars and falls back to ``aws configure get region``.
- **STS token** — ``get_caller_identity()`` returns account/userId/Arn dict.

Install AWS CLI v2:
  https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html

Configuration YAML::

    integrations:
      - name: aws
        type: aws_cli
        capabilities: [aws]
        required: true
        validation:
          command: aws sts get-caller-identity
"""

import json
import os
import re
from typing import Any, Dict, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger

logger = get_logger(__name__)


class AWSCLIIntegration(BaseIntegration):
    """AWS CLI integration — availability, authentication, and identity context."""

    COMMAND = "aws"
    CAPABILITIES: list = []  # capability name: "aws"

    def get_version_command(self):
        return [self.command, "--version"]

    def parse_version(self, version_output: str) -> str:
        """Parse version from ``aws --version`` output (e.g. 'aws-cli/2.15.0 ...')."""
        m = re.search(r"aws-cli/(\d+\.\d+\.\d+)", version_output)
        return m.group(1) if m else version_output.strip()

    def get_setup_info(self) -> Dict[str, Any]:
        identity = self._get_identity_safe()
        if identity:
            status = f"Authenticated (account: {identity.get('Account', '?')})"
        else:
            status = "Not authenticated"
        return {
            "name": "aws_cli",
            "command": "aws",
            "install_url": "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html",
            "env_vars": [
                {"name": "AWS_ACCESS_KEY_ID", "purpose": "Access key ID", "required": False},
                {"name": "AWS_SECRET_ACCESS_KEY", "purpose": "Secret access key", "required": False},
                {"name": "AWS_SESSION_TOKEN", "purpose": "Session token (temporary credentials)", "required": False},
                {"name": "AWS_DEFAULT_REGION", "purpose": "Default AWS region", "required": False},
                {"name": "AWS_PROFILE", "purpose": "Named profile from ~/.aws/credentials", "required": False},
            ],
            "auth_methods": [
                {"method": "aws configure", "description": "Interactive profile setup. Preferred for local dev."},
                {
                    "method": "Environment variables",
                    "description": "Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION.",
                },
                {
                    "method": "IAM role / instance profile",
                    "description": "Automatic on EC2/ECS/Lambda — no env vars needed.",
                },
                {"method": "AWS SSO", "description": "Run 'aws sso login --profile <profile>'."},
            ],
            "yaml_example": ("- name: aws\n  type: aws_cli\n  capabilities: [aws]\n  required: true"),
            "info": status,
        }

    # ------------------------------------------------------------------
    # Availability and authentication
    # ------------------------------------------------------------------

    def ensure_available(self) -> Tuple[bool, str]:
        """Check that ``aws`` is installed AND authenticated.

        Runs ``aws sts get-caller-identity`` — the minimal STS call that confirms
        valid credentials without side effects.  A bare ``aws --version`` check is
        insufficient because unauthenticated CLI is useless for any real operation.
        """
        if not self.is_available():
            msg = (
                "AWS CLI is not installed or not in PATH. "
                "Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
            )
            self._info = msg
            return False, msg

        identity = self._get_identity_safe()
        if identity is None:
            msg = (
                "AWS CLI is installed but not authenticated. "
                "Run: aws configure  or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY"
            )
            self._info = msg
            return False, msg

        account = identity.get("Account", "?")
        region = self.get_region() or "no region set"
        self._info = f"Authenticated — account: {account}, region: {region}"
        return True, ""

    # ------------------------------------------------------------------
    # Identity and region context
    # ------------------------------------------------------------------

    def get_identity(self) -> Optional[Dict[str, str]]:
        """Return ``{Account, UserId, Arn}`` from ``aws sts get-caller-identity``."""
        return self._get_identity_safe()

    def get_region(self) -> Optional[str]:
        """Return the active AWS region.

        Resolution order:
        1. ``AWS_DEFAULT_REGION`` environment variable
        2. ``AWS_REGION`` environment variable
        3. ``aws configure get region`` (reads the active profile)
        """
        region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
        if region:
            return region
        try:
            result = self._run_integration(["configure", "get", "region"], timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _get_identity_safe(self) -> Optional[Dict[str, str]]:
        """Run ``aws sts get-caller-identity --output json``; return dict or None."""
        try:
            result = self._run_integration(["sts", "get-caller-identity", "--output", "json"], timeout=15)
            if result.returncode != 0 or not result.stdout:
                return None
            data = json.loads(result.stdout)
            return {
                "Account": data.get("Account", ""),
                "UserId": data.get("UserId", ""),
                "Arn": data.get("Arn", ""),
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Convenience: run arbitrary aws subcommands
    # ------------------------------------------------------------------

    def run_aws(self, args, timeout: int = 120):
        """Run an arbitrary ``aws`` subcommand and return the CommandResult.

        Callers (lifecycle scripts, future deployers) use this to execute
        ``aws eks update-kubeconfig``, ``aws s3api create-bucket``, etc.
        """
        return self._run_integration(args, timeout=timeout)
