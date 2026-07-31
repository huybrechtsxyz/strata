# Deployment state locking for concurrent deploy protection

- Status: completed
- Date: 2026-06-16

## Context and Problem Statement

When multiple team members work on the same strata repo from different machines, nothing prevents them from running `strata deploy run` simultaneously against the same infrastructure. Terraform locks its own state during `plan`/`apply`, but strata's pipeline spans more than Terraform — it includes lifecycle hooks, Ansible runs, health checks, outputs collection, and policy evaluation. Two overlapping pipelines can corrupt state, produce partial deployments, or trigger conflicting Ansible runs.

The risk is highest in teams with shared environments (staging, production) where CI/CD and human operators both trigger deployments.

## Considered Options

- **Option A**: Rely entirely on Terraform's native backend locking
- **Option B**: Strata-managed lock wrapping the full pipeline, using a remote backend
- **Option C**: Strata-managed lock derived from the provisioner's existing `backend` configuration

## Decision Outcome

Chosen: **Option C — Lock backend derived from the provisioner's existing `WorkspaceIacBackendModel`**, because:

- `WorkspaceIacModel.backend` already carries `type` + `configuration` (including variable/secret references) for every provisioner — no duplicate connection config needed
- The connection details are already resolved by `ResolvedValues` before deploy runs; the lock backend receives concrete values
- A pluggable `BaseLockBackend` ABC follows strata's existing integration pattern without registering in the integration factory (locking is an internal concern, not a user-declared integration)
- Terraform Cloud is fully supported via its workspace lock API with the same credentials Terraform uses

### Consequences

- Good: Full pipeline protection — hooks, Ansible, health checks, and policy evaluation all covered
- Good: Zero duplicate configuration — connection details come from the provisioner backend already in the workspace YAML
- Good: Terraform Cloud users get native workspace lock integration with no extra setup
- Good: Lock metadata in the deployment manifest provides audit trail
- Bad: Lock acquisition adds latency (~1-2s per deploy start)
- Bad: Stale lock cleanup requires heuristics (TTL, heartbeat, force-unlock)
- Bad: Dry-run mode must skip locks explicitly

## Detailed Design

### Connection Source — `WorkspaceIacBackendModel`

The backend connection details are **not** declared on `spec.locking`. They already exist on each provisioner in the workspace YAML as `WorkspaceIacBackendModel`:

```yaml
# workspace.yaml — already supported today
spec:
  provisioners:
    - name: tf_main
      provisioner: terraform
      source:
        path: terraform/
      backend:
        type: azurerm                              # determines which lock backend to use
        configuration:
          resource_group_name: ${var:rg_name}      # resolved before deploy runs
          storage_account_name: ${var:storage_acct}
          container_name: tfstate
          key: prod.terraform.tfstate
```

At deploy time strata:
1. Looks up the provisioner for each stage via `workspace.spec.provisioners[stage.provisioner]`
2. Reads `provisioner.backend.type` → selects the lock backend implementation
3. Reads `provisioner.backend.configuration` (already variable-resolved) → passes to the backend

Multiple stages in the same deployment share the same lock if they use the same provisioner backend. Stages with no backend (Ansible, scripts, local) fall back to `local` file lock.

### YAML Configuration — `spec.locking`

Only behavioural settings live here — no connection config:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: prod-platform
spec:
  locking:
    enabled: true
    strategy: wrap          # wrap | delegate  (default: wrap)
    wait_timeout: 30m       # how long to wait for a held lock (default: 30m)
    force_unlock_after: 8h  # stale lock TTL — auto-release after this (default: 8h)
