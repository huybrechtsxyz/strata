"""Built-in strata script: authenticate Docker to Google Artifact Registry or GCR.

Runs ``gcloud auth configure-docker`` to configure Docker credential helpers
for Artifact Registry and/or Container Registry hostnames.

Required environment variables (at least one):
    GAR_LOCATION    — Artifact Registry location (e.g. us-central1, europe-west1, us)
                      Used to configure <location>-docker.pkg.dev
    GCR_HOST        — Container Registry host (e.g. gcr.io, eu.gcr.io, us.gcr.io)
                      Defaults to "gcr.io" if neither is set and GCR_ENABLE=true

Optional:
    GCR_ENABLE      — Set to "true" to also configure legacy gcr.io (default: false)
    GOOGLE_CLOUD_PROJECT — GCP project (used for logging only)

Usage in workspace YAML:
    lifecycle:
      pre_deploy:
        scripts:
          - <strata_data>/scripts/gcloud_artifact_registry_login.py

    variables:
      - key: GAR_LOCATION
        source: constant
        value: europe-west1
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from strata.utils.gcloud_script_base import GCloudScript


class ArtifactRegistryLogin(GCloudScript):
    """Configure Docker to authenticate with Google Artifact Registry / GCR."""

    def run(self) -> None:
        gar_location = self.env("GAR_LOCATION")
        gcr_host = self.env("GCR_HOST", "gcr.io")
        gcr_enable = self.env("GCR_ENABLE", "false").lower() == "true"

        if not gar_location and not gcr_enable:
            self.log(
                "Neither GAR_LOCATION nor GCR_ENABLE is set. "
                "Set GAR_LOCATION (Artifact Registry) or GCR_ENABLE=true (Container Registry)."
            )
            sys.exit(1)

        hosts: list = []
        if gar_location:
            hosts.append(f"{gar_location}-docker.pkg.dev")
        if gcr_enable:
            hosts.append(gcr_host)

        host_list = ",".join(hosts)
        self.log(f"Configuring Docker credential helper for: {host_list}")

        result = self.run_gcloud(["auth", "configure-docker", host_list, "--quiet"])
        self.exit_on_failure(result, "gcloud auth configure-docker")
        self.log(f"Docker credential helper configured ({host_list})")


if __name__ == "__main__":
    ArtifactRegistryLogin().execute()
