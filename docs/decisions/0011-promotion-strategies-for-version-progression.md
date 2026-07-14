# Promotion strategies for version progression across environments

- Status: Accepted (partially implemented — see Implementation Status)
- Date: 2026-06-23
- Revised: 2026-07-14

## Context and Problem Statement

Strata manages deployments across multiple environments (dev, test, acceptance, production)
and multiple tenants. Version changes — whether Terraform landscape references or Helm
chart versions — must progress through environments in a controlled, auditable way.

Today, promotion is entirely manual: an operator edits a `spec.overrides.remotes[].reference`
or a module `chart_version` in an environment YAML file, commits, and deploys. There is no
guardrail preventing a direct jump to production, no canary mechanism, no rollback tracking,
and no visibility into what version is running where across the fleet.

Version truth is **scattered**: base image/chart versions live in `stack/*.yaml`, git refs
default in `configuration.spec.remotes[]`, and deviations live in per-environment
`spec.overrides`. Answering "what version runs in prd?" requires resolving the full merge
chain. This ADR resolves that by introducing **version files** (named release snapshots)
and an optional **promotion system** that automates progression through rings.

The platform needs a structured version and promotion system that:

- Captures the complete version set for a release in a single file
- Supports manual deployment (explicit version reference) without requiring promotion configuration
- Defines allowed progressions through ordered **rings** (dev → sandbox → prod)
- Supports gradual rollout via **waves** (label-based deployment grouping)
- Integrates with the existing git-based workflow (branch, commit, PR, merge, deploy)
- Provides visibility into current versions and in-flight promotions
- Supports rollback using recorded previous state

## Related Work

**[ADR 0017: Tag-based release workflow](0017-tag-based-release-workflow-option-c.md)** addresses
the **release lifecycle**: how versions move from code into tagged releases. This ADR addresses
**promotion**: how tagged releases are deployed progressively across environments.

- **ADR 0017** (Release): Commits → Tests → `tested` tag → Release branch → `vX.Y.Z` tag
- **This ADR** (Promotion): Version file → Lock → Ring progression → Waves → Production

---

## Design Overview

The system is layered. Each layer builds on the previous. Users opt in to as much
automation as they need — from fully manual to fully automated promotion.

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: VERSION FILES          strata versions add            │
│  Layer 2: LOCKING                strata versions lock           │
│  Layer 3: MANUAL DEPLOY          strata deploy run -f ... -v .. │
│  Layer 4: PROMOTIONS             strata promote <ring> <file>   │
│  Layer 5: AUTO-RESOLVE           strata deploy run -f ...       │
└─────────────────────────────────────────────────────────────────┘
```

| Layer | Requires         | What it adds                                       |
| ----- | ---------------- | -------------------------------------------------- |
| 1     | Nothing          | Pins snapshot — a named release                    |
| 2     | Layer 1          | Tamper protection (hash inside file)               |
| 3     | Layer 2          | Deploy with explicit `-v` flag — no config needed  |
| 4     | Layer 2 + config | Automated ring progression + wave rollout          |
| 5     | Layer 4          | Deploy auto-resolves version from promotion config |

---

## Layer 1 — Version Files

A version file is a `kind: version` snapshot of a complete release. It captures all pins
(images, charts, remotes, tools) in a single, readable YAML file.

### Creating a version file

```bash
# Generate from current workspace state (scan what's pinnable)
strata versions add --workspace-file stack/workspace.yaml -o versions/v1.0.1.yaml

# Generate from a previous version (copy + update specific pins)
strata versions add --version-file versions/v1.0.0.yaml -o versions/v1.0.1.yaml
```

### Version file format

```yaml
# versions/v1.0.1.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: release-1.0.1
  annotations:
    description: "Adds dark mode, fixes auth timeout"
spec:
  version: 1.0.1                 # canonical version identifier
  pins:
    images:
      infisical:
        app:    v1.2.0
        worker: v1.2.0
        redis:  "7.2.0"
      traefik: v3.0.0           # single-service module — flat value
    charts:
      traefik: "28.2.0"
    remotes:
      iac_core: v2.6.0
    tools:
      terraform: "~> 1.9"
