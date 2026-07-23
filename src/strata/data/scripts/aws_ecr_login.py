"""Built-in strata script: authenticate Docker to Amazon ECR.

Runs ``aws ecr get-login-password | docker login`` so that subsequent
docker push/pull commands can access the private ECR registry.

Required environment variables:
    ECR_REGISTRY     — Full ECR registry URL
                       (e.g. 123456789012.dkr.ecr.us-east-1.amazonaws.com)
                       or set ECR_ACCOUNT_ID + AWS_DEFAULT_REGION to construct it

Optional:
    ECR_ACCOUNT_ID   — AWS account ID (auto-resolved via STS if absent and
                       ECR_REGISTRY is not set)
    AWS_DEFAULT_REGION — AWS region (auto-resolved if not set)

Usage in workspace YAML:
    lifecycle:
      pre_deploy:
        scripts:
          - <strata_data>/scripts/aws_ecr_login.py

    variables:
      - key: ECR_REGISTRY
        source: constant
        value: 123456789012.dkr.ecr.us-east-1.amazonaws.com
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from strata.utils.aws_script_base import AWSScript


class EcrLogin(AWSScript):
    """Authenticate Docker to Amazon Elastic Container Registry."""

    def run(self) -> None:
        registry = self.env("ECR_REGISTRY")

        # Construct registry URL if not given directly
        if not registry:
            account_id = self.env("ECR_ACCOUNT_ID") or self.account_id()
            aws_region = self.region()
            registry = f"{account_id}.dkr.ecr.{aws_region}.amazonaws.com"

        aws_region = self.region()

        self.log(f"Logging Docker into ECR registry '{registry}'")

        # Step 1: get-login-password
        pw_result = self.run_aws(["ecr", "get-login-password", "--region", aws_region])
        self.exit_on_failure(pw_result, "aws ecr get-login-password")

        # Step 2: docker login (pipe password via stdin)
        docker_result = subprocess.run(
            ["docker", "login", "--username", "AWS", "--password-stdin", registry],
            input=pw_result.stdout,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if docker_result.returncode != 0:
            self.log(docker_result.stderr.strip())
            self.log(f"docker login failed with exit code {docker_result.returncode}")
            sys.exit(1)

        self.log(f"ECR login successful ({registry})")


if __name__ == "__main__":
    EcrLogin().execute()
