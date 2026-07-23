"""Built-in strata script: fetch EKS credentials into kubeconfig.

Runs ``aws eks update-kubeconfig`` before a Helm, ArgoCD, or Flux stage that
targets an Amazon EKS cluster.

Required environment variables:
    EKS_CLUSTER      — EKS cluster name

Optional:
    AWS_DEFAULT_REGION      — AWS region (required if not in profile/env)
    EKS_ROLE_ARN            — IAM role ARN to assume for kubeconfig auth
    EKS_CONTEXT_ALIAS       — Override the kubeconfig context name
    EKS_NAMESPACE           — Set default namespace in kubeconfig context

Usage in workspace YAML:
    lifecycle:
      pre_deploy:
        scripts:
          - <strata_data>/scripts/aws_eks_credentials.py

    variables:
      - key: EKS_CLUSTER
        source: constant
        value: my-eks-cluster
      - key: AWS_DEFAULT_REGION
        source: constant
        value: us-east-1
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from strata.utils.aws_script_base import AWSScript


class EksCredentials(AWSScript):
    """Fetch EKS credentials and merge into kubeconfig."""

    def run(self) -> None:
        cluster = self.require_env("EKS_CLUSTER")
        aws_region = self.region()
        role_arn = self.env("EKS_ROLE_ARN")
        context_alias = self.env("EKS_CONTEXT_ALIAS")
        namespace = self.env("EKS_NAMESPACE")

        args = [
            "eks",
            "update-kubeconfig",
            "--name",
            cluster,
            "--region",
            aws_region,
        ]
        if role_arn:
            args += ["--role-arn", role_arn]
        if context_alias:
            args += ["--alias", context_alias]
        if namespace:
            args += ["--set-default-namespace", namespace]

        self.log(f"Fetching kubeconfig for EKS cluster '{cluster}' ({aws_region})")
        result = self.run_aws(args)
        self.exit_on_failure(result, "aws eks update-kubeconfig")
        self.log(f"EKS kubeconfig updated (cluster: {cluster}, region: {aws_region})")


if __name__ == "__main__":
    EksCredentials().execute()