```

**Fields:**

| Field               | Required | Description                                     |
| ------------------- | -------- | ----------------------------------------------- |
| `spec.version`      | yes      | Canonical version string — the release identity |
| `spec.pins.images`  | no       | Image tags per module/service                   |
| `spec.pins.charts`  | no       | Helm chart versions per module                  |
| `spec.pins.remotes` | no       | Git references per remote                       |
| `spec.pins.tools`   | no       | Tool version constraints per provisioner        |

**Who creates them:** a human operator, a CI pipeline, or a renovate-style bot. Strata does
not care — it reads them. `strata validate <file>` validates the schema.

### Pin syntax

**Images — single-service module** (value is a string):
```yaml
images:
  traefik: v3.0.0       # module name = service name
```

**Images — multi-service module** (value is a dict):
```yaml
images:
  infisical:
    app:    v1.2.0       # module: infisical, service: app
    worker: v1.2.0       # module: infisical, service: worker
```

**Tools — version constraints** (same syntax as Terraform `required_version`):

| Constraint | Meaning                 |
| ---------- | ----------------------- |
| `~> 1.9`   | `>= 1.9.0, < 2.0.0`     |
| `~> 1.9.2` | `>= 1.9.2, < 1.10.0`    |
| `>= 1.9`   | minimum, no upper bound |
| `1.9.2`    | exact match             |

---

## Layer 2 — Locking

Locking adds tamper protection to a version file. Once locked, any modification is
detectable at build/deploy time.

```bash
strata versions lock versions/v1.0.1.yaml
```

This computes a sha256 hash of the file's pin content and writes it into the file:

```yaml
# versions/v1.0.1.yaml — after locking
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: release-1.0.1
spec:
  version: 1.0.1
  hash: "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
  pins:
    images:
      infisical:
        app: v1.2.0
        ...
```

**Rules:**
- `spec.hash` is computed over the `spec.pins` content (deterministic serialisation)
- Editing any pin after locking makes the hash invalid
- `strata validate` verifies the hash; mismatch → exit 3
- `strata deploy -v` refuses to deploy an unlocked version in strict mode
- Re-locking after intentional edits: `strata versions lock --force`

A locked version file is the **immutable release artifact**. It lives in git, is PR-approved,
and its hash proves integrity at every subsequent step.

---

## Layer 3 — Manual Deploy with Version

The simplest deployment mode. No promotion configuration required. Pass the version file
directly on the command line.

```bash
strata deploy run -f deploy/landscape-prod.yaml -v versions/v1.0.1.yaml
```

**What happens:**
1. Strata reads the version file
2. Verifies `spec.hash` (if present — required in strict mode)
3. Applies pins to the deployment (images → module overrides, remotes → references, etc.)
4. Deploys

The deployment file does NOT need `spec.promotion`. This is fully manual version management:
the operator chooses which version to deploy, when, and where.

```yaml
# deploy/landscape-prod.yaml — no promotion config needed
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: landscape-prod
spec:
  workspace:
    source: "@config/stack/workspace.yaml"
  environments:
    - file: "@config/environments/production.yaml"
  # No spec.promotion — manual mode
```

**When to use:** single-workspace setups, early-stage projects, or any deployment where
you want direct control without the promotion machinery.

---

## Layer 4 — Promotions

Promotions automate version progression through rings. They require configuration on the
`kind: configuration` file and opt-in on each deployment.

### Promotion configuration

```yaml
# config/config.yaml (kind: configuration)
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: haven
spec:
  promotions:
    - name: customer-apps
      versions_path: "@config/versions/customer/"
      rings:
        - name: dev
          order: 1
        - name: sandbox
          order: 2
        - name: prod
          order: 3
          waves:
            - id: 1
              match_labels:
                tier: enterprise
            - id: 2
              match_labels:
                tier: growth

    - name: landscape-infra
      versions_path: "@config/versions/landscape/"
      rings:
        - name: dev
          order: 1
        - name: prod
          order: 2
```

**Promotion fields:**

| Field                  | Required | Description                                             |
| ---------------------- | -------- | ------------------------------------------------------- |
| `name`                 | yes      | Promotion identifier — referenced by deployments        |
| `versions_path`        | yes      | Directory where version files and ring locks live       |
| `rings`                | yes      | Ordered list of rings                                   |
| `rings[].name`         | yes      | Ring identifier                                         |
| `rings[].order`        | yes      | Progression sequence (lower = earlier)                  |
| `rings[].waves`        | no       | Wave definitions for gradual rollout                    |
| `waves[].id`           | yes      | Wave number — determines deploy order and lock filename |
| `waves[].match_labels` | yes      | Label selector matched against deployment `meta.labels` |

### Deployment opt-in

```yaml
# deploy/customer-acme.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: customer-acme
  labels:
    tier: enterprise         # → matched to wave 1 by promotion config
    customer: acme