```

### Backend Selection by Provisioner Type

The lock backend is chosen from `provisioner.backend.type`:

| `backend.type`    | Lock Backend         | Lock Mechanism                                           | Atomic Guarantee | Multi-Machine       | New Dependency              |
| ----------------- | -------------------- | -------------------------------------------------------- | ---------------- | ------------------- | --------------------------- |
| `azurerm`         | `AzurermLockBackend` | Blob lease (60s, auto-renewed via REST)                  | Azure-native     | Yes                 | None (`az` CLI + `urllib`)  |
| `s3`              | `S3LockBackend`      | DynamoDB conditional put (`attribute_not_exists`)        | DynamoDB-native  | Yes                 | `boto3`                     |
| `consul`          | `ConsulLockBackend`  | Session + KV lock (reuses `ConsulIntegration`)           | Consul-native    | Yes                 | None (existing integration) |
| `terraform_cloud` | `TfcLockBackend`     | Workspace lock API (`POST /workspaces/:id/actions/lock`) | TFC-native       | Yes                 | None (`httpx`/`urllib`)     |
| `remote`          | `TfcLockBackend`     | Same as `terraform_cloud` — `remote` is an alias         | TFC-native       | Yes                 | None                        |
| `gcs`             | `GcsLockBackend`     | GCS object conditions (`if-generation-match: 0`)         | GCS-native       | Yes                 | `google-cloud-storage`      |
| `local` / none    | `LocalLockBackend`   | File lock (`LockFileEx` / `fcntl`)                       | OS-native        | No (single machine) | None                        |

### Backend Implementation Details

#### `azurerm` — Azure Blob Lease

Follows the same pattern as `AzureKeyVaultIntegration` and `AzureAppConfigIntegration`: no SDK, uses `az account get-access-token` for a Bearer token and calls the REST API over `urllib.request`.

```
Configuration keys read from provisioner.backend.configuration:
  storage_account_name  → {storage_account}.blob.core.windows.net
  container_name        → container for the lock blob
  (resource_group_name and key are ignored for locking)

Lock blob path: {container}/strata-locks/{deployment_name}.lock

Auth:  az account get-access-token --resource https://storage.azure.com
       → Bearer token, valid 1h, cached

REST:
  Acquire:  PUT  …/{blob}?comp=lease  x-ms-lease-action: acquire  x-ms-lease-duration: 60
  Renew:    PUT  …/{blob}?comp=lease  x-ms-lease-action: renew    x-ms-lease-id: {id}
  Release:  PUT  …/{blob}?comp=lease  x-ms-lease-action: release  x-ms-lease-id: {id}
  Status:   HEAD …/{blob}             → x-ms-lease-state header
```

Lock content (blob body) is JSON: `LockEntry` schema.
Heartbeat thread renews every 30s while the pipeline runs.

#### `terraform_cloud` / `remote` — TFC Workspace Lock API

```
Configuration keys read from provisioner.backend.configuration:
  organization   → TFC organization name
  workspaces.name or workspace → TFC workspace name
  (token resolved from TF_TOKEN_app_terraform_io env var or .terraformrc)

REST:
  Acquire:  POST  https://app.terraform.io/api/v2/workspaces/{id}/actions/lock
            Body: {"reason": "strata deploy run — {deployment} by {holder}"}
  Release:  POST  https://app.terraform.io/api/v2/workspaces/{id}/actions/unlock
  Status:   GET   https://app.terraform.io/api/v2/workspaces/{id}
            → attributes.locked + attributes.locked-by
```

No new dependency — plain `urllib.request` with Bearer token from env var.

**`delegate` strategy with TFC:** When `strategy: delegate` and `backend.type: terraform_cloud`, strata skips explicit locking and relies on TFC's run queue. Only safe when all stages use TFC remote execution (no Ansible or script stages).

#### `s3` — DynamoDB Conditional Put

```
Configuration keys read from provisioner.backend.configuration:
  dynamodb_table  → DynamoDB table for state locking (same table Terraform uses)
  region          → AWS region
  (bucket, key are ignored for locking)

Auth: standard AWS credential chain (env vars, ~/.aws/credentials, instance profile)

Operation:
  Acquire:  PutItem with ConditionExpression="attribute_not_exists(LockID)"
  Release:  DeleteItem
  Status:   GetItem
