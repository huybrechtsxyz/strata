# Google Cloud CLI (`gcloud`) as a First-Class Integration

- Status: partially-implemented — Phase 1 done, Phase 2 not started
- Date: 2026-07-23
- Related: ADR-0048 (CDK pattern), ADR-0051 (Checkov pattern), ADR-0053 (az CLI pattern)

## Remaining Work

- Phase 2 integrations not built: GCP Secret Manager, RuntimeConfig

## Context and Problem Statement

strata's Google Cloud support is minimal today:

| Component               | How it uses GCP                                               |
| ----------------------- | ------------------------------------------------------------- |
| `terraform_deployer.py` | Assumes Application Default Credentials for `google` provider |

There are **no built-in GCP integrations** — no Secret Manager, no RuntimeConfig, no GCS.
strata never checks whether `gcloud` is installed or authenticated. Operations targeting
GCP fail with unhelpful subprocess errors when credentials are missing.

**The opportunity:** `gcloud` is a single entry point to the entire Google Cloud platform.
One integration that validates availability and authentication gives all current and future
GCP-related features a shared foundation.

## What `gcloud` Enables Beyond Terraform

| Capability               | `gcloud` subcommand                                            | Use in strata                             |
| ------------------------ | -------------------------------------------------------------- | ----------------------------------------- |
| GKE credentials          | `gcloud container clusters get-credentials`                    | Pre-deploy setup for Helm/ArgoCD          |
| Container registry       | `gcloud auth configure-docker`, `gcloud artifacts`             | Container push before deploy              |
| Project context          | `gcloud config get-value project`                              | Confirm active project before deploy      |
| Service account token    | `gcloud auth print-access-token`                               | Token for REST API calls                  |
| Deployment Manager       | `gcloud deployment-manager deployments`                        | Native GCP IaC (alternative to Terraform) |
| Cloud Run                | `gcloud run deploy`                                            | Serverless deploy target                  |
| IAM impersonation        | `gcloud auth print-access-token --impersonate-service-account` | Workload Identity / CI auth               |
| Monitoring / Cloud Trace | `gcloud monitoring`                                            | Health checks, observability              |
| Application credentials  | `gcloud auth application-default login`                        | ADC for Terraform `google` provider       |

## Relationship to Existing GCP Integrations

### Current: SDK-first, `gcloud` as fallback

```
GCPSecretManagerIntegration  → REST API (urllib) → uses gcloud ADC token if available
GCPRuntimeConfigIntegration  → REST API (urllib) → uses gcloud ADC token if available
```

Both integrations independently resolve OAuth tokens — either from service account key
files (`GOOGLE_APPLICATION_CREDENTIALS`) or via Application Default Credentials (ADC),
which itself may depend on `gcloud auth application-default login`.

### Proposed: `GCloudCLIIntegration` as shared auth foundation

```
GCloudCLIIntegration(BaseIntegration)
    COMMAND = "gcloud"
    ensure_available()     → gcloud config get-value account (confirms login + project)
    get_project()          → active project id from gcloud config
    get_access_token()     → gcloud auth print-access-token (cached per session)
    ↓ Used by:
    ├── GKE credential setup              (gcloud container clusters get-credentials)
    ├── Artifact Registry / GCR login     (gcloud auth configure-docker)
    ├── GCPSecretManagerIntegration       (token resolution validation)
    ├── GCPRuntimeConfigIntegration       (token resolution validation)
    └── Cloud Run deployer (future)       (gcloud run deploy)
```

**Key principle:** `GCloudCLIIntegration` is a tool-availability + auth check, not a
replacement for the existing SDK-based integrations. Secret Manager and RuntimeConfig
continue using REST directly (faster, no subprocess per secret). The CLI integration
provides:

1. **Tools view status** — "gcloud ✅ authenticated (project: my-gcp-project)" vs "❌ not authenticated"
2. **Token caching** — `get_access_token()` caches the token for the session, avoids repeated `gcloud` calls
3. **Project context** — confirms the right project is active before deploy
4. **Shared foundation for future GKE/Cloud Run deployers** — no bootstrap complexity

### Impact on existing integrations

| Integration                    | Change needed?                | How                                          |
| ------------------------------ | ----------------------------- | -------------------------------------------- |
| `GCPSecretManagerIntegration`  | No (Phase 1)                  | Continues working as-is                      |
| `GCPRuntimeConfigIntegration`  | No (Phase 1)                  | Continues working as-is                      |
| Terraform `google` provider    | No                            | Uses own ADC chain (env var or `gcloud` ADC) |
| Future GKE/Cloud Run deployers | **Uses GCloudCLIIntegration** | `gcloud` commands via the integration        |

Future (Phase 2): Secret Manager and RuntimeConfig integrations could delegate their
`_get_access_token()` to `GCloudCLIIntegration.get_access_token()` — centralising token
caching and reducing subprocess spawns.

## Design

### `GCloudCLIIntegration(BaseIntegration)`