spec:
  promotion:
    name: customer-apps      # references config.spec.promotions[].name
    ring: prod               # which ring this deployment belongs to
  workspace:
    source: "@config/stack/workspace.yaml"
  environments:
    - file: "@config/environments/production.yaml"
```

A deployment with `spec.promotion` is **managed** — strata auto-resolves its version from
the ring lock. A deployment without `spec.promotion` is **unmanaged** — use `-v` flag.

### Promoting a version

```bash
# Lock the version file first (if not already locked)
strata versions lock versions/customer/v2.1.0.yaml

# Promote dev ring to v2.1.0
strata promote dev versions/customer/v2.1.0.yaml --promotion customer-apps
# → looks up customer-apps → versions_path: @config/versions/customer/
# → writes @config/versions/customer/dev.lock.yaml
# → git checkout -b promote/dev-2.1.0
# → commits the lock file
# → pushes
# → prints: gh pr create ...

# After validation, promote prod
strata promote prod versions/customer/v2.1.0.yaml --promotion customer-apps
# → writes @config/versions/customer/prod.lock.yaml
```

If only one promotion is configured, `--promotion` can be omitted.

### Ring lock file (version-lock)

The ring lock is a small pointer file written by `strata promote`. It records which version
file the ring is currently on.

```yaml
# @config/versions/customer/prod.lock.yaml — written by strata promote
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prod
spec:
  source: "v2.1.0.yaml"                    # relative to versions_path
  hash: "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
  version: "2.1.0"                          # copied from meta.version for quick reads
  previous:
    source: "v2.0.0.yaml"
    version: "2.0.0"
    hash: "sha256:2c624232cdd221771294dfbb310acbc8754e197e333e9342f18b93fcb4e0de7c"
```

**Lock fields:**

| Field                   | Description                                            |
| ----------------------- | ------------------------------------------------------ |
| `spec.source`           | Path to the version file (relative to `versions_path`) |
| `spec.hash`             | sha256 of the version file — verified at deploy time   |
| `spec.version`          | Version identifier (from `meta.version` of the source) |
| `spec.previous.source`  | Previous version file — enables rollback               |
| `spec.previous.version` | Previous version identifier                            |
| `spec.previous.hash`    | Previous file hash — verified during rollback          |

The lock is PR-approved in git. It does NOT duplicate pins — it points to the version file.
Strata follows the reference at deploy time.

### Multi-workspace directory layout

Each promotion has its own `versions_path`. Version files and locks are co-located:

```
@config/versions/
  landscape/                     # promotion: landscape-infra
    v1.0.0.yaml
    v1.0.1.yaml
    dev.lock.yaml
    prod.lock.yaml
  customer/                      # promotion: customer-apps
    v2.0.0.yaml
    v2.1.0.yaml
    dev.lock.yaml
    prod.lock.yaml
    prod.wave.1.lock.yaml        # wave lock (during rollout only)
```

Each workspace layer promotes independently. Landscape may be on v1.0.1 while customer is
on v2.1.0. Different cadences, different blast radii, different version files.

### Rollback

```bash
strata promote rollback prod --promotion customer-apps
# → reads prod.lock.yaml → spec.previous
# → verifies previous hash
# → writes new prod.lock.yaml pointing to previous source
# → branch → commit → push
```

No git history traversal needed — the previous version is recorded in the lock itself.

---

## Layer 5 — Auto-Resolve at Deploy Time

When a deployment has `spec.promotion`, strata resolves the version automatically.

```bash
strata deploy run -f deploy/customer-acme.yaml
# No -v flag needed — version is resolved from promotion config
```

**Resolution steps:**

1. Read `spec.promotion.name` → look up promotion in config
2. Get `versions_path` from the promotion
3. Read `spec.promotion.ring` → derive lock path: `{versions_path}/{ring}.lock.yaml`
4. Read the ring lock → follow `spec.source` to the version file
5. Verify hash (lock hash matches version file)
6. Check for wave membership (see Waves below)
7. Apply pins to the deployment

**Resolution precedence (full chain):**

```
1. Stack defaults                             ← human-authored
2. Environment merge chain (spec.overrides.*) ← human-authored
3. Version file pins (via ring lock)          ← machine-resolved
4. Wave lock pins (if wave rollout active)    ← machine-resolved, wins
```

A deployment without `spec.promotion` skips layers 3–4 entirely — pre-promotion behaviour
is fully preserved.

### Build output provenance

`strata build` records which version was used in the build output:

```yaml
# In build output (platform.json or equivalent)
versions:
  source: "@config/versions/customer/v2.1.0.yaml"
  version: "2.1.0"
  hash: "sha256:9f86d081..."
  ring: prod
  wave: 1                        # if wave rollout active