```

New dependency: `boto3` (optional, only imported when `backend.type: s3`).

#### `consul` — Session + KV Lock

Reuses the existing `ConsulIntegration` (`hashicorp_consul.py`). No new code for the integration layer.

```
Configuration keys read from provisioner.backend.configuration:
  address  → Consul HTTP endpoint (e.g., http://consul:8500)
  (path, scheme, datacenter also read if present)

Lock key path: strata/locks/{deployment_name}
Auth: CONSUL_HTTP_TOKEN env var (same as ConsulIntegration)

Operations: session create → KV acquire → heartbeat → KV release → session destroy
```

#### `local` / no backend — File Lock

Used when no provisioner backend is declared (Ansible stages, script stages) or `backend.type: local`.

```
Lock file path: {work_path}/.strata/locks/{deployment_name}.lock
Auth: none
Operations: platform file lock (fcntl.flock on Unix, LockFileEx on Windows)
```

Only protects against concurrent processes on the same machine. Appropriate for development or single-operator setups.

### Lock Entry Schema

```json
{
  "lock_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "deployment": "prod-platform",
  "holder": "alice@company.com",
  "hostname": "WORKSTATION-A",
  "pid": 12345,
  "acquired_at": "2026-06-16T14:02:01Z",
  "expires_at": "2026-06-16T22:02:01Z",
  "stage": null,
  "reason": "strata deploy run"
}
```

### Strategies

| Strategy   | Behavior                                                                                      | Use When                                                      |
| ---------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `wrap`     | Strata acquires lock before first stage, holds for entire pipeline, releases after last stage | Mixed pipelines (Terraform + Ansible + scripts) — **default** |
| `delegate` | Strata does NOT lock; trusts the backend's native locking (TFC run queue, TF state lock)      | Pure TFC remote execution pipelines only                      |

### Architecture

```
models/
  deployment_model.py          ← Add: DeploymentLockingModel
  deployment_manifest_model.py ← Add: ManifestLockReferenceModel

integrations/
  lock/
    base_lock_backend.py       ← New: BaseLockBackend ABC + LockHandle + LockEntry dataclasses
    lock_azurerm.py            ← New: Azure Blob lease (az CLI + urllib, no new dep)
    lock_tfc.py                ← New: TFC workspace lock API (urllib, no new dep)
    lock_s3.py                 ← New: DynamoDB conditional put (boto3, optional dep)
    lock_consul.py             ← New: Consul session lock (wraps ConsulIntegration)
    lock_gcs.py                ← New: GCS object conditions (google-cloud-storage, optional dep)
    lock_local.py              ← New: File-based lock (no dep)
    lock_factory.py            ← New: selects backend from WorkspaceIacBackendModel.type

commands/
  deploy/
    run_deploy_command.py      ← Modified: acquire/release around _execute_provisioning()
    lock_deploy_command.py     ← New: strata deploy lock status|acquire|release|history

  cli_deploy.py                ← Modified: register `deploy lock` subgroup
```

### Model Additions

```python
class DeploymentLockingModel(PlatformBaseModel):
    """Locking behaviour for spec.locking — connection config comes from provisioner.backend."""
    enabled: bool = False
    strategy: Literal["wrap", "delegate"] = "wrap"
    wait_timeout: str = "30m"
    force_unlock_after: str = "8h"


class ManifestLockReferenceModel(PlatformBaseModel):
    """Lock audit trail recorded in the deployment manifest."""
    lock_id: str
    backend: str        # azurerm | terraform_cloud | s3 | consul | gcs | local
    acquired_at: str    # ISO-8601
    released_at: Optional[str] = None
    holder: str
    hostname: str
```

`DeploymentLockingConfigModel` is **not needed** — connection details come from `WorkspaceIacBackendModel.configuration` which is already resolved.

### Integration Pattern — BaseLockBackend

```python
class BaseLockBackend(ABC):
    """Abstract base for deployment lock backends.

    Instantiated by LockFactory from a resolved WorkspaceIacBackendModel.
    """

    @abstractmethod
    def acquire(self, deployment_name: str, holder: str, reason: str, timeout_seconds: int) -> "LockHandle":
        """Acquire lock. Raises LockTimeoutError if timeout exceeded."""

    @abstractmethod
    def release(self, handle: "LockHandle") -> None:
        """Release a held lock."""

    @abstractmethod
    def status(self, deployment_name: str) -> Optional["LockEntry"]:
        """Return current lock state, or None if unlocked."""

    @abstractmethod
    def force_release(self, deployment_name: str) -> None:
        """Force-release regardless of holder. Emergency use only."""

    @abstractmethod
    def history(self, deployment_name: str, limit: int = 10) -> List["LockEntry"]:
        """Return recent lock events for audit trail."""
```

### Lock Factory

```python
class LockFactory:
    """Selects and instantiates a lock backend from a provisioner's backend config."""

    @staticmethod
    def create(backend_model: Optional[WorkspaceIacBackendModel],
               work_path: Path) -> BaseLockBackend:
        if backend_model is None:
            return LocalLockBackend(work_path)
        match backend_model.type:
            case "azurerm":       return AzurermLockBackend(backend_model.configuration)
            case "terraform_cloud" | "remote":
                                  return TfcLockBackend(backend_model.configuration)
            case "s3":            return S3LockBackend(backend_model.configuration)
            case "consul":        return ConsulLockBackend(backend_model.configuration)
            case "gcs":           return GcsLockBackend(backend_model.configuration)
            case _:               return LocalLockBackend(work_path)
```

### Execution Flow Integration

```python
# RunDeployCommand._execute_provisioning()
def _execute_provisioning(self) -> bool:
    stages = self._get_filtered_stages()
    if not stages:
        return True

    lock_handle = None
    if self._should_lock():
        provisioner = self._resolve_primary_provisioner(stages)
        backend = LockFactory.create(provisioner.backend if provisioner else None,
                                     self._work_path)
        lock_handle = self._acquire_lock(backend, stages)
        if lock_handle is None:
            return False  # timeout or backend error

    try:
        for stage in self._resolve_execution_order(stages):
            success = self._execute_stage(stage)
            if not success and stage.on_failure == "stop":
                return False
        return True
    finally:
        if lock_handle:
            self._release_lock(backend, lock_handle)
```

`_resolve_primary_provisioner` picks the backend from the first Terraform stage in the list, falling back to `local` if none found.

### CLI Commands

```
strata deploy lock status  -f deployment.yaml
strata deploy lock acquire -f deployment.yaml [--reason "..."]
strata deploy lock release -f deployment.yaml [--force]
strata deploy lock history -f deployment.yaml [--last N]
```

**Exit codes:**
- `0` — operation succeeded
- `1` — system failure (backend unreachable, auth error)
- `3` — lock contention (timeout, or force-release of another holder's lock denied without `--force`)

### Dry-Run Behavior

| Mode                          | Lock Behavior                                |
| ----------------------------- | -------------------------------------------- |
| `strata deploy run` (normal)  | Acquire → hold → release                     |
| `strata deploy run --dry-run` | Skip lock entirely (read-only, no mutations) |
| `strata deploy lock status`   | Read-only query, no acquire                  |

### Failure Scenarios

| Scenario                      | Behavior                                                                          |
| ----------------------------- | --------------------------------------------------------------------------------- |
| Deploy succeeds               | Lock released in `finally` block                                                  |
| Deploy fails (stage error)    | Lock released in `finally` block                                                  |
| Process killed (SIGKILL)      | Backend TTL/lease expires → auto-release                                          |
| Machine crashes mid-deploy    | Same as SIGKILL — TTL handles it                                                  |
| Network drops during apply    | Terraform finishes (has its own retry); strata lock heartbeat fails → TTL expires |
| `force_unlock_after` exceeded | Any subsequent acquire auto-releases the stale lock first                         |
| Lock holder is unreachable    | `strata deploy lock release --force` + audit log entry                            |

### Notification Hooks (Future Extension)

```yaml
locking:
  notify_on_contention:
    - type: slack
      channel: "#platform-deploys"
    - type: github_issue
      repo: org/infrastructure
      label: deployment-blocked
```

Not in initial scope — noted as a future enhancement once the core lock mechanism is proven.

## Implementation Phases

### Phase 1 — Core (MVP)
- `DeploymentLockingModel` on `DeploymentSpecModel`
- `BaseLockBackend` ABC + `LockFactory` + `LocalLockBackend` (file-based, no dependencies)
- Lock acquire/release in `RunDeployCommand._execute_provisioning()`
- `strata deploy lock status|release` commands
- `ManifestLockReferenceModel` in manifest output

### Phase 2 — Remote Backends
- `AzurermLockBackend` (blob lease via `az` CLI + `urllib`, no new dependency)
- `TfcLockBackend` (workspace lock API via `urllib`, no new dependency)
- `ConsulLockBackend` (wraps existing `ConsulIntegration`)
- `strata deploy lock history` command

### Phase 3 — Additional Backends + Robustness
- `S3LockBackend` (DynamoDB conditional put — requires `boto3`)
- `GcsLockBackend` (GCS object conditions — requires `google-cloud-storage`)
- Heartbeat thread (renew blob lease / TFC lock during long deploys)
- `force_unlock_after` enforcement
- `strata deploy lock acquire` command
- Notification hooks on contention
- `delegate` strategy for pure-TFC pipelines

## Implementation Plan

### Step 1 — Model: `DeploymentLockingModel`

**File:** `src/strata/models/deployment_model.py`

Add `DeploymentLockingModel` and wire it into `DeploymentSpecModel`:

```python
class DeploymentLockingModel(PlatformBaseModel):
    enabled: bool = False
    strategy: Literal["wrap", "delegate"] = "wrap"
    wait_timeout: str = "30m"
    force_unlock_after: str = "8h"

class DeploymentSpecModel(PlatformBaseModel):
    ...
    locking: Optional[DeploymentLockingModel] = Field(
        None, description="Pipeline locking behaviour for concurrent deploy protection"
    )
```

**Tests:** valid YAML roundtrip, defaults applied when key absent, `strategy` validation.

---

### Step 2 — Model: `ManifestLockReferenceModel`

**File:** `src/strata/models/deployment_manifest_model.py`

```python
class ManifestLockReferenceModel(PlatformBaseModel):
    lock_id: str
    backend: str
    acquired_at: str
    released_at: Optional[str] = None
    holder: str
    hostname: str

class DeploymentManifestSpecModel(PlatformBaseModel):
    ...
    lock: Optional[ManifestLockReferenceModel] = Field(
        None, description="State lock audit trail"
    )
```

**Tests:** model construction, serialization, optional field absent.

---

### Step 3 — ABC: `BaseLockBackend` + dataclasses

**File:** `src/strata/integrations/lock/base_lock_backend.py`

```python
@dataclass
class LockEntry:
    lock_id: str
    deployment: str
    holder: str
    hostname: str
    pid: int
    acquired_at: str
    expires_at: str
    reason: str

@dataclass
class LockHandle:
    lock_id: str
    backend_type: str
    acquired_at: str
    _backend_data: Dict[str, Any]   # backend-specific (lease_id, session_id, etc.)

class BaseLockBackend(ABC):
    @abstractmethod
    def acquire(self, deployment_name, holder, reason, timeout_seconds) -> LockHandle: ...
    @abstractmethod
    def release(self, handle: LockHandle) -> None: ...
    @abstractmethod
    def status(self, deployment_name) -> Optional[LockEntry]: ...
    @abstractmethod
    def force_release(self, deployment_name) -> None: ...
    @abstractmethod
    def history(self, deployment_name, limit=10) -> List[LockEntry]: ...
```

**Tests:** dataclass construction, ABC cannot be instantiated.

---

### Step 4 — `LocalLockBackend`

**File:** `src/strata/integrations/lock/lock_local.py`

- Lock file at `{work_path}/.strata/locks/{deployment_name}.lock`
- Uses `fcntl.flock` (Unix) / `msvcrt.locking` or `LockFileEx` (Windows)
- Lock entry JSON written to the file; read back for `status()`
- `history()` — reads `{deployment_name}.lock.history` append-only NDJSON
- On acquire failure, polls every 5s up to `timeout_seconds`

**Tests:** acquire/release cycle, contention (second acquire blocks), force-release, status read, history append.

---

### Step 5 — `LockFactory`

**File:** `src/strata/integrations/lock/lock_factory.py`

```python
class LockFactory:
    @staticmethod
    def create(backend_model: Optional[WorkspaceIacBackendModel],
               work_path: Path) -> BaseLockBackend:
        if backend_model is None:
            return LocalLockBackend(work_path)
        match backend_model.type:
            case "azurerm":                       return AzurermLockBackend(backend_model.configuration)
            case "terraform_cloud" | "remote":    return TfcLockBackend(backend_model.configuration)
            case "s3":                            return S3LockBackend(backend_model.configuration)
            case "consul":                        return ConsulLockBackend(backend_model.configuration)
            case "gcs":                           return GcsLockBackend(backend_model.configuration)
            case _:                               return LocalLockBackend(work_path)
```

Phase 1 only includes `LocalLockBackend`; other branches raise `NotImplementedError` with a clear message until Phase 2.

**Tests:** factory returns correct type per backend string, unknown type falls back to local.

---

### Step 6 — Duration parser utility

**File:** `src/strata/utils/duration.py`

Parses `"30m"`, `"8h"`, `"2h30m"`, `"60s"` into seconds. Used by `RunDeployCommand` to interpret `wait_timeout` and `force_unlock_after`.

```python
def parse_duration(value: str) -> int:
    """Parse duration string to seconds. Supports h/m/s suffixes."""
```

**Tests:** `"30m"` → 1800, `"8h"` → 28800, `"2h30m"` → 9000, `"60s"` → 60, invalid → `ValueError`.

---

### Step 7 — Wire locking into `RunDeployCommand`

**File:** `src/strata/commands/deploy/run_deploy_command.py`

Add helper methods:

```python
def _should_lock(self) -> bool:
    """Check if locking is enabled and not a dry-run."""

def _resolve_lock_backend(self, stages) -> BaseLockBackend:
    """Look up the first stage's provisioner backend, pass to LockFactory."""

def _acquire_lock(self, backend, stages) -> Optional[LockHandle]:
    """Acquire with wait_timeout. Log on contention. Return None on timeout."""

def _release_lock(self, backend, handle) -> None:
    """Release lock, log outcome. Never raises — errors are logged."""
```

Wrap `_execute_provisioning()` stage loop in `try/finally` with acquire/release.

After deploy, record `ManifestLockReferenceModel` on the manifest spec.

**Tests:** mock `LockFactory.create`, verify acquire called before stages, release called in finally (success + failure), dry-run skips lock, locking disabled skips lock. Follow the existing `TestEvaluatePhasePolices` pattern with `_make_run_command` helper.

---

### Step 8 — CLI: `strata deploy lock` subgroup

**Files:**
- `src/strata/commands/deploy/lock_deploy_command.py` — `LockStatusCommand`, `LockReleaseCommand`
- `src/strata/commands/cli_deploy.py` — register `deploy lock` Click group

```
strata deploy lock status  -f deployment.yaml [--output text|json]
strata deploy lock release -f deployment.yaml [--force]
```

`status` loads the deployment + workspace, creates the lock backend via `LockFactory`, calls `backend.status()`, renders output.

`release` does the same, calls `backend.release()` or `backend.force_release()` with `--force`.

**Tests:** CliRunner invoke, mock backend, verify output format, exit codes (0 success, 1 error, 3 contention).

---

### Step 9 — `AzurermLockBackend` (Phase 2)

**File:** `src/strata/integrations/lock/lock_azurerm.py`

- Reads `storage_account_name`, `container_name` from `backend_model.configuration`
- Auth via `az account get-access-token --resource https://storage.azure.com`
- Blob lease REST: acquire (60s duration), renew (30s interval), release
- Lock blob at `{container}/strata-locks/{deployment_name}.lock`
- Body is JSON `LockEntry`
- Heartbeat thread (`threading.Thread(daemon=True)`) renews lease every 30s

**Tests:** mock `subprocess` (for `az`) and `urllib.request` (for REST), verify lease acquire/renew/release flow, auth token caching, timeout polling.

---

### Step 10 — `TfcLockBackend` (Phase 2)

**File:** `src/strata/integrations/lock/lock_tfc.py`

- Reads `organization`, workspace name from `backend_model.configuration`
- Auth via `TF_TOKEN_app_terraform_io` env var
- Resolves workspace ID: `GET /api/v2/organizations/{org}/workspaces/{name}`
- Lock: `POST /api/v2/workspaces/{id}/actions/lock` with reason body
- Unlock: `POST /api/v2/workspaces/{id}/actions/unlock`
- Status: `GET /api/v2/workspaces/{id}` → `attributes.locked`, `attributes.locked-by`

**Tests:** mock `urllib.request`, verify workspace lookup, lock/unlock/status API calls, missing token error.

---

### Step 11 — `ConsulLockBackend` (Phase 2)

**File:** `src/strata/integrations/lock/lock_consul.py`

- Wraps existing `ConsulIntegration` for HTTP connectivity
- Lock key: `strata/locks/{deployment_name}`
- Session create → KV acquire → release → session destroy
- Auth via `CONSUL_HTTP_TOKEN`

**Tests:** mock `ConsulIntegration`, verify session lifecycle and KV operations.

---

### Step 12 — `strata deploy lock history` (Phase 2)

**File:** `src/strata/commands/deploy/lock_deploy_command.py` — add `LockHistoryCommand`

```
strata deploy lock history -f deployment.yaml [--last N] [--output text|json]
```

Calls `backend.history()`, renders as table or JSON.

**Tests:** CliRunner invoke with mock backend returning history entries.

---

### Step 13 — Docs + schema update

**Files:**
- `docs/platform/commands.md` — add `deploy lock status|release|history`
- `docs/config/deployment.md` — add `spec.locking` section with examples per backend
- JSON schema (if generated) — add `locking` to deployment spec

---

### Implementation order summary

| Step | What                          | Layer        | Depends on | New files |
| ---- | ----------------------------- | ------------ | ---------- | --------- |
| 1    | `DeploymentLockingModel`      | models       | —          | 0         |
| 2    | `ManifestLockReferenceModel`  | models       | —          | 0         |
| 3    | `BaseLockBackend` ABC         | integrations | —          | 1         |
| 4    | `LocalLockBackend`            | integrations | 3          | 1         |
| 5    | `LockFactory`                 | integrations | 3, 4       | 1         |
| 6    | Duration parser               | utils        | —          | 1         |
| 7    | Wire into `RunDeployCommand`  | commands     | 1, 2, 5, 6 | 0         |
| 8    | `deploy lock` CLI commands    | commands     | 5          | 1         |
| 9    | `AzurermLockBackend`          | integrations | 3          | 1         |
| 10   | `TfcLockBackend`              | integrations | 3          | 1         |
| 11   | `ConsulLockBackend`           | integrations | 3          | 1         |
| 12   | `deploy lock history` command | commands     | 8          | 0         |
| 13   | Docs + schema                 | docs         | 8          | 0         |

Steps 1–8 = Phase 1 (MVP).  Steps 9–12 = Phase 2 (remote backends).  Step 13 spans both.

## Related

- [How Deployment Locking Works](../guides/how-deployment-locking-works.md) — user-facing
  walkthrough of what locking protects and how to manage stuck locks
