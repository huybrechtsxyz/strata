"""Built-in strata script: fetch AKS credentials into kubeconfig.

Runs ``az aks get-credentials`` before a Helm, ArgoCD, or Flux stage that
targets an Azure Kubernetes Service cluster.

Required environment variables (set via deployment spec.variables or spec.secrets):
    AKS_CLUSTER              — AKS cluster name
    AKS_RESOURCE_GROUP       — Resource group containing the cluster

Optional:
    AKS_SUBSCRIPTION         — Subscription ID (uses active subscription if absent)
    AKS_ADMIN_CREDENTIALS    — Set to "true" to use --admin flag (cluster admin creds)
    AKS_CONTEXT_NAME         — Override the kubeconfig context name

Usage in workspace YAML:
    lifecycle:
      pre_deploy:
        scripts:
          - <strata_data>/scripts/azure_aks_credentials.py

    variables:
      - key: AKS_CLUSTER
        source: constant
        value: my-aks-cluster
      - key: AKS_RESOURCE_GROUP
        source: constant
        value: my-resource-group
"""

import sys
from pathlib import Path

# Allow running directly (python azure_aks_credentials.py) during development
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from strata.utils.azure_script_base import AzureScript


class AksCredentials(AzureScript):
    """Fetch AKS credentials and merge into kubeconfig."""

    def run(self) -> None:
        cluster = self.require_env("AKS_CLUSTER")
        rg = self.require_env("AKS_RESOURCE_GROUP")
        subscription = self.env("AKS_SUBSCRIPTION")
        admin = self.env("AKS_ADMIN_CREDENTIALS", "false").lower() == "true"
        context_name = self.env("AKS_CONTEXT_NAME")

        args = [
            "aks",
            "get-credentials",
            "--resource-group",
            rg,
            "--name",
            cluster,
            "--overwrite-existing",
        ]
        if subscription:
            args += ["--subscription", subscription]
        if admin:
            args.append("--admin")
        if context_name:
            args += ["--context", context_name]

        self.log(f"Fetching credentials for AKS cluster '{cluster}' in '{rg}'")
        result = self.run_az(args)
        self.exit_on_failure(result, "az aks get-credentials")
        self.log(f"AKS credentials merged into kubeconfig (cluster: {cluster})")


if __name__ == "__main__":
    AksCredentials().execute()