```

Reference only — no duplicated pins. The hash proves integrity. The source path + git
commit enables full reproducibility.

---

## Waves

Waves enable gradual rollout within a ring. Wave 1 deploys first (canary), later waves
follow after validation. Waves only apply to rings that configure them.

### How waves work

1. Wave definitions live on the promotion config (not on deployments)
2. Each deployment's `meta.labels` determine its wave membership
3. During a rollout, wave lock files exist alongside the ring lock
4. At deploy time, strata resolves wave membership from labels

**Deployments are simple — no wave declaration needed:**

```yaml
# deploy/customer-acme.yaml
meta:
  labels:
    tier: enterprise         # → matched to wave 1 by promotion config
    customer: acme
spec:
  promotion:
    name: customer-apps
    ring: prod

# deploy/customer-contoso.yaml
meta:
  labels:
    tier: starter            # → not matched by any wave → catch-all
    customer: contoso
spec:
  promotion:
    name: customer-apps
    ring: prod
```

### Wave rollout flow

```bash
# 1. Promote wave 1
strata promote prod versions/customer/v2.1.0.yaml --promotion customer-apps --wave 1
# → writes @config/versions/customer/prod.wave.1.lock.yaml
# → branch → commit → push → PR

# 2. Deploy wave 1
strata deploy run --ring prod --wave 1 --promotion customer-apps
# → finds all prod deployments where meta.labels matches { tier: enterprise }
# → for each: resolves prod.lock.yaml + layers prod.wave.1.lock.yaml on top
# → deploys

# 3. Validate, then promote wave 2
strata promote prod versions/customer/v2.1.0.yaml --promotion customer-apps --wave 2
strata deploy run --ring prod --wave 2 --promotion customer-apps

# 4. Complete: advance the ring lock, delete wave locks
strata promote prod versions/customer/v2.1.0.yaml --promotion customer-apps --complete
# → advances prod.lock.yaml to v2.1.0
# → deletes prod.wave.*.lock.yaml
# → branch → commit → push → PR
```

### Wave resolution at deploy time

When deploying a single deployment that has wave membership:

1. Load ring lock from `{versions_path}/{ring}.lock.yaml`
2. Read wave config from the promotion (`rings[].waves`)
3. Match deployment's `meta.labels` against wave selectors → wave N
4. Look for `{versions_path}/{ring}.wave.{N}.lock.yaml`
5. If found → layer it on top (wave lock wins for overlapping pins)
6. If not found → ring lock applies as-is (no rollout in progress)

Deployments not matched by any wave selector are in the **catch-all** group — they receive
the ring lock only after `--complete`.

### Wave lock file

```yaml
# @config/versions/customer/prod.wave.1.lock.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prod.wave.1
spec:
  source: "v2.1.0.yaml"
  hash: "sha256:9f86d081..."
  version: "2.1.0"
  wave: 1
