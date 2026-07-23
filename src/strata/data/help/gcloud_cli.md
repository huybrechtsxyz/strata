# Google Cloud CLI Integration

The Google Cloud CLI integration (`type: gcloud_cli`) is the shared foundation for all
gcloud-based operations in strata. It checks that `gcloud` is installed, **authenticated**,
and has an **active project** set.

Installation
```
# macOS
brew install --cask google-cloud-sdk

# Linux / Windows
https://cloud.google.com/sdk/docs/install
```

Verify install
```
gcloud --version
gcloud auth list
gcloud config get-value project
```

Authentication

| Method | Setup |
|---|---|
| **Interactive login** (local dev) | `gcloud auth login` |
| **Application Default Credentials** | `gcloud auth application-default login` (required for Terraform google provider) |
| **Service account key** | `export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json` |
| **Workload Identity** | Automatic on GKE/Cloud Run — no setup needed |

> **Note:** `gcloud auth login` and `gcloud auth application-default login` are **two separate commands** — run both for local development.

Key environment variables

| Variable | Purpose |
|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account key JSON |
| `GOOGLE_CLOUD_PROJECT` | Override active project ID |
| `CLOUDSDK_CORE_PROJECT` | Alternative project override |

Configuration YAML

```yaml
integrations:
  - name: gcloud
    type: gcloud_cli
    capabilities: [gcloud]
    required: true
```

What `ensure_available()` checks

1. `gcloud` binary in PATH — if not: "Google Cloud CLI not installed"
2. `gcloud config get-value account` returns a non-empty value — if not: "not authenticated"
3. `gcloud config get-value project` is set — if not: "no project set"

Tools view shows:
- ✅ `gcloud_cli — Authenticated (user@example.com / project: my-gcp-project)`
- ❌ `gcloud_cli — not authenticated (run: gcloud auth login)`
- ⚠️ `gcloud_cli — no project set (run: gcloud config set project <ID>)`

Project management
```
gcloud projects list                          # list all projects
gcloud config set project <PROJECT_ID>        # set active project
gcloud config get-value project               # current project
gcloud auth list                              # list authenticated accounts
```

Docs
- Google Cloud CLI: https://cloud.google.com/sdk/docs
- Install guide: https://cloud.google.com/sdk/docs/install
- Authentication: https://cloud.google.com/sdk/docs/authorizing