```python
class GCloudCLIIntegration(BaseIntegration):
    COMMAND = "gcloud"
    CAPABILITIES = []  # capability name: "gcloud"

    def ensure_available(self) -> Tuple[bool, str]:
        """Check gcloud is installed AND authenticated (active account + project configured)."""

    def get_project(self) -> Optional[str]:
        """Return active project id from gcloud config get-value project."""

    def get_account(self) -> Optional[str]:
        """Return the active account from gcloud config get-value account."""

    def get_access_token(self) -> Optional[str]:
        """Return a cached bearer token from gcloud auth print-access-token."""
```

### Configuration YAML

```yaml
integrations:
  - name: gcloud
    type: gcloud_cli
    capabilities: [gcloud]
    required: true        # or false — depends on whether GCP is the target
    validation:
      command: gcloud config get-value account
```

No `endpoints` or `authentication` block needed — `gcloud` uses its own credential chain
(Workload Identity / metadata server → service account key file → interactive login).

### Tools view output

```
gcloud_cli   498.0.0   ✅    (project: my-gcp-project / user@example.com)
```

When not authenticated:
```
gcloud_cli   498.0.0   ⚠️    not authenticated (run: gcloud auth login or gcloud auth application-default login)
```

### What `ensure_available()` checks

1. `gcloud` binary in PATH → if not: "Google Cloud CLI not installed"
2. `gcloud config get-value account` returns a non-empty value → if not: "Not authenticated (run `gcloud auth login`)"
3. `gcloud config get-value project` returns a non-empty value → if not: "No project set (run `gcloud config set project <PROJECT_ID>`)"
4. Returns account and project for display in Tools view

This mirrors the stronger check used by `AzureCLIIntegration` (ADR-0053) and
`AWSCLIIntegration` (ADR-0054): binary-without-auth is useless — operators need to know
immediately before any GCP operations are attempted.

## Implementation Plan

### Phase 1 — Integration + lifecycle scripts ✅
1. `src/strata/integrations/gcloud_cli.py` — `GCloudCLIIntegration` ✅
   - `ensure_available()`: binary + account + project all checked
   - `get_project()`: `GOOGLE_CLOUD_PROJECT` → `CLOUDSDK_CORE_PROJECT` → `gcloud config`
   - `get_account()`: `gcloud config get-value account`
   - `get_access_token()`: `gcloud auth print-access-token` (cached)
   - `run_gcloud(args)`: passthrough
2. Register `gcloud_cli` in `IntegrationFactory._BUILTIN_CLASS_MAP` ✅
3. `IGCloudTool` capability protocol + `"gcloud"` in `CAPABILITY_MAP` ✅
4. Help files: `gcloud_cli.md`, `gcloud_scripts.md` ✅
5. Tests: 35 unit tests ✅

**GCloudScript base class + built-in lifecycle scripts (also Phase 1):**
- `strata.utils.gcloud_script_base.GCloudScript` — mirrors `AzureScript`/`AWSScript`; adds `project()` (3-tier resolution) and `account()` ✅
- `gcloud_gke_credentials.py` — `gcloud container clusters get-credentials`; `GKE_CLUSTER` + `GKE_ZONE`/`GKE_REGION` ✅
- `gcloud_artifact_registry_login.py` — `gcloud auth configure-docker`; `GAR_LOCATION` or `GCR_ENABLE=true` ✅
- `gcloud_gcs_bucket_ensure.py` — idempotent `gcloud storage buckets create` + versioning ✅
- Solution scaffold: `.strata/scripts/gcloud_lifecycle_example.py` ✅
- Guide: `docs/guides/gcloud-lifecycle-scripts.md` ✅

### Phase 2 — GCP secret/variable integrations (future)
- `gcp_secretmanager.py` — `gcloud secrets versions access` / REST; uses `GCloudCLIIntegration` for auth
- `gcp_appconfig.py` or Firestore — GCP-native variable/config store
- See ADR-0056 for full gap analysis

### Phase 3 — Token caching for future GCP integrations (optional)
- Future `GCPSecretManagerIntegration._get_access_token()` delegates to `GCloudCLIIntegration.get_access_token()`
- Single cached token per resource scope per session

## Consequences

### Positive
- **Single source of truth** for Google Cloud CLI availability and auth status
- **Tools view** shows clear "authenticated / not authenticated" with project context at a glance
- **GKE deployers** get a pre-validated `gcloud` foundation — no bootstrap complexity
- **Token caching** reduces subprocess calls when multiple GCP integrations are active
- **Project context** — operators know which GCP project will be targeted before deploy
- **Workload Identity** support — `gcloud` credential chain handles GKE-managed service accounts natively

### Negative
- `gcloud config get-value account` is slightly slower than a simple `gcloud --version` check
- The `google-cloud-sdk` package is large (~500 MB); teams without GCP targets should set `required: false`
- Operators must run both `gcloud auth login` (interactive) and `gcloud auth application-default login` (ADC for Terraform) — two separate commands

### Neutral
- Existing Secret Manager/RuntimeConfig integrations continue to work unchanged (no migration needed)
- `gcloud` component updates (`gcloud components update`) are outside strata's responsibility