```

Same structure as a ring lock, with an additional `wave` field. Exists only during a
rollout; deleted by `--complete`.

---

## CLI Summary

### `strata versions`

| Command                         | What it does                                                   |
| ------------------------------- | -------------------------------------------------------------- |
| `strata versions add`           | Generate a new version file from workspace or previous version |
| `strata versions lock <file>`   | Compute hash and write it into the version file                |
| `strata versions export <file>` | Print resolved pin map (`--output json\|text`)                 |

### `strata promote`

All commands accept `--promotion <name>` (required when multiple promotions exist, optional when only one is configured).

| Command                                                      | What it does                                 |
| ------------------------------------------------------------ | -------------------------------------------- |
| `strata promote <ring> <file> --promotion <name>`            | Write ring lock pointing to the version file |
| `strata promote <ring> <file> --promotion <name> --wave N`   | Write wave lock for gradual rollout          |
| `strata promote <ring> <file> --promotion <name> --complete` | Advance ring lock, delete wave locks         |
| `strata promote <ring> <file> --promotion <name> --dry-run`  | Show what would be written, no side effects  |
| `strata promote rollback <ring> --promotion <name>`          | Revert ring lock to previous version         |
| `strata promote status --promotion <name>`                   | Show current version per ring                |
| `strata promote matrix`                                      | Show version matrix across all promotions    |

### `strata deploy` (version-related flags)

| Flag                 | Description                                                          |
| -------------------- | -------------------------------------------------------------------- |
| `-v <file>`          | Deploy with explicit version file (manual mode, no promotion needed) |
| `--ring <name>`      | Filter to deployments in this ring                                   |
| `--wave <id>`        | Filter to deployments in this wave                                   |
| `--promotion <name>` | Filter to deployments using this promotion                           |
| `--require-lock`     | Fail if version file is not locked (strict mode)                     |

---

## Deployment Modes — Summary

| Mode            | Deployment has                   | Version resolved from               | Managed by            |
| --------------- | -------------------------------- | ----------------------------------- | --------------------- |
| **No versions** | No `spec.promotion`, no `-v`     | Stack + environment chain only      | Human (overrides)     |
| **Manual**      | No `spec.promotion`              | `-v` flag on CLI                    | Human (explicit file) |
| **Promoted**    | `spec.promotion: { name, ring }` | Auto-resolved from promotion config | `strata promote`      |

A deployment is exactly one mode. If it has `spec.promotion`, it is promoted (Layer 5).
If not, it is manual (Layer 3) or unversioned.

---

## Audit Trail

| Question                            | Answer                                                       |
| ----------------------------------- | ------------------------------------------------------------ |
| What version ran in prod on date X? | Read `prod.lock.yaml` at that git commit                     |
| Who approved the version change?    | PR merge history on the lock file                            |
| Was the version file tampered?      | `spec.hash` inside the version file — verified at every step |
| Was the progression followed?       | Git timestamps on each ring's lock file                      |
| What changed between versions?      | Diff two version files directly                              |
| What was deployed?                  | Build output `versions:` section (source + hash)             |

---

## Multi-Workspace Topology

Different workspace layers (landscape, zone, customer) have different version files and
promote independently. Each promotion config has its own `versions_path`.

```yaml
# config.yaml
spec:
  promotions:
    - name: landscape-infra
      versions_path: "@config/versions/landscape/"
      rings:
        - { name: dev, order: 1 }
        - { name: prod, order: 2 }

    - name: customer-apps
      versions_path: "@config/versions/customer/"
      rings:
        - { name: dev, order: 1 }
        - { name: sandbox, order: 2 }
        - { name: prod, order: 3, waves: [...] }
