"""GCP lifecycle script starter — copy and adapt for your deployment.

Quick start — reference built-in scripts directly, no code needed:
===============================================================

    lifecycle:
      pre_deploy:
        scripts:
          # Fetch GKE credentials before Helm / ArgoCD deploy
          - ${STRATA_GCP_SCRIPTS}/gcloud_gke_credentials.py

          # Configure Docker for Artifact Registry
          - ${STRATA_GCP_SCRIPTS}/gcloud_artifact_registry_login.py

      pre_provision:
        scripts:
          # Create GCS bucket for Terraform remote state (idempotent)
          - ${STRATA_GCP_SCRIPTS}/gcloud_gcs_bucket_ensure.py

    variables:
      - key: GKE_CLUSTER
        source: constant
        value: my-gke-cluster
      - key: GKE_REGION
        source: constant
        value: us-central1
      - key: GAR_LOCATION
        source: constant
        value: europe-west1
      - key: GCS_BUCKET
        source: constant
        value: my-terraform-state
      - key: GCS_VERSIONING
        source: constant
        value: "true"


Custom script — subclass GCloudScript:
========================================

    from strata.utils.gcloud_script_base import GCloudScript

    class MyDeployScript(GCloudScript):
        def run(self):
            cluster = self.require_env("GKE_CLUSTER")
            region = self.require_env("GKE_REGION")
            project = self.project()

            result = self.run_gcloud([
                "container", "clusters", "get-credentials", cluster,
                "--region", region, "--project", project,
            ])
            self.exit_on_failure(result, "gcloud container clusters get-credentials")
            self.log(f"GKE credentials fetched for {cluster}")

    if __name__ == "__main__":
        MyDeployScript().execute()


Available helpers on GCloudScript:
=====================================

    self.run_gcloud(args)          Run gcloud subcommand; returns CompletedProcess
    self.exit_on_failure(result)   sys.exit(1) if returncode != 0
    self.require_env("VAR")        Get env var or exit(1) with clear error
    self.env("VAR", default="")    Get env var with optional default
    self.project()                 GCP project (env → gcloud config → exit(1))
    self.account()                 Active account from gcloud config
    self.get_access_token()        Bearer token from gcloud auth print-access-token
    self.workspace_path()          Path from STRATA_WORKSPACE_PATH
    self.log("message")            Print to stderr (visible in strata output)

Docs: strata help --topic gcloud_scripts
"""

# Uncomment and adapt the block below to create your custom script:
#
# from strata.utils.gcloud_script_base import GCloudScript
#
# class MyScript(GCloudScript):
#     def run(self):
#         project = self.project()
#         result = self.run_gcloud(["config", "list", "--format=json"])
#         self.exit_on_failure(result, "gcloud config list")
#         self.log(f"gcloud is configured for project: {project}")
#
# if __name__ == "__main__":
#     MyScript().execute()
