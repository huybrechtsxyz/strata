# How Deployment Locking Works

> A plain-language walkthrough of strata's deployment state locking — what it protects,
> how it behaves across different backends, and how to manage stuck locks.

---

## Why locking exists

Running `strata deploy run` mutates live infrastructure.  If two pipelines target the
same deployment at the same time — a hotfix and a scheduled release, for example — they
will race to write Terraform state and you will end up with corrupted state or a
partially-applied change.

Locking serialises those runs.  The first caller acquires the lock; the second waits
(or fails fast) until it is released.

---

## How the lock fits into `deploy run`

```
strata deploy run
│
├─ _should_lock()         ← is locking enabled? is strategy != "delegate"?
│
├─ _resolve_lock_backend()← find the Terraform backend for the stages being run
│
├─ _acquire_lock()        ← write the lock record, start polling if already held
│   │
│   └─ (blocks up to wait_timeout, then exits with code 3)
│
├─ [ stage 1 … stage N ]  ← all stages run inside a try/finally
│
└─ _release_lock()        ← always runs, even on failure or Ctrl-C
```

The lock is held for the **entire pipeline** — from the first stage until the last one
finishes or fails.

---

## Configuring locking

Add a `locking` block to your deployment's `spec`:

```yaml
spec:
  locking:
    enabled: true
    strategy: wrap            # "wrap" | "delegate"
    wait_timeout: "5m"        # how long to poll before giving up
```

| Field          | Default | Description                                                                                                                        |
| -------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `enabled`      | `false` | Set to `true` to activate locking                                                                                                  |
| `strategy`     | `wrap`  | `wrap` = strata manages the lock; `delegate` = trust the backend's own locking (e.g. TFC run queue) and skip strata's lock entirely |
| `wait_timeout` | `"5m"`  | Duration to wait for a held lock before aborting. Accepts `s`, `m`, `h` (e.g. `"90s"`, `"2m"`, `"1h"`)                             |

---

## Backend selection

strata selects the lock backend automatically from the Terraform backend configured on
the provisioner your stages reference.  No extra config is needed.

| Terraform backend            | Lock backend                      | Where the lock is stored                                                         |
| ---------------------------- | --------------------------------- | -------------------------------------------------------------------------------- |
| `azurerm`                    | Azure Blob lease                  | A blob named `strata-lock/{deployment}.lock` in the same container as your state |
| `terraform_cloud` / `remote` | Terraform Cloud workspace lock    | TFC's native lock API                                                            |
| `consul`                     | Consul KV session                 | `strata/locks/{deployment}`                                                      |
| `local`                      | File lock                         | `.strata/locks/local-{deployment}.lock` on disk                                  |
| `s3` / `gcs`                 | *(Phase 3 — not yet implemented)* | —                                                                                |

---

## End-to-end example — azurerm backend

### 1. Deployment file

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: xyz-production
spec:
  locking:
    enabled: true
    strategy: wrap
    wait_timeout: "2m"
  stages:
    - name: production
      provisioner: xyz-infra
```

The provisioner `xyz-infra` uses an `azurerm` Terraform backend:

```yaml
backend:
  type: azurerm
  configuration:
    resource_group_name: rg-strata-state
    storage_account_name: strastate001
    container_name: tfstate
    key: xyz-production.tfstate
```

### 2. Lock acquired

```
$ strata deploy run --deployment xyz-production

[INFO] Acquiring deployment lock for 'xyz-production' (azurerm) ...
[INFO] Lock acquired: a3f2c1d0-… held by ci-bot@runner-07
```

strata:
1. Obtains an Azure bearer token via `az account get-access-token`
2. Creates (or opens) the blob `strata-lock/xyz-production.lock`
3. Acquires an **infinite lease** on the blob — no other process can write or delete it
4. Writes the lock record as blob content:

```json
{
  "lock_id": "a3f2c1d0-...",
  "deployment": "xyz-production",
  "holder": "ci-bot@runner-07",
  "hostname": "runner-07",
  "pid": 12345,
  "acquired_at": "2026-06-16T08:31:00Z",
  "reason": "deploy run"
}
```

5. Appends the same record to the local history file:
   `.strata/locks/azurerm-xyz-production.lock.history`

### 3. Concurrent run is blocked

A second pipeline runs at the same time:

```
$ strata deploy run --deployment xyz-production

[INFO] Acquiring deployment lock for 'xyz-production' (azurerm) ...
[WARN] Lock held by 'ci-bot@runner-07' — retrying (45s / 120s) ...
[WARN] Lock held by 'ci-bot@runner-07' — retrying (55s / 120s) ...
```

Azure returns `409 Conflict` on every lease attempt.  strata reads the blob to show who
holds the lock, then sleeps and retries until `wait_timeout` elapses.  If the timeout
expires, it exits with **code 3** (validation/lock failure).

### 4. Stages execute

All stages run while the lease is held.  The manifest records `lock_ref.lock_id` so you
can correlate a run record with its lock history.

### 5. Lock released

```
[INFO] Deployment stages complete.
[INFO] Lock released: a3f2c1d0-…
```

strata breaks the lease and deletes the blob.  A `released_at` timestamp is appended to
the history file.

---

## What happens when a run crashes mid-deploy

The `_release_lock()` call is inside a `finally` block — it runs even if a stage fails
or the process receives a signal.

If the process is **killed hard** (OOM kill, node reboot):

- **azurerm** — the infinite lease remains.  `strata deploy lock release --force` breaks
  it via the management API.
- **TFC** — TFC's own lock record stays.  `--force` unlocks via the Runs API.
- **Consul** — the session TTL is 8 hours.  If strata does not renew it, Consul
  automatically deletes the key when the TTL expires.  `--force` destroys the session
  immediately.
- **local** — the OS releases the `flock` when the process dies.

---

## Manual lock management

### Check who holds the lock

```bash
strata deploy lock status --deployment xyz-production
```

```
Deployment : xyz-production
Lock ID    : a3f2c1d0-...
Holder     : ci-bot@runner-07
Hostname   : runner-07
PID        : 12345
Acquired   : 2026-06-16T08:31:00Z
Reason     : deploy run
```

Returns exit code 0 if locked, 3 if not.

### Force-release a stuck lock

```bash
strata deploy lock release --deployment xyz-production --force
```

Use this when a pipeline died and left the lock held.  The command writes an audit entry
to the history file before breaking the lock.

### View the audit trail

```bash
strata deploy lock history --deployment xyz-production --last 10
```

Reads `.strata/locks/{backend}-{deployment}.lock.history` (NDJSON) and prints the most
recent events in reverse chronological order.

---

## The `delegate` strategy

Some backends handle their own concurrency:

- **Terraform Cloud** — the TFC run queue serialises applies natively.
- **Atlantis** — holds a per-repo lock while a plan/apply is in progress.

Set `strategy: delegate` to skip strata's lock entirely and rely on the backend's
built-in mechanism:

```yaml
spec:
  locking:
    enabled: true
    strategy: delegate
```

strata will not acquire, poll, or release any lock.  `strata deploy lock status` will
return "not locked" even when a TFC run is in progress — use the TFC UI or API to check
run queue status in that case.

---

## Dry runs never lock

`strata deploy run --dry-run` skips locking unconditionally.  Dry runs are read-only
planning operations and should never block or be blocked by an in-flight apply.