```

**Deployment dependency order:**
1. **Landscape** deploys first (outputs feed zone)
2. **Zone** deploys second (outputs feed customer)
3. **Customer** deploys last — separate cadence, waves

Each layer promotes independently. Landscape may be on v1.0.1 while customer is on v2.1.0.
Different cadences, different blast radii, different version files.

---

## Implementation Status

| Phase | Description                                                                      | Status | Completed  |
| ----- | -------------------------------------------------------------------------------- | ------ | ---------- |
| 1     | Models: `VERSION_LOCK`, `VERSION` kinds; `deployment.spec.versions` field        | ✅ Done | 2026-07-11 |
| 2     | Resolution: `VersionService`, `_apply_version_pins` hook in `DeploymentService`  | ✅ Done | 2026-07-11 |
| 3     | Validation: `platform_validator.py`, `cli_schema.py`, shadowed-override warnings | ✅ Done | 2026-07-11 |
| 4     | CLI: `strata versions` group (`init`, `export`, `apply`, `refresh`)              | ✅ Done | 2026-07-11 |
| P-1   | Promotion model + validation (`promotion_model.py`, env ring ref check)          | ✅ Done | 2026-07-11 |
| P-2   | `strata promote` CLI group                                                       | ✅ Done | 2026-07-11 |
| P-3   | Promotion validation wiring                                                      | ✅ Done | 2026-07-11 |
| P-4   | Strict lock mode: `require_lock` on ring                                         | ✅ Done | 2026-07-11 |
| P-5a  | `type: tool` support                                                             | ✅ Done | 2026-07-11 |
| P-5b  | Shadowed-override warnings                                                       | ✅ Done | 2026-07-11 |
| F-2   | Artifact digest policy                                                           | ✅ Done | 2026-07-11 |
| R-1   | Revised design: layers, `-v` flag, `versions_path`, hash-in-file                 | ✅ Done | 2026-07-14 |
| R-2   | `strata versions add` (generate from workspace/previous)                         | ✅ Done | 2026-07-14 |
| R-3   | `strata versions lock` (hash-in-file)                                            | ✅ Done | 2026-07-14 |
| R-4   | `deploy -v` flag (manual mode)                                                   | ✅ Done | 2026-07-14 |
| R-5   | `spec.promotions[].versions_path` on configuration                               | ✅ Done | 2026-07-14 |
| R-6   | Auto-resolve version from promotion at deploy time (Layer 5)                     | ✅ Done | 2026-07-14 |
| R-7   | Wave lock files (`{ring}.wave.{N}.lock.yaml`); `--wave N`, `--complete`          | ✅ Done | 2026-07-14 |
| R-8   | `spec.hash`, `spec.version`, `spec.wave`, `spec.previous` on lock files          | ✅ Done | 2026-07-14 |
| R-9   | Hash verification in `VersionService.load()` when following pointer              | ✅ Done | 2026-07-14 |
| R-10  | `run_pointer_rollback()` — restore ring lock via `spec.previous`                 | ✅ Done | 2026-07-14 |
| R-11  | `deploy run --ring / --wave / --promotion` filter flags + wave lock layering     | ✅ Done | 2026-07-14 |

---

## Edge Cases

| Scenario                                                     | Behaviour                                                                                                                              |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| Deploy with `spec.promotion` but no lock file exists         | Exit 3: `"No lock file at '{versions_path}/{ring}.lock.yaml'. Run 'strata promote' first."`                                            |
| `strata deploy -v` with unlocked version file (non-strict)   | Warn: `"Version file has no spec.hash — integrity cannot be verified."` Deploy proceeds.                                               |
| `strata deploy -v --require-lock` with unlocked version file | Exit 3: `"Version file is not locked. Run 'strata versions lock' first."`                                                              |
| `strata promote` with file outside `versions_path`           | Exit 3: `"Version file must be inside versions_path '{path}' for promotion '{name}'."`                                                 |
| Two promotions with overlapping `versions_path`              | Caught by `strata validate`: `"Promotions '{a}' and '{b}' share versions_path '{path}'. Each promotion must have a unique directory."` |
| `strata promote --dry-run`                                   | Shows what would be written (lock content, git branch name) without creating files or git operations.                                  |

---

## Open Questions

- ~~**Progression enforcement strictness**~~ — **Decided:** hard exit-3 error with a clear
  message: `"Error: ring 'prod' (order 3) cannot be promoted before 'sandbox' (order 2)
  has received version 2.1.0. Promote sandbox first, or use --force to override."`
  No configuration needed. `--force` bypasses for emergencies.
- ~~**Wave lock scope**~~ — **Decided:** all wave locks reference the same version file
  (the one being promoted). The ring lock stays on the previous version until `--complete`.
  Waves control **timing**, not different versions. During rollout: `prod.lock → v1`,
  `prod.wave.1.lock → v2`, wave 2 has no lock yet so gets v1 from ring lock.
  After `--complete`: `prod.lock → v2`, wave locks deleted.
- ~~**Multi-label matching**~~ — **Decided:** exit-3 error, no guessing. If a deployment's
  labels match multiple wave selectors, `strata validate` and `strata deploy` fail with:
  `"Error: deployment 'customer-acme' matches wave 1 and wave 2 in promotion 'customer-apps'.
  Labels must match exactly one wave. Fix match_labels to be unambiguous."`
  Wave selectors must be mutually exclusive — the operator designs labels that don't overlap.
- ~~**`-v` with promoted deployments**~~ — **Decided:** mutually exclusive. If a deployment
  has `spec.promotion`, `-v` is refused: `"Error: deployment 'customer-acme' is managed by
  promotion 'customer-apps'. Use 'strata promote' to change its version, or remove
  spec.promotion to use manual mode."` For hotfixes: `strata promote` with `--force`
  bypasses progression order — that's the escape hatch, not `-v`.
