# GCP Lifecycle Scripts

`GCloudScript` is the GCP equivalent of `AzureScript` / `AWSScript` — a Python base
class for lifecycle scripts in `.strata/scripts/` with pre-wired Google Cloud CLI helpers.

## Three built-in scripts (no code needed)

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

### `gcloud_gke_credentials.py`
Required: `GKE_CLUSTER`, one of `GKE_ZONE` or `GKE_REGION`
Optional: `GOOGLE_CLOUD_PROJECT` (auto-resolved), `GKE_CONTEXT_NAME`, `GKE_INTERNAL_IP=true`

### `gcloud_artifact_registry_login.py`
Required: `GAR_LOCATION` or `GCR_ENABLE=true`
Optional: `GCR_HOST` (default: gcr.io)

### `gcloud_gcs_bucket_ensure.py`
Required: `GCS_BUCKET`
Optional: `GOOGLE_CLOUD_PROJECT`, `GCS_LOCATION=US`, `GCS_STORAGE_CLASS=STANDARD`, `GCS_VERSIONING=true`, `GCS_LABELS=k=v,k2=v2`

## Write a custom script

```python
# .strata/scripts/my_gcp_script.py
from strata.utils.gcloud_script_base import GCloudScript

class MyScript(GCloudScript):
    def run(self):
        project = self.project()    # GOOGLE_CLOUD_PROJECT → gcloud config → exit(1)
        bucket = self.require_env("GCS_BUCKET")
        result = self.run_gcloud(["storage", "ls", f"gs://{bucket}"])
        self.exit_on_failure(result, "gcloud storage ls")

if __name__ == "__main__":
    MyScript().execute()
```

## `GCloudScript` reference

| Method | Description |
|---|---|
| `run_gcloud(args)` | Run `gcloud` subcommand; returns `subprocess.CompletedProcess` |
| `exit_on_failure(result)` | `sys.exit(1)` if `returncode != 0` |
| `require_env(name)` | Get env var or `sys.exit(1)` |
| `env(name, default="")` | Get env var with default |
| `project()` | `GOOGLE_CLOUD_PROJECT` → `gcloud config` → exit(1) |
| `account()` | Active account from gcloud config |
| `get_access_token()` | Bearer token via `gcloud auth print-access-token` |
| `workspace_path()` | Path from `STRATA_WORKSPACE_PATH` |
| `log(msg)` | Print to stderr (visible in strata output) |
