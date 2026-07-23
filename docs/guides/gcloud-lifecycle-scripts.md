# GCP Lifecycle Scripts

Lifecycle scripts are Python files strata runs before or after deploy stages. The
`GCloudScript` base class gives scripts pre-wired access to the Google Cloud CLI.

## Quick start — use a built-in script

```yaml
lifecycle:
  pre_deploy:
    scripts:
      - strata://gcloud_gke_credentials.py          # gcloud container clusters get-credentials
      - strata://gcloud_artifact_registry_login.py  # gcloud auth configure-docker

  pre_provision:
    scripts:
      - strata://gcloud_gcs_bucket_ensure.py        # gcloud storage buckets create (idempotent)
```

---

## Built-in scripts

### `gcloud_gke_credentials.py`

Runs `gcloud container clusters get-credentials` before Helm, ArgoCD, or Flux stages.

Required: `GKE_CLUSTER` + one of `GKE_ZONE` / `GKE_REGION`

| Variable               | Description                                    |
| ---------------------- | ---------------------------------------------- |
| `GKE_CLUSTER`          | GKE cluster name                               |
| `GKE_ZONE`             | Zonal cluster zone (e.g. `us-central1-a`)      |
| `GKE_REGION`           | Regional cluster region (e.g. `us-central1`)   |
| `GOOGLE_CLOUD_PROJECT` | GCP project (auto-resolved from gcloud config) |
| `GKE_CONTEXT_NAME`     | Override kubeconfig context name               |
| `GKE_INTERNAL_IP`      | Set to `true` for private endpoint             |

---

### `gcloud_artifact_registry_login.py`

Runs `gcloud auth configure-docker` to configure Docker credential helpers for
Artifact Registry and/or Container Registry.

Required: `GAR_LOCATION` (Artifact Registry) **or** `GCR_ENABLE=true` (legacy GCR)

| Variable       | Description                                            |
| -------------- | ------------------------------------------------------ |
| `GAR_LOCATION` | AR location (e.g. `europe-west1`, `us`, `us-central1`) |
| `GCR_ENABLE`   | Set to `true` to also configure `gcr.io`               |
| `GCR_HOST`     | GCR host override (default: `gcr.io`)                  |

---

### `gcloud_gcs_bucket_ensure.py`

Runs `gcloud storage buckets create --no-fail-on-existing-bucket` — idempotent.
Use before Terraform deployments that need a remote state bucket.

Required: `GCS_BUCKET`

| Variable               | Default       | Description                               |
| ---------------------- | ------------- | ----------------------------------------- |
| `GCS_BUCKET`           | —             | Bucket name (without `gs://`)             |
| `GOOGLE_CLOUD_PROJECT` | gcloud config | GCP project                               |
| `GCS_LOCATION`         | `US`          | Bucket location                           |
| `GCS_STORAGE_CLASS`    | `STANDARD`    | Storage class                             |
| `GCS_VERSIONING`       | `false`       | Set to `true` to enable object versioning |
| `GCS_LABELS`           | —             | Comma-separated `key=value` labels        |

---

## Write a custom script

```python
# .strata/scripts/pre_deploy_custom.py
from strata.utils.gcloud_script_base import GCloudScript

class MyPreDeploy(GCloudScript):
    def run(self):
        project = self.project()                      # exits if not set
        bucket = self.require_env("GCS_BUCKET")

        result = self.run_gcloud([
            "storage", "ls", f"gs://{bucket}", "--project", project,
        ])
        self.exit_on_failure(result, "gcloud storage ls")
        self.log(f"Bucket '{bucket}' accessible in project '{project}'")

if __name__ == "__main__":
    MyPreDeploy().execute()
```

---

## `GCloudScript` reference

| Method                           | Description                                                    |
| -------------------------------- | -------------------------------------------------------------- |
| `run_gcloud(args, timeout=120)`  | Run `gcloud` subcommand; returns `subprocess.CompletedProcess` |
| `exit_on_failure(result, label)` | `sys.exit(1)` if `returncode != 0`                             |
| `require_env(name)`              | Return env var or `sys.exit(1)`                                |
| `env(name, default="")`          | Return env var with default                                    |
| `project()`                      | `GOOGLE_CLOUD_PROJECT` → `gcloud config` → exit(1)             |
| `account()`                      | Active account from gcloud config                              |
| `get_access_token()`             | Bearer token from `gcloud auth print-access-token`             |
| `workspace_path()`               | Path from `STRATA_WORKSPACE_PATH`                              |
| `log(msg)`                       | Print to stderr — visible in strata console output             |
