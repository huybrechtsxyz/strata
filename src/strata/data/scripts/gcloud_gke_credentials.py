"""Built-in strata script: fetch GKE credentials into kubeconfig.

Runs ``gcloud container clusters get-credentials`` before a Helm, ArgoCD,
or Flux stage that targets a Google Kubernetes Engine cluster.

Required environment variables:
    GKE_CLUSTER          — GKE cluster name

Optional:
    GKE_ZONE             — Zone (e.g. us-central1-a) — mutually exclusive with GKE_REGION
    GKE_REGION           — Region for regional clusters (e.g. us-central1)
    GOOGLE_CLOUD_PROJECT — GCP project ID (auto-resolved from gcloud config if absent)
    GKE_CONTEXT_NAME     — Override the kubeconfig context name
    GKE_INTERNAL_IP      — Set to "true" to use internal IP endpoint

Usage in workspace YAML:
    lifecycle:
      pre_deploy:
        scripts:
          - <strata_data>/scripts/gcloud_gke_credentials.py

    variables:
      - key: GKE_CLUSTER
        source: constant
        value: my-gke-cluster
      - key: GKE_REGION
        source: constant
        value: us-central1
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from strata.utils.gcloud_script_base import GCloudScript


class GkeCredentials(GCloudScript):
    """Fetch GKE credentials and merge into kubeconfig."""

    def run(self) -> None:
        cluster = self.require_env("GKE_CLUSTER")
        gcp_project = self.project()
        zone = self.env("GKE_ZONE")
        region = self.env("GKE_REGION")
        context_name = self.env("GKE_CONTEXT_NAME")
        internal_ip = self.env("GKE_INTERNAL_IP", "false").lower() == "true"

        args = [
            "container",
            "clusters",
            "get-credentials",
            cluster,
            "--project",
            gcp_project,
        ]
        if zone:
            args += ["--zone", zone]
        elif region:
            args += ["--region", region]
        else:
            self.log("Warning: neither GKE_ZONE nor GKE_REGION set — gcloud will use the default zone.")

        if context_name:
            args += ["--context-name", context_name]
        if internal_ip:
            args.append("--internal-ip")

        self.log(f"Fetching GKE credentials for cluster '{cluster}' (project: {gcp_project})")
        result = self.run_gcloud(args)
        self.exit_on_failure(result, "gcloud container clusters get-credentials")
        self.log(f"GKE credentials merged into kubeconfig (cluster: {cluster})")


if __name__ == "__main__":
    GkeCredentials().execute()
