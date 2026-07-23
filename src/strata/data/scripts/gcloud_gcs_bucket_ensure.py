"""Built-in strata script: ensure a GCS bucket exists.

Runs ``gcloud storage buckets create`` — idempotent: if the bucket already exists
the command succeeds silently with ``--no-fail-on-existing-bucket``.

Common uses:
- Ensure Terraform remote state bucket exists before ``strata deploy run``
- Ensure artifact storage bucket exists before a build stage

Required environment variables:
    GCS_BUCKET           — GCS bucket name (without gs:// prefix)
    GOOGLE_CLOUD_PROJECT — GCP project ID (auto-resolved from gcloud config if absent)

Optional:
    GCS_LOCATION         — Bucket location (e.g. US, EU, us-central1). Default: US
    GCS_STORAGE_CLASS    — Storage class (STANDARD, NEARLINE, COLDLINE, ARCHIVE). Default: STANDARD
    GCS_VERSIONING       — Set to "true" to enable object versioning (recommended for TF state)
    GCS_LABELS           — Comma-separated key=value labels (e.g. "env=prod,team=platform")

Usage in workspace YAML:
    lifecycle:
      pre_provision:
        scripts:
          - <strata_data>/scripts/gcloud_gcs_bucket_ensure.py

    variables:
      - key: GCS_BUCKET
        source: constant
        value: my-terraform-state
      - key: GCS_LOCATION
        source: constant
        value: EU
      - key: GCS_VERSIONING
        source: constant
        value: "true"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from strata.utils.gcloud_script_base import GCloudScript


class GcsBucketEnsure(GCloudScript):
    """Create a GCS bucket if it does not already exist."""

    def run(self) -> None:
        bucket = self.require_env("GCS_BUCKET")
        gcp_project = self.project()
        location = self.env("GCS_LOCATION", "US")
        storage_class = self.env("GCS_STORAGE_CLASS", "STANDARD")
        versioning = self.env("GCS_VERSIONING", "false").lower() == "true"
        labels_raw = self.env("GCS_LABELS")

        bucket_url = f"gs://{bucket}" if not bucket.startswith("gs://") else bucket

        args = [
            "storage",
            "buckets",
            "create",
            bucket_url,
            "--project",
            gcp_project,
            "--location",
            location,
            "--default-storage-class",
            storage_class,
            "--no-fail-on-existing-bucket",  # idempotent
        ]

        if labels_raw:
            for pair in labels_raw.split(","):
                pair = pair.strip()
                if "=" in pair:
                    args += ["--labels", pair]

        self.log(f"Ensuring GCS bucket '{bucket}' exists in '{location}' (project: {gcp_project})")
        result = self.run_gcloud(args)
        self.exit_on_failure(result, "gcloud storage buckets create")
        self.log(f"Bucket '{bucket}' ready")

        # Versioning (separate command — only applies if bucket was just created or changed)
        if versioning:
            r = self.run_gcloud(
                [
                    "storage",
                    "buckets",
                    "update",
                    bucket_url,
                    "--versioning",
                ]
            )
            if r.returncode == 0:
                self.log("Versioning enabled")
            else:
                self.log(f"Warning: could not enable versioning: {r.stderr.strip()}")


if __name__ == "__main__":
    GcsBucketEnsure().execute()
