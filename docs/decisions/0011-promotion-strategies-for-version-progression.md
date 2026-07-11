# Promotion strategies for version progression across environments

- Status: Accepted (partially implemented — see Implementation Status)
- Date: 2026-06-23

## Context and Problem Statement

Strata manages deployments across multiple environments (dev, test, acceptance, production)
and multiple tenants. Version changes — whether Terraform landscape references or Helm
chart versions — must progress through environments in a controlled, auditable way.

Today, promotion is entirely manual: an operator edits a `spec.overrides.remotes[].reference`
or a module `chart_version` in an environment YAML file, commits, and deploys. There is no
guardrail preventing a direct jump to production, no canary mechanism, no rollback tracking,
and no visibility into what version is running where across the fleet.

Version truth is also **scattered**: base image/chart versions live in `stack/*.yaml`, git
refs default in `configuration.spec.remotes[]`, and deviations live in per-environment
`spec.overrides`. Answering "what version runs in prd?" requires resolving the full merge
chain, which is why a cheap version matrix is hard. This ADR resolves that by making each
ring own a single machine-managed **version-lock** (`versions/<ring>.yaml`) — the lock-file
pattern applied to environments. Promotion becomes advancing the lock, not surgically
editing scattered override blocks.

The platform needs a structured promotion system that:

- Defines allowed progressions through ordered **rings** (dev → test → qas → prd), where each ring
  can contain multiple environments (e.g., `prd` = prod-be, prod-us, prod-sg)
- Records the version set per ring in a single, directly-readable **version-lock** file
- Supports gradual rollout strategies (single tenant first, waves, all-at-once)
- Distinguishes between infrastructure changes (high blast radius) and application changes (lower blast radius)
- Integrates with the existing git-based workflow (branch, commit YAML edits, PR, merge, deploy)
- Provides visibility into current versions and in-flight promotions
- Supports unpromotion (controlled rollback) using the same strategy and safety checks

## Related Work

**[ADR 0017: Tag-based release workflow](0017-tag-based-release-workflow-option-c.md)** addresses the **release lifecycle**: how versions move from code (commits, tests) into tagged releases and release branches. This ADR (0011) addresses **promotion**: how tagged releases are deployed progressively across environments.

They are complementary:

- **ADR 0017** (Release Lifecycle): Commits → Tests pass → `tested` tag → Release branch → `vX.Y.Z` semantic tag
- **ADR 0017** (Promotion): Semantic tag → dev ring → test ring → qas ring → prd ring (with ring waves, deployment waves and validation)

Together they provide end-to-end visibility and control over version progression from code to production.

## Key Observations

### Promotion types and risk profiles

Four promotion types are supported. Each maps to a specific YAML field and carries a
different blast radius, which drives which strategy (and how many waves) is appropriate.

| Type              | YAML field promoted                                           | Example                                    | Blast radius                                                                               |
| ----------------- | ------------------------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------ |
| `remote`          | `spec.overrides.remotes[name].reference`                      | `iac-core v1→v2`, `tf-landscape v2.3→v2.4` | **Very high** — IaC code change; Terraform plan can add, remove, or modify cloud resources |
| `helm_chart`      | `spec.modules[name].chart_version`                            | `nginx-ingress 4.0→4.1`                    | **Medium** — chart update may add CRDs, change resource manifests, alter pod specs         |
| `image`           | `spec.services[name].image` or `spec.modules[name].image_tag` | `myapp:v1.2→v1.3`                          | **Low** — application code only; rolling update, no schema change                          |
| `tool` *(future)* | Provisioner version constraint                                | Terraform `1.8→1.9`, Helm CLI `3.14→3.15`  | **High** — execution engine change; plan output may differ even with identical config      |

**Key distinctions:**

- `remote` is **not Terraform-specific** — it covers any versioned git repository that
  strata references: Terraform module collections, Ansible role collections, shared config
  repos. The mechanism is always the same: change a git reference tag on
  `spec.overrides.remotes[name].reference`. `iac-core v1 → v2` is a `remote` promotion.

- `helm_chart` and `image` are **distinct types** even though both live inside a module.
  Promoting a chart version (chart structure changes) and promoting an image tag (app code
  changes) have different blast radii and benefit from different strategies and wave counts.

- `tool` is **deferred** — provisioner tool versions are typically pinned in the remote
  repo (`.terraform-version`, `required_version` in `versions.tf`, `requirements.yml`),
  meaning a tool upgrade is usually expressed as a `remote` promotion of the pinning repo.
  Native `tool` type support (directly targeting a provisioner version field) is a Phase 4
  addition once strata has a `spec.provisioners[].version` field.

| Dimension           | `remote`                                        | `helm_chart`                 | `image`                      |
| ------------------- | ----------------------------------------------- | ---------------------------- | ---------------------------- |
| Rollback cost       | Very high — may require Terraform state surgery | Medium — helm rollback       | Low — redeploy previous tag  |
| Validation required | `terraform plan` diff is essential              | Helm diff is nice-to-have    | Image scan; smoke test       |
| Canary feasibility  | Hard — infra is typically shared per zone       | Natural — per-tenant release | Natural — per-tenant release |
| Shared vs isolated  | Shared across all tenants in a zone             | Isolated — per-tenant ns     | Isolated — per-tenant ns     |

This means the promotion strategy must be type-aware — the same progression
may use different strategies depending on what's being promoted.

### A promotion advances a version-lock

Versions do not live scattered across base stack files and hand-written environment
overrides. Each ring owns a single, machine-managed **version-lock file**
(`versions/<ring>.yaml`, `kind: version-lock`) that pins every promotable target to an
exact version. This mirrors the lock-file pattern (`package-lock.json`, `Cargo.lock`,
`poetry.lock`, `.terraform.lock.hcl`): humans declare loose intent; a purpose-built lock
records the exact pins; **promotion is advancing the lock**.

The mechanism is always the same:

1. Determine the target ring's lock file and the pin(s) to set (or copy from the source ring's lock)
2. Create a branch, edit `versions/<ring>.yaml`, commit
3. CI validates (plan, lint, overlap check)
4. PR reviewed and merged
5. CI deploys

Strata's job is steps 1-2 and providing visibility. Git and CI handle steps 3-5.

**Why a dedicated file and not in-place edits of environment files:**

- **Single source of truth per ring** — `versions/prd.yaml` is the complete, directly
  readable answer to "what versions run in prd?" No merge-chain resolution required, which
  makes `strata promote matrix` trivial (read the lock files) instead of expensive.
- **Clean, auditable diffs** — a promotion diff contains version changes only, never mixed
  with unrelated config edits. This matters for the ISO 27001 / ISAE 3402 evidence trail.
- **Strata edits a machine-owned file** — it never rewrites hand-authored, multi-purpose
  environment YAML, so comments and formatting in those files are never at risk.
- **Trivial rollback** — `git revert` of the lock commit restores the exact prior version set.

**Two-layer version model** (permanent, not a migration state):

- `stack/*.yaml` — human-authored **defaults**. Written once when defining a module or remote.
  Apply to any ring that has no lock, and provide backwards compatibility for configs that
  don't use promotion. Never touched by `strata promote`.
- `versions/<ring>.yaml` — machine-generated **pins**. Written only by `strata promote start`.
  Never hand-edited. Wins over any default or inline override for the pinned target.

This is the same separation as `package.json` (human intent, loose defaults) and
`package-lock.json` (machine-exact pins, authoritative). Having a `chart_version` in a
stack file alongside a lock pin is not dead code — it remains the default for any ring
not yet under a lock and for simple one-off deployments that don't go through promotion.

A target pinned in a lock ignores any `spec.overrides` for the same target (lock wins).
A target absent from the lock resolves normally through the existing merge chain.

### Canary is a special case of waves

Waves operate at two independent levels:

**Deployment waves** — within a single environment, controls which tenants/deployments
receive the version first. Membership is declared on `kind: deployment`.

| Approach     | Waves | Wave 1 target                                                      |
| ------------ | ----- | ------------------------------------------------------------------ |
| All-at-once  | `1`   | Ring lock `versions/<ring>.yaml` (all deployments)                 |
| Canary-first | `2`   | Scoped lock overlay `versions/<ring>.<scope>.yaml`, then ring lock |
| Multi-wave   | `3`   | Scope overlay iteration 1, then 2, then ring lock                  |

The underlying mechanic is always: "which deployments get this version in this wave?"
Wave membership is determined per-deployment via `spec.promotion.wave` (explicit
`iteration` or `match_labels`). Deployments without wave config default to the last wave.
A canary wave writes a **scoped lock overlay** (`versions/<ring>.<scope>.yaml`) that pins
only the waved deployments; the final wave folds the pin into the ring lock and deletes
the overlay.

**Ring waves** — within a ring, controls which environments receive the version first.
Membership is declared on the ring's `environments[]` list via a numeric `wave:` field.
This is distinct from deployment waves: it sequences *environments* (e.g., prod-be before
prod-us) rather than *deployments* within one environment.

| Approach         | Environments in ring       | Ring waves              |
| ---------------- | -------------------------- | ----------------------- |
| All-at-once      | prod-be, prod-us, prod-sg  | 1 (all together)        |
| Region-by-region | prod-be → prod-us, prod-sg | 2 (be first, then rest) |

CLI distinction: ring waves use integers (`--wave 1`, `--wave 2`); deployment waves use
names (`--wave canary`, `--wave all`). The type of the `--wave` argument determines which
level is targeted.

## Considered Options

### Option A: Configuration-defined strategies, `strata promote` command

Promotion strategies are defined in `configuration.yaml`. Environments reference which
strategy applies to them. A new `strata promote` command group automates the YAML edits
and tracks promotion state.

**Configuration model:**

```yaml
# configuration.yaml
spec:
  promotions:
    progressions:
      - name: standard
        rings:
          - name: dev
            environments: [dev1, dev2, internal-dev]
            # first ring — no inbound requirement
          - name: test
            environments: [last-test, int-test]
            require: any_one      # at least one dev env must have the version
          - name: qas
            environments: [int-qas, customer-qas]
            require: any_one
          - name: prd
            environments:         # waved within the ring for regional rollout
              - { name: prod-be, wave: 1 }
              - { name: prod-us, wave: 2 }
              - { name: prod-sg, wave: 2 }
            require: any_one
      - name: hotfix
        rings:
          - name: dev
            environments: [dev1]
          - name: prd
            environments: [prod-be, prod-us, prod-sg]
            require: any_one

    strategies:
      - name: infra-cautious
        type: remote                          # promotes spec.overrides.remotes[].reference
        # covers: iac-core v1→v2, tf-landscape v2.3→v2.4, ansible-roles v3→v4
        progression: standard
        waves:
          - name: canary                      # first: deployments with iteration: 1
          - name: all                         # last: everyone else
        scope: tenant                         # only tenant-layer deployments are waved
        gates:
          require_progression_order: true     # previous ring quorum must be satisfied

      - name: app-wave
        type: module                          # promotes chart_version / image tags
        # NOTE: prefer type: helm_chart or type: image for new strategies (see Design section)
        progression: standard
        waves:
          - name: canary                      # first: explicit iteration: 1 deployments
          - name: early                       # middle: match_labels tier: standard
          - name: all                         # last: everyone else
        scope: tenant                         # only tenant-layer deployments are waved
        gates:
          require_progression_order: true     # previous ring quorum must be satisfied
```

**Environment references the strategy and declares its ring:**

```yaml
# environments/production.yaml
spec:
  promotion:
    strategy: infra-cautious
    ring: prd                 # which ring this environment belongs to
```

**Wave assignment on deployments:**

Wave assignment is decentralized — each deployment declares its own wave membership.
Deployments without a `wave` block default to the last wave (the "everyone else" catch-all).
Resolution order: `iteration` wins over `match_labels`.

```yaml
# deploy/acme-production.yaml — explicit wave assignment  (kind: deployment)
spec:
  promotion:
    wave:
      iteration: 1                            # acme is always the canary in production
```

```yaml
# deploy/contoso-production.yaml — label-based wave assignment  (kind: deployment)
# (matches against this deployment's resolved meta.labels)
spec:
  promotion:
    wave:
      match_labels: { tier: standard }        # standard-tier deployments go in wave 1
```

The `wave:scope` on the strategy determines which layer of deployments can be waved
at all. Zone-layer infrastructure (shared AKS, shared networking) is always all-at-once
regardless of wave config — only the layer matching `scope` participates in gradual
rollout.

**CLI commands:**

```bash
# Initiate a promotion to ring prd — ring wave 1 (prod-be only, wave: 1)
strata promote start --remote tf_landscape --version v2.4.0 --to prd --wave 1

# Advance to ring wave 2 (prod-us + prod-sg)
strata promote start --remote tf_landscape --version v2.4.0 --to prd --wave 2

# Within a single environment, target deployment wave by name
strata promote start --remote tf_landscape --version v2.4.0 --to prd --wave canary

# Check status of all promotions
strata promote status

# Roll back (reverse promotion, same strategy applies)
strata promote rollback --remote tf_landscape --to prd

# Show version matrix across all rings and environments
strata promote matrix

# Show historical promotions from artifact store
strata promote history --ring prd
strata promote history --remote tf_landscape --last 5

# Show activity log for a promotion (CI/CD diagnostic output)
strata promote log --remote tf_landscape --to prd
```

**Promotion state tracking:**

Promotion state is split into two concerns: **activity log** (diagnostic trace for DevOps)
and **completed record** (durable audit evidence stored alongside deployment manifests).

*Activity log* — lives in `.strata/promotions/` as a running diagnostic file. Not required
for the promotion to function — strata derives all state from environment files and git.
DevOps engineers can watch this file to follow what's happening without reading git logs.

```yaml
# .strata/promotions/tf_landscape-v2.4.0-prd.yaml
target: tf_landscape
version: v2.4.0
previous_version: v2.3.0
ring: prd
environments: [prod-be, prod-us, prod-sg]
strategy: infra-cautious
progression: standard
rings: [dev, test, qas, prd]
branch: promote/tf_landscape-v2.4.0-prd
events:
  - timestamp: 2026-06-23T10:00:00Z
    action: start
    ring_wave: 1
    environments: [prod-be]
    deployment_wave: canary
    initiated_by: brady
    deployments: [acme]
    files_modified:
      - environments/tenants/acme.yaml        # scope: tenant — env file edited for this wave
  - timestamp: 2026-06-23T10:01:12Z
    action: gate_passed
    gate: require_progression_order
    detail: "qas ring satisfied (any_one): int-qas has v2.4.0"
  - timestamp: 2026-06-23T10:01:15Z
    action: branch_created
    branch: promote/tf_landscape-v2.4.0-prd
    commit: abc123f
  - timestamp: 2026-06-24T14:30:00Z
    action: start
    ring_wave: 2
    environments: [prod-us, prod-sg]
    deployment_wave: all
    initiated_by: brady
    deployments: [all]
    files_modified:
      - environments/prod-us.yaml
      - environments/prod-sg.yaml
    fields_removed:
      - environments/tenants/acme.yaml → spec.overrides.remotes[tf_landscape]
  - timestamp: 2026-06-24T15:10:00Z
    action: completed
    outcome: completed
```

Activity log filename uses the ring name: `.strata/promotions/{target}-{version}-{ring}.yaml`

This file is append-only during the promotion. It is never deleted — `.strata/promotions/`
is gitignored, so logs accumulate locally without polluting the config repo. The information
is purely diagnostic — the promotion-record in the artifact store is the authoritative
audit trail.

*Completed record* — written to the configured artifact store (same remote as deployment
manifests) when the last wave is committed (or when rollback is committed). This is the
authoritative audit evidence. It captures what was promoted, how it was authorized, every
gate that was checked, every file that was edited, and every git commit made.

```yaml
# Stored in artifact remote, e.g. manifests/promotions/prom-20260623-prd-001.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: promotion-record
meta:
  name: prom-20260623-prd-001
  labels:
    target: tf_landscape
    ring: prd
    outcome: completed            # completed | partial | rolled-back
  annotations:
    description: "Promote tf_landscape v2.3.0 → v2.4.0 to prd ring"
spec:

  # ── What was promoted ──────────────────────────────────────────────────
  target:
    type: remote                  # remote | helm_chart | image
    name: tf_landscape
    from_version: v2.3.0          # version in the ring before this promotion
    to_version: v2.4.0            # version set by this promotion

  # ── How it was promoted ────────────────────────────────────────────────
  strategy: infra-cautious
  progression: standard
  rings: [dev, test, qas, prd]   # full ordered ring list from the progression

  # ── Outcome ────────────────────────────────────────────────────────────
  outcome: completed
  # completed   — all waves committed; branch exists; awaiting PR/deploy
  # partial     — some waves committed but promotion was aborted before the last wave
  # rolled-back — rollback edits committed; rollback_of points to the original record
  rollback_of: null               # name of the promotion-record this reverses (rollbacks only)

  # ── Identity & timing ──────────────────────────────────────────────────
  initiated_by: brady             # $USER or $CI_ACTOR who ran strata promote start
  hostname: workstation-01        # machine that ran strata
  started_at: 2026-06-23T10:00:00Z    # timestamp of first wave commit
  completed_at: 2026-06-24T15:10:00Z  # timestamp of last wave commit
  duration_seconds: 104400            # calendar time first→last commit (not CPU time)

  # ── Git ────────────────────────────────────────────────────────────────
  branch: promote/tf_landscape-v2.4.0-prd
  commits:
    - ring_wave: 1
      sha: abc123f
      message: "promote tf_landscape v2.4.0 → prd ring-wave 1 (prod-be) deploy-wave canary"
      committed_at: 2026-06-23T10:01:15Z
    - ring_wave: 2
      sha: def456a
      message: "promote tf_landscape v2.4.0 → prd ring-wave 2 (prod-us, prod-sg) deploy-wave all"
      committed_at: 2026-06-24T14:31:00Z

  # ── Gate results (compliance evidence) ────────────────────────────────
  gates:
    - gate: require_progression_order
      ring: prd
      require: any_one
      checked_at: 2026-06-23T10:01:12Z
      passed: true
      detail: "qas ring quorum satisfied (any_one): int-qas has v2.4.0"

  # ── Wave execution summary ─────────────────────────────────────────────
  ring_waves:
    - ring_wave: 1
      environments: [prod-be]
      deployment_wave: canary
      deployments: [acme]
      files_modified:
        - environments/tenants/acme.yaml
      committed_at: 2026-06-23T10:01:15Z

    - ring_wave: 2
      environments: [prod-us, prod-sg]
      deployment_wave: all
      deployments: all
      files_modified:
        - environments/prod-us.yaml
        - environments/prod-sg.yaml
      fields_removed:
        - "environments/tenants/acme.yaml → spec.overrides.remotes[tf_landscape]"
      committed_at: 2026-06-24T14:31:00Z

  # ── Links to deployment manifests (written later by strata deploy run) ─
  # Strata does not know when the PR merges or when CI deploys. These links
  # are populated by strata deploy run when it detects an active promotion
  # branch for this target+ring combination and writes its deployment manifest.
  deployment_manifests:
    - acme_eu_prod-be/manifest-20260623.json
    - contoso_eu_prod-be/manifest-20260624.json
```

**Promotion record field reference:**

| Field                  | Type           | Description                                                                               |
| ---------------------- | -------------- | ----------------------------------------------------------------------------------------- |
| `target.type`          | enum           | `remote` \| `helm_chart` \| `image`                                                       |
| `target.name`          | string         | Remote/module/service name                                                                |
| `target.from_version`  | string         | Version before promotion (read from env file at gate-check time)                          |
| `target.to_version`    | string         | Version set by this promotion                                                             |
| `strategy`             | string         | Strategy name from `configuration.spec.promotions.strategies[]`                           |
| `progression`          | string         | Progression name                                                                          |
| `rings`                | list           | Ordered ring names from the progression (for lineage)                                     |
| `outcome`              | enum           | `completed` \| `partial` \| `rolled-back`                                                 |
| `rollback_of`          | string \| null | Name of the original promotion-record (rollbacks only)                                    |
| `initiated_by`         | string         | `$USER` or CI actor identity                                                              |
| `hostname`             | string         | Machine that ran `strata promote`                                                         |
| `started_at`           | ISO-8601       | Timestamp of first wave commit                                                            |
| `completed_at`         | ISO-8601       | Timestamp of last wave commit                                                             |
| `duration_seconds`     | int            | Calendar time first→last commit                                                           |
| `branch`               | string         | Git branch name (`promote/{target}-{version}-{ring}`)                                     |
| `commits[]`            | list           | One entry per wave: `ring_wave`, `sha`, `message`, `committed_at`                         |
| `gates[]`              | list           | One entry per gate check: `gate`, `ring`, `require`, `checked_at`, `passed`, `detail`     |
| `ring_waves[]`         | list           | Summary per ring wave: environments, deployment wave, deployments, files modified/removed |
| `deployment_manifests` | list \| null   | Paths to deployment manifests written by subsequent `strata deploy run` invocations       |

**When is it written?**

The promotion record is written by `strata promote start` when the **last ring wave** is
committed. For rollbacks, written by `strata promote rollback` when the rollback commit is
made. Neither waits for the PR to merge or CI to deploy — those are git and CI concerns.

For partial promotions (aborted mid-way):
- `strata promote rollback` writes a new record with `outcome: rolled-back` pointing to the
  partial one via `rollback_of`
- The partial record itself is written with `outcome: partial` at rollback time, referencing
  only the waves that were actually committed

**Relationship to deployment manifests:**

The promotion record and the deployment manifest are complementary, not redundant:

|            | Promotion record                                                      | Deployment manifest                                                             |
| ---------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Written by | `strata promote start/rollback`                                       | `strata deploy run`                                                             |
| Captures   | Authorization chain: gates passed, strategy followed, YAML edits made | Execution evidence: stages ran, images deployed, Terraform state backend locked |
| Timing     | At commit time (before PR/deploy)                                     | At deploy completion (after PR merges, CI runs)                                 |
| Answers    | "Was the right process followed to change the version?"               | "Did the deployment succeed after the version was changed?"                     |

Together they form a complete audit chain: the promotion record proves the change was
authorized and followed the declared strategy; the deployment manifest proves the change
was actually applied.

**Why two artifacts?**

| Concern      | Activity log (`.strata/promotions/`)                          | Promotion record (artifact store)                  |
| ------------ | ------------------------------------------------------------- | -------------------------------------------------- |
| Purpose      | Diagnostic trace — watch what strata is doing, debug failures | Audit evidence — authorization chain, gate results |
| Required     | No — promotion works without it (all state derived from git)  | Yes — authoritative audit trail                    |
| Lifetime     | Local, gitignored, accumulates indefinitely                   | Permanent — never deleted                          |
| Content      | Timestamped event log: every action, gate, file, commit       | Structured summary: gates, waves, commits, outcome |
| Storage      | Local workspace (`.strata/`), gitignored                      | Same artifact remote as deployment manifests       |
| Queryable by | `strata promote status` (in-flight diagnostics)               | `strata promote history` (historical audit)        |



**Git flow for wave progression:**

Strata resolves all deployments in the target environment, evaluates each deployment's
`spec.promotion.wave` config (iteration, match_labels, or default=last), and groups
them into waves defined by the strategy.

Wave 1 (canary — deployments matching wave 1):
- Writes a scoped lock overlay pinning only the waved deployments:
  `versions/prd.acme.yaml` → `pins: [{ target: {type: remote, name: tf_landscape}, version: v2.4.0 }]`
- The resolver applies the overlay above the ring lock and merge chain — no hand-authored file touched

Wave N (final — all remaining):
- Sets the pin in the ring lock:
  `versions/prd.yaml` → `pins: [{ target: {type: remote, name: tf_landscape}, version: v2.4.0 }]`
- Deletes the scoped overlays from earlier waves (the ring lock now covers everyone)

**Unpromotion (rollback):**
- Same mechanism in reverse: `strata promote rollback` reads `previous_version` from state,
  applies it using the same strategy (canary-first if that's the policy)
- No shortcuts: if production required canary-first going forward, it requires canary-first
  going backward
- The completed record stores `outcome: rolled-back` for audit trail

### Option B: Environment-only, no command automation (not chosen)

Define progressions and strategies in configuration, but don't automate the edits.
Strata only provides `strata promote status` (read-only visibility) and validates that
promotions follow the declared progression (e.g., reject a direct dev→prd jump during `strata validate`).

The operator manually edits environment YAML files and creates branches/PRs.

- Good: Simpler implementation. No state tracking file. No git automation.
- Good: Teams keep full control over how they manage branches and PRs.
- Bad: Manual process is error-prone — the operator must know which file to edit for canary vs full rollout.
- Bad: No tracking of in-flight promotions — "is production on wave 1 or wave 2?" requires reading git history.
- Bad: Unpromotion requires the operator to look up the previous version manually.

### Option C: External promotion tool (no strata involvement, not chosen)

Promotions are handled by CI/CD pipeline logic or a separate tool (e.g., ArgoCD progressive
delivery, Flux + Flagger). Strata just deploys whatever version is in the YAML files.

- Good: No new strata features needed.
- Good: Leverages mature external tools for canary/progressive delivery.
- Bad: Only works for Helm/Kubernetes applications — doesn't cover Terraform infrastructure promotions.
- Bad: Breaks the single-source-of-truth principle — promotion state lives outside the config repo.
- Bad: No unified visibility — Terraform version matrix requires a separate mechanism from Helm version matrix.

## Decision Outcome

**Decision: Option A** — Configuration-defined strategies with `strata promote` automation.

The promotion system should be a first-class strata concept because:

1. **Both Terraform and Helm need it.** External tools like Flagger only cover Kubernetes.
   Infrastructure version progression is the harder problem and has no off-the-shelf solution.

2. **A promotion advances a version-lock.** Each ring owns `versions/<ring>.yaml` — a
   machine-managed lock that pins every promotable target. Strata edits that dedicated file
   (never hand-authored config), so diffs are version-only and auditable, and "what version
   is where" is a direct read. Locks are authoritative when present but optional, so adoption
   is gradual and inline overrides keep working for un-pinned targets.

3. **State tracking enables visibility.** With per-ring locks, "what version is running where"
   requires cross-referencing environment files, deployment manifests, and git history.
   A small state file makes this queryable.

4. **Unpromotion is critical for safety.** Rolling back under pressure is when mistakes happen.
   Automating the reverse edit with the same safety checks prevents shortcuts.

### Implementation approach

**Phase 1 — Strategy model + validation:**
- `configuration.spec.promotions` model (progressions with rings, strategies, named deployment waves)
- `progression.rings[]` with `name`, `environments`, `require` (quorum gate)
- `progression.rings[].environments[]` supporting both bare strings and `{ name, wave }` objects for intra-ring ordering
- `environment.spec.promotion` — `strategy` reference and `ring` membership declaration
- `deployment.spec.promotion.wave` model (`iteration`, `match_labels`) — opt-in deployment wave assignment
- `kind: version-lock` model (`versions/<ring>.yaml`) — per-ring version pins; resolver applies it as the top layer for pinned targets (authoritative when present, optional otherwise)
- `strata validate` checks: warn if a version jump skips a ring in the progression
- Still no automation — strategies are advisory guardrails

**Phase 2 — Promotion automation:**
- `strata promote start` — creates branch, makes YAML edits, commits (ring wave via `--wave <int>`, deployment wave via `--wave <name>`)
- `strata promote rollback` — reverse promotion using the same strategy
- `strata promote status` — shows in-flight promotions by reading `.strata/promotions/` activity log
- `.strata/promotions/` activity log (diagnostic trace, gitignored, kept locally)
- `kind: promotion-record` written to artifact store on completion/rollback
- `strata promote history` — query completed records from artifact store
- Gate: `require_progression_order` with `ring.require` quorum — refuse if previous ring quorum is not satisfied
  (pure YAML inspection, no external tool integration needed)
- Future gates added incrementally: `require_plan_clean`, `require_healthy`, `require_no_drift`

**Phase 2 — `strata promote matrix` (no longer deferred):**
With per-ring version-locks, `promote matrix` reads `versions/<ring>.yaml` (plus any scoped
overlays) directly — no fleet-wide `EnvironmentService` traversal, no resolved-model cache
required. The lock files *are* the version index. Targets not yet under a lock are shown by
falling back to the merge chain for that single target only.

> **Historical note:** Before the version-lock mechanism, `promote matrix` was deferred
> because reading effective versions required loading the full merged environment model for
> every registered deployment (see OQ-17). The lock file removes that cost. A general
> resolved-model cache may still benefit other fleet-wide operations (bulk validation, drift
> detection); that design belongs in a separate ADR.

### Consequences

- Good: Unified promotion model for both infrastructure and application changes.
- Good: Strategies are configuration-as-code — auditable, versioned, team-shared.
- Good: Phased implementation means model + validation ships before automation; `promote matrix` reads lock files directly (no caching dependency).
- Good: Git remains the source of truth — strata automates the edits but the PR/merge flow is unchanged.
- Good: Unpromotion uses the same strategy, preventing unsafe shortcuts under pressure.
- Good: Completed promotion records stored in the same artifact remote as deployment manifests — no new infrastructure for audit storage. Reuses existing `spec.remotes` configuration.
- Good: `kind: promotion-record` follows the Kubernetes-style schema, consistent with all other strata documents.
- Good: No required runtime state — all promotion state derived from version-lock files and git. Activity log is diagnostic-only.
- Good: Terraform landscapes benefit equally — `iac-core v1 → v2` and any other versioned remote repo progress through rings via the same `type: remote` strategy and `require_progression_order` gate. Zone-layer infra uses a single `all` wave (all-at-once per env, still gated). Tenant-layer infra can canary via `scope: tenant`.
- Good: Helm chart versions (`type: helm_chart`) and image tags (`type: image`) are distinct strategy types with appropriate blast-radius defaults, rather than being conflated under a generic `module` bucket.
- Neutral: Zone-layer infrastructure is structurally shared per environment — canary within a single env is not possible. This is inherent to shared infra, not a system limitation. The `scope` field makes this explicit.
- Neutral: Multi-promotion of the same target to the same environment (e.g., v2.4.0 and v2.5.0 both in flight) produces a git merge conflict on the second PR. This is correct behavior — git is the conflict detector. `strata promote start` can warn if a competing branch exists, but enforcement is via the normal PR process.
- Neutral: Rollback after a partially-merged promotion (wave 1 PR merged, wave 2 PR still open) requires two actions: discard the open wave 2 branch, then create a new rollback branch that reverses the already-merged wave 1 change. `strata promote rollback` detects this via the activity log and generates the correct reverse edit, but the operator must close the open wave 2 PR manually. No silent partial states.

## Open Questions

1. ~~**Observation period enforcement:**~~ Resolved — not applicable. `strata promote continue`
   no longer exists (each wave is explicit `start --wave N`). Strata is a config tool, not a
   runtime system — it doesn't track deployment timestamps. The operator decides when enough
   time has passed. The explicit wave command and PR review process ARE the observation gate.

2. ~~**Wave tenant selection:**~~ Resolved — no auto-selection. Wave membership is always
   a deliberate choice: pilots declare `iteration: 1`, middle waves use explicit `iteration`
   or `match_labels`, everyone else defaults to last wave. The wave array on the strategy
   declares how many waves exist, not a selection algorithm. No randomness needed.

3. ~~**Promotion scope per layer:**~~ Resolved — the `scope` field on the strategy handles
   this. Zone-layer uses `waves: [100]` (all-at-once). Tenant-layer uses `scope: tenant`
   for canary. No automatic adaptation needed — the operator picks the strategy per env.

4. ~~**Multi-promotion conflicts:**~~ Resolved — independent targets edit different files,
   so no conflict. Same target to the same environment produces a git merge conflict on
   the second PR — correct behavior, already covered in Consequences (Neutral).

5. ~~**Promotion and `strata deploy list`:**~~ Resolved — `strata deploy list` already
   resolves the effective configuration per deployment through the merge chain. Promotion
   overrides are standard YAML files (per-deployment or shared), so deploy naturally sees
   them. No special integration needed — they're the same system.

6. ~~**Should `strata promote` be part of `strata env`?**~~ Resolved — `strata promote` is
   its own command group. It has `start`, `rollback`, `status`, `matrix`, `history`, `log` —
   too many subcommands to nest under `env`.

## Issues to Resolve (Phase 3 Blockers)

7. ~~**Promotion override file not in merge chain:**~~ Resolved via version-locks. A
   promotion writes a scoped overlay `versions/<ring>.<scope>.yaml` (canary wave), then
   sets the pin in the ring lock `versions/<ring>.yaml` and deletes the overlay (final
   wave). Lock files slot into the resolver as the top layer for pinned targets; no
   hand-authored environment file is edited, and no auto-include/glob is needed.

8. ~~**`scope: tenant` has no mechanical definition:**~~ Resolved — `scope` references a
   layer name from `configuration.spec.layering[]`. The predicate is:
   `layer_name in deployment.spec.layers` — if the deployment has that layer key, it
   participates in wave logic. Zone-level deployments lack the `tenant` layer key →
   excluded from waving → always all-at-once. Generic: works for any layer name.

9. ~~**"Percentage waves" table in Key Observations is stale:**~~ Fixed — renamed to
   "Multi-wave" with wave count instead of percentages. Membership is always deliberate.

10. ~~**Deployment properties beyond layering:**~~ Resolved — informational properties
    (`costcenter`, `region`, `project`, etc.) belong in `spec.properties` (already a
    `Dict[str, Any]` on both deployment and environment models) or `meta.labels` for
    filtering/matching. `match_labels` in wave assignment matches against `meta.labels`.
    `spec.tenant` is structural (drives file resolution) and is not used by `match_labels`
    directly. See Appendix.

11. ~~**Phase 1 reads a field that doesn't exist:**~~ Resolved — there is no `spec.version`
    field on `kind: environment`. `status` and `matrix` read `versions/<ring>.yaml` lock
    files (plus any scoped overlays) directly — a lightweight YAML parse of the lock index,
    not a full per-environment `EnvironmentService` resolution. Un-pinned targets fall back
    to the merge chain for that single target only.

12. ~~**No deployment discovery mechanism for `promote start`:**~~ Resolved — deployment
    files are registered in `solution.json` via `strata sln deployment add <path>` or
    discovered in bulk via `strata sln deployment scan [directory]`. The solution registry
    (`spec.deployments[]`) is the source of truth for enumeration. `promote start --to
    production` loads all registered deployments and filters by `spec.environments`
    containing the target environment name. This also enables the VS Code extension to
    display all managed deployments.

    **CLI:**
    ```bash
    strata sln deployment add deploy/myapp.yaml     # register one file
    strata sln deployment scan deploy/              # scan + register all in directory
    strata sln deployment list                      # show registered deployments
    strata sln deployment remove myapp             # unregister by name
    ```

13. ~~**Wave-to-file mapping is ambiguous:**~~ Resolved — the `spec.environments` list on
    `kind: deployment` is extended from `List[str]` to `List[DeploymentEnvironmentRef]`,
    where each entry has a `file` path and an optional `scope` annotation. Bare strings
    are auto-coerced at parse time for full backward compatibility.

    The promotion controller identifies whether a wave writes the ring lock or a scoped
    overlay by matching `entry.scope` against the strategy's `scope` field:
    - `scope: "tenant"` entry → canary/early waves write `versions/<ring>.tenant.<selector>.yaml`
    - `scope: "shared"` entry → final wave sets the pin in `versions/<ring>.yaml`
    - `scope: null` entries → not targeted for wave-specific edits (all-at-once ring lock)

    Deployments that don't annotate `scope` on any entry behave as today (all-at-once
    only). The field is optional — existing YAML without `scope` continues to work.

    ```yaml
    # deployment with scoped environment refs
    spec:
      environments:
        - file: environments/production.yaml
          scope: shared
        - file: environments/tenants/acme.yaml
          scope: tenant

    # backward-compatible shorthand (scope: null)
    spec:
      environments:
        - environments/production.yaml
    ```

14. ~~**Rollback depends on a local-only file:**~~ Resolved — `previous_version` is derived
    via a three-tier fallback that requires no special infrastructure:

    **Tier 1 — Activity log** (`.strata/promotions/{target}-{version}-{env}.yaml`): if
    present locally, read `previous_version` directly. Fast path, always preferred.

    **Tier 2 — Git history** (robust, always available): the pre-promotion value is always
    readable at the merge base between the promotion branch and `main`:
    ```bash
    git merge-base HEAD main          # → <base-commit>
    git show <base-commit>:<env-file> # → YAML before promotion started
    ```
    Parse `spec.overrides.remotes[{name}].reference` from that YAML — that is the
    previous version. This works on any machine, in CI, after workspace reset.
    The branch name (`promote/{target}-{version}-{environment}`) encodes enough context
    to locate the correct files without the activity log.

    **Tier 3 — Explicit flag** (escape hatch): `--from-version v2.3.0` — required only
    when Tier 1 and Tier 2 both fail (shallow clone, detached HEAD, etc.).

    This is consistent with the ADR's stated principle: "all promotion state can be derived
    from environment files and git history." The activity log is a local convenience, not
    a dependency.

15. ~~**`scope` + single-layer configurations:**~~ Resolved — when a strategy has
    `scope: tenant` but no registered deployment has the `tenant` layer key,
    `promote start` degrades to all-at-once mode automatically: it edits the
    `scope: "shared"` environment file directly (or the only environment file if none
    are annotated), logs a notice `"No scoped deployments found — falling back to
    all-at-once"`, and proceeds. No error, no silent no-op — the promotion still
    happens, just without canary waving.

    Single-layer operators who never annotate `scope` on their environment refs get
    the same behavior as today: all environment changes go to the shared file in a
    single wave. Waving is opt-in via `spec.environments[].scope` annotations.

16. ~~**Wave config placement: `kind: deployment` vs `kind: tenant`:**~~ Resolved —
    `spec.promotion.wave` belongs on `kind: deployment`, not `kind: tenant`.
    A deployment file already represents "this entity deployed to this environment"
    (e.g., `deploy/acme-production.yaml`). Wave assignment is per deployment-environment
    pairing: acme can be canary in production but all-at-once in acceptance (if that
    environment's strategy uses a single wave). Placing wave config on the tenant file
    would couple promotion strategy into entity identity and prevent per-environment
    wave variation. The deployment file is the correct owner because it is already
    environment-specific and is the unit enumerated by `solution.json`.

17. ~~**`promote matrix` implementation cost — deferred:**~~ Resolved — `promote matrix`
    requires a full `EnvironmentService` load per registered deployment to read effective
    versions. Without a resolved-model cache this is expensive for large fleets. The
    command is deferred until a `.strata/` caching ADR defines the cache format and
    invalidation strategy. `promote matrix` will read from that cache. The caching
    mechanism will also benefit bulk validation, drift detection, and other fleet-wide
    commands. Design tracked in **[ADR-0026](0026-resolved-model-cache.md)**.

## Appendix: How `spec.tenant` Works on Deployments

`deployment.spec.tenant` is an **Optional[PlatformName]** field that drives file-based
resolution and zone validation — it is not merely metadata.

**When defined** (`spec.tenant: acme`):

1. **File resolution:** The system loads `tenants/acme.yaml` from the repository.
   Validation fails if the file doesn't exist.
2. **Zone alignment:** Tenant zones (from the tenant file) are cross-checked against
   `configuration.spec.zones` and against provider zones. A provider cannot deploy to a
   zone the tenant isn't allowed in.
3. **Platform artifact:** The tenant model is embedded in the build artifact
   (`PlatformTenantModel`) and available to builders (Terraform, Ansible).

**When omitted** (`spec.tenant: null` or absent):

- No tenant file is loaded. No zone-alignment checks run.
- The deployment is treated as a **shared/platform deployment** serving all tenants.
- Builders receive `tenant = None` — templates can branch on this.

**Relationship to layers:** `spec.tenant` is a *structural* field (resolves files, drives
validation). It happens to correspond to the `tenant` layer key in `spec.layers`, but
serves a different purpose. The layer key controls artifact path structure; the `spec.tenant`
field controls tenant-scoped validation and embedding.

**Relationship to `match_labels`:** Wave assignment via `match_labels` matches against
`meta.labels` on any artifact (deployment, environment, tenant file). It does **not** read
`spec.tenant` directly — if you want to wave by tenant, label the deployment
(`meta.labels.tenant: acme`) or use `spec.layers` as the scope predicate.

**Informational properties** (`costcenter`, `region`, `project`, etc.) that don't drive file
resolution belong in `spec.properties` (both deployment and environment models already have
this as `Dict[str, Any]`) or `meta.labels` for filtering/matching purposes.

---

## Design: Promotion System

This section consolidates the full design for the promotion system as a reference for
implementation. It supersedes the exploratory examples in Option A above.

### Concepts

| Concept              | Definition                                                                                                                                                                                                                                                                                   |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Ring**             | A named group of environments at the same delivery tier (e.g., `prd` groups `prod-be`, `prod-us`, `prod-sg`). Progressions advance ring-by-ring.                                                                                                                                             |
| **Progression**      | An ordered list of rings that a version must traverse (e.g., `dev → test → qas → prd`). Each ring can contain multiple environments.                                                                                                                                                         |
| **Strategy**         | A named policy that governs HOW a version moves into a specific ring (wave count, scope, gates)                                                                                                                                                                                              |
| **Wave**             | An ordering unit. *Deployment wave* (named): a subset of deployments within one environment that receive the version together. *Ring wave* (integer): a subset of environments within a ring that receive the version together.                                                              |
| **Scope**            | A layer name from `configuration.spec.layering[]` that determines which deployments participate in deployment waving                                                                                                                                                                         |
| **Gate**             | A precondition that must pass before promotion proceeds (e.g., quorum of previous ring must have the version)                                                                                                                                                                                |
| **Promotion target** | The thing being versioned. One of: `remote` (git ref on a versioned repo — IaC modules, Ansible roles), `helm_chart` (Helm chart version), `image` (container image tag), `tool` (provisioner version — future).                                                                             |
| **Version-lock**     | A machine-managed file (`versions/<ring>.yaml`, `kind: version-lock`) that pins every promotable target to an exact version for one ring. Authoritative when present, optional otherwise. A promotion advances the lock. A canary wave uses a scoped overlay `versions/<ring>.<scope>.yaml`. |

### Configuration Model

#### `configuration.spec.promotions`

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
spec:
  promotions:
    progressions:
      - name: standard
        rings:
          - name: dev
            environments: [dev1, dev2, internal-dev]
            # first ring — no inbound requirement
          - name: test
            environments: [last-test, int-test]
            require: any_one      # at least one dev env must have the version
          - name: qas
            environments: [int-qas, customer-qas]
            require: any_one
          - name: prd
            environments:         # waved within the ring for regional rollout
              - { name: prod-be, wave: 1 }
              - { name: prod-us, wave: 2 }
              - { name: prod-sg, wave: 2 }
            require: any_one
      - name: hotfix
        rings:
          - name: dev
            environments: [dev1]
          - name: prd
            environments: [prod-be, prod-us, prod-sg]
            require: any_one

    strategies:
      - name: infra-cautious
        type: remote
        progression: standard
        waves:
          - name: canary
          - name: all
        scope: tenant
        gates:
          require_progression_order: true

      - name: chart-gradual
        type: helm_chart                      # promotes spec.modules[name].chart_version
        progression: standard
        waves:
          - name: canary
          - name: early-adopters
          - name: all
        scope: tenant
        gates:
          require_progression_order: true

      - name: image-wave
        type: image                           # promotes spec.services[name].image / image_tag
        progression: standard
        waves:
          - name: canary
          - name: early-adopters
          - name: all
        scope: tenant
        gates:
          require_progression_order: true

      - name: infra-zone
        type: remote
        progression: standard
        waves:
          - name: all
        scope: null                           # no waving — all-at-once for zone infra
        gates:
          require_progression_order: true
```

**Progression fields:**

| Field   | Type       | Required | Description                                       |
| ------- | ---------- | -------- | ------------------------------------------------- |
| `name`  | string     | yes      | Unique identifier for the progression             |
| `rings` | list[Ring] | yes      | Ordered list of rings. Position = promotion order |

**Ring fields:**

| Field          | Type                    | Required | Description                                                                                                                                         |
| -------------- | ----------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`         | string                  | yes      | Ring identifier (e.g. `dev`, `test`, `qas`, `prd`)                                                                                                  |
| `environments` | list[string \| RingEnv] | yes      | Environment names in this ring. Bare strings = `{ name, wave: null }` (all together)                                                                |
| `require`      | enum \| null            | no       | Inbound gate quorum: `any_one` (default), `all`, `null` (no gate — first ring). Evaluated only when strategy gate `require_progression_order: true` |

**RingEnv fields (when environments need intra-ring wave ordering):**

| Field         | Type           | Required | Description                                                                                                                                  |
| ------------- | -------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`        | string         | yes      | Environment name                                                                                                                             |
| `wave`        | integer        | no       | Ring wave number (1, 2, 3…). Environments with the same wave number execute together. Omit for all-at-once.                                  |
| ------------- | -------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`        | string         | yes      | Unique identifier for the strategy                                                                                                           |
| `type`        | enum           | yes      | See promotion types table below. `remote` \| `helm_chart` \| `image` \| `module` (deprecated alias for `helm_chart`)                         |
| `progression` | string         | yes      | References a named progression                                                                                                               |
| `waves`       | list[Wave]     | yes      | Ordered list of wave definitions. Position = execution order                                                                                 |
| `scope`       | string \| null | no       | Layer name from `configuration.spec.layering[]`. Only deployments with this layer key participate in waving. `null` = all-at-once, no waving |
| `gates`       | dict           | no       | Named gate conditions (see Gates section)                                                                                                    |

**Promotion types:**

| Type         | Field promoted                                                | Notes                                                                    |
| ------------ | ------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `remote`     | `spec.overrides.remotes[name].reference`                      | Any versioned git remote: Terraform modules, Ansible roles, IaC packages |
| `helm_chart` | `spec.modules[name].chart_version`                            | Helm chart version from registry                                         |
| `image`      | `spec.services[name].image` or `spec.modules[name].image_tag` | OCI image tag                                                            |
| `module`     | Same as `helm_chart`                                          | Deprecated alias — use `helm_chart` or `image`                           |
| `tool`       | `spec.provisioners[name].version`                             | Workspace provisioner tool version                                       |

**Wave definition:**

| Field  | Type   | Required | Description                                    |
| ------ | ------ | -------- | ---------------------------------------------- |
| `name` | string | yes      | Wave identifier (used in CLI: `--wave canary`) |

Waves are implicitly ordered by position in the list. The last wave is always the
"everyone else" catch-all — deployments without explicit wave assignment land here.

#### `environment.spec.promotion`

```yaml
# environments/prod-be.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: prod-be
spec:
  promotion:
    strategy: infra-cautious
    ring: prd                # which ring this environment belongs to
```

```yaml
# environments/dev1.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: dev1
spec:
  promotion:
    strategy: infra-cautious
    ring: dev
```

Each environment references which strategy applies and declares its ring membership.
The `ring` field is used by `promote start` to:
1. Enumerate which environments belong to the target ring
2. Evaluate the inbound gate (quorum of the previous ring)

Environments without a `promotion` block have no promotion guardrails (useful for
dev/sandbox environments where versions can be set freely).

#### `deployment.spec.environments` — scoped refs

The `spec.environments` field on `kind: deployment` accepts either bare strings (backward
compatible) or scoped ref objects. The `scope` annotation tells the promotion controller
which file to target for each wave:

```yaml
# deploy/acme-production.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: acme-production
spec:
  environments:
    - file: "@config/environments/production.yaml"
      scope: shared          # edited by the final wave (all)
    - file: "@config/environments/tenants/acme.yaml"
      scope: tenant          # edited by canary/early waves
```

```yaml
# deploy/zone-production.yaml — zone infra, no waving
spec:
  environments:
    - file: "@config/environments/production.yaml"
      scope: shared
```

Bare strings (`- environments/production.yaml`) are auto-coerced to `{file: ..., scope: null}`
at parse time. Deployments with no `scope` annotations are treated as all-at-once only.

**Scope naming contract:** `spec.environments[].scope` values and `strategy.scope` use the
same names by convention — a strategy with `scope: "tenant"` targets env entries annotated
`scope: "tenant"`. These are complementary, not identical in semantics: the strategy field
is a layer predicate (which deployments participate in waving); the entry annotation is a
role label (which file gets edited for which wave). An implementer should not assume
`strategy.scope == entry.scope` is a direct equality check — it is a naming convention.

#### `deployment.spec.versions` — explicit version file references

Version files are referenced explicitly in the deployment — parallel to `spec.environments`. No
auto-discovery. No implicit loading. Consistent with strata's principle that everything is declared.

```yaml
# deploy/prd.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: acme-production
spec:
  environments:
    - file: "@config/environments/production.yaml"
      scope: shared
    - file: "@config/environments/tenants/acme.yaml"
      scope: tenant
  versions:
    - "@config/versions/prd.manifest.yaml"   # human/tool-edited intent — lower precedence
    - "@config/versions/prd.yaml"            # strata-generated lock — wins
```

**Resolution rules:**
- Files are applied in **list order** — later entries win over earlier entries.
- The convention is manifest first, lock second. The lock always overrides the manifest for any
  pin it declares. Pins absent from the lock still resolve from the manifest.
- Either file is optional. A deployment with no `versions:` field resolves versions from the
  stack modules and environment overrides only — pre-promotion behaviour, unchanged.
- Bare strings and `@remote/` cross-repo references are both valid, identical to `environments`.

**Separation of concerns — why two lists:**

| List            | Changed by                             | Contains          |
| --------------- | -------------------------------------- | ----------------- |
| `environments:` | Operators (endpoints, secrets, sizing) | Runtime config    |
| `versions:`     | Operators, CI, renovate-style tools    | Software versions |

Keeping them in separate lists means a tool that updates versions never touches environment
files, and a human editing environment config never touches version files. Different change
rates, different owners, different tooling.

Deployment files must be **registered in the solution** to be visible to `promote start`:

```bash
strata sln deployment add deploy/acme-production.yaml
strata sln deployment scan deploy/         # register all in directory
strata sln deployment list                 # verify
```

#### Wave assignment on deployments

Wave membership is declared on the deployment — decentralized, not centrally managed
in the strategy.

```yaml
# deploy/acme-production.yaml  (kind: deployment — specific to production)
spec:
  promotion:
    wave:
      iteration: 1                # canary in production; may differ per environment
```

```yaml
# deploy/contoso-production.yaml  (kind: deployment)
spec:
  promotion:
    wave:
      match_labels:
        tier: enterprise          # matched against meta.labels on the deployment
```

```yaml
# deploy/fabrikam-production.yaml  (kind: deployment)
# No spec.promotion.wave — defaults to last wave ("all")
```

**Resolution order:**

1. `iteration` (explicit wave position, 1-indexed) — wins if present
2. `match_labels` (matches against `meta.labels` of the deployment) — evaluated if no iteration
3. Default = last wave — deployments without wave config are in the final catch-all

**Scope filtering:** Before wave assignment is evaluated, the strategy's `scope` field
filters deployments. Only deployments where `scope_layer_name in deployment.spec.layers`
participate. Zone-level deployments (no tenant layer) are excluded from waving entirely —
they follow the `scope: null` / all-at-once path.

#### The `version-lock` kind

Each ring owns one lock file. **Generated exclusively by `strata promote start` — never
hand-edited.** Humans review the diff in a PR before merging, but the file content is
always strata-owned. Strata advances it ring-by-ring as versions progress through the
progression.

```yaml
# versions/prd.yaml — one file per ring, machine-managed
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prd                       # ring name; matches a ring in the progression
spec:
  ring: prd
  pins:
    - target: { type: remote,     name: iac_core }   # git ref on a versioned repo
      version: v2.4.0
    - target: { type: helm_chart, name: traefik }    # spec.modules[name].chart_version
      version: "28.1.0"
    - target: { type: image,      name: app }        # spec.services[name].image tag
      version: v1.3.0
```

```yaml
# versions/prd.acme.yaml — scoped overlay for a canary wave (tenant "acme")
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prd.acme
spec:
  ring: prd
  scope: tenant
  scope_selector: acme
  pins:
    - target: { type: image, name: app }
      version: v1.3.0             # only acme gets v1.3.0 during the canary wave
```

**Pin fields:**

| Field         | Type   | Required | Description                                        |
| ------------- | ------ | -------- | -------------------------------------------------- |
| `target.type` | enum   | yes      | `remote` \| `helm_chart` \| `image` \| `tool`      |
| `target.name` | string | yes      | Name of the remote / chart module / service to pin |
| `version`     | string | yes      | Exact tag, chart version, or image tag             |

**Lock spec fields:**

| Field            | Type      | Required | Description                                     |
| ---------------- | --------- | -------- | ----------------------------------------------- |
| `ring`           | string    | yes      | Ring this lock governs                          |
| `scope`          | string    | no       | Layer name when this is a scoped canary overlay |
| `scope_selector` | string    | no       | Which deployment(s) the overlay applies to      |
| `pins`           | list[Pin] | yes      | The pinned targets                              |

**Resolution precedence** (versions slot into the existing merge chain above environment overrides):

```
1. Base stack files (chart_version, image tags, remote reference: main)   ← human-authored defaults
2. Environment merge chain (spec.overrides.*)                              ← human-authored config
3. Version manifest (deployment.spec.versions[0])                          ← human/tool-edited intent
4. Ring version-lock (deployment.spec.versions[1] / versions/<ring>.yaml)  ← machine-generated, wins
5. Scoped lock overlay (versions/<ring>.<scope>.yaml) during a canary wave ← machine-generated, wins
```

Layers 3 and 4 correspond to the entries in `spec.versions[]`, applied in list order.
A deployment with no `spec.versions:` skips layers 3 and 4 entirely — pre-promotion
behaviour is fully preserved.

- A target present in a lock **ignores** any default or override for the same target.
- A target **absent** from the lock resolves normally through the merge chain.
  Stack defaults and hand-written overrides remain valid for un-promoted targets.
- `strata validate` warns when a hand-written `spec.overrides` entry is shadowed by a
  lock pin — the override has no effect and can be removed.
- A ring with no `versions/<ring>.yaml` behaves exactly as before promotion was introduced.

### CLI Interface

```
strata promote <subcommand>
```

| Subcommand | Purpose                                                               |
| ---------- | --------------------------------------------------------------------- |
| `start`    | Initiate or advance a promotion (creates branch, edits YAML, commits) |
| `rollback` | Reverse a promotion using the same strategy                           |
| `status`   | Show in-flight promotions (from activity log)                         |
| `matrix`   | Show version matrix across all environments                           |
| `history`  | Query completed promotion records from artifact store                 |
| `log`      | Show activity log for a specific promotion                            |

#### `strata promote start`

```bash
strata promote start \
  --remote tf_landscape \
  --version v2.4.0 \
  --to prd \
  --wave 1
```

| Flag        | Required | Description                                                                                                                                                                                                |
| ----------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--remote`  | yes*     | Remote name being promoted (mutually exclusive with `--module`)                                                                                                                                            |
| `--module`  | yes*     | Module name being promoted (mutually exclusive with `--remote`)                                                                                                                                            |
| `--version` | yes      | Target version                                                                                                                                                                                             |
| `--to`      | yes      | Target ring name (e.g. `prd`). All environments in the ring are candidates; wave selection controls which are targeted.                                                                                    |
| `--wave`    | no       | Integer = ring wave (which environments in the ring, e.g. `--wave 1`). Named string = deployment wave (which tenants within each environment, e.g. `--wave canary`). Defaults to ring wave 1 when omitted. |
| `--dry-run` | no       | Show what would be edited without making changes                                                                                                                                                           |

**What `start` does:**

1. Resolves the target ring from the progression: finds all environments belonging to ring `--to`
2. If `--wave <int>`: filters to environments with that ring-wave number; if `--wave <name>`: targets all ring-wave-1 environments with that deployment wave
3. Loads strategy from any resolved environment's `spec.promotion.strategy`
4. Validates inbound gate: checks the `require` quorum of the previous ring (if `require_progression_order: true`)
5. Loads all deployments registered in `solution.json`, filters to deployments referencing any of the targeted environments
6. Filters by scope: only deployments where `strategy.scope in deployment.spec.layers` participate in deployment waving; others are all-at-once
7. Assigns deployments to deployment waves (iteration → match_labels → default last)
8. For single-layer / no scoped entries: degrades to all-at-once with a console notice
9. Determines the lock file: the ring lock `versions/{ring}.yaml` for the final wave, or a scoped overlay `versions/{ring}.{scope}.yaml` for a canary/early wave
10. Creates branch `promote/{target}-{version}-{ring}`
11. Writes/updates the version-lock pin (and deletes folded overlays on the final wave), commits
12. Appends to activity log (`.strata/promotions/`)
13. Outputs: files modified, branch name, suggested PR command

**Wave mechanics — what gets edited:**

| Wave position                | Action                                                                                                                                                          |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| First/middle wave (not last) | Write a scoped overlay `versions/{ring}.{scope}.yaml` pinning the target for the wave-member deployments only (e.g. `scope: "tenant"`, `scope_selector: acme`). |
| Last wave (`all`)            | Set the pin in the ring lock `versions/{ring}.yaml`. Delete the scoped overlays written by earlier waves (they are now covered by the ring lock).               |
| No scoped entries found      | Degrade to all-at-once: write the pin directly into the ring lock `versions/{ring}.yaml`. Log notice: `"No scoped deployments — falling back to all-at-once"`   |

#### Worked examples

**1. Update a version in one ring and redeploy** (single-env bump, no progression):

```bash
strata promote start --remote iac_core --version v2.5.0 --to dev --wave 1
# → commits on branch promote/iac_core-v2.5.0-dev, opens PR, CI redeploys dev
```

Generated / updated file:

```yaml
# versions/dev.yaml  ← created or updated by strata
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: dev
spec:
  ring: dev
  pins:
    - target: { type: remote, name: iac_core }
      version: v2.5.0        # was v2.4.0
```

---

**2. Advance a proven version set ring-to-ring** (dev → qas → prd with canary):

```bash
# Step 1 — promote to qas (gate checks dev quorum first)
strata promote start --remote iac_core --version v2.5.0 --to qas
```

```yaml
# versions/qas.yaml  ← created by strata
spec:
  ring: qas
  pins:
    - target: { type: remote, name: iac_core }
      version: v2.5.0
```

```bash
# Step 2 — canary wave: acme tenant first (ring wave 1, deployment wave canary)
strata promote start --remote iac_core --version v2.5.0 --to prd --wave canary
```

```yaml
# versions/prd.acme.yaml  ← scoped overlay created by strata (temporary)
spec:
  ring: prd
  scope: tenant
  scope_selector: acme
  pins:
    - target: { type: remote, name: iac_core }
      version: v2.5.0    # only acme; rest of prd still on v2.4.0
```

```bash
# Step 3 — final wave: all remaining prd environments
strata promote start --remote iac_core --version v2.5.0 --to prd --wave all
```

```yaml
# versions/prd.yaml  ← ring lock updated; versions/prd.acme.yaml deleted
spec:
  ring: prd
  pins:
    - target: { type: remote, name: iac_core }
      version: v2.5.0    # now covers all prd environments
```

#### `strata promote rollback`

```bash
strata promote rollback \
  --remote tf_landscape \
  --to prd

# explicit previous version when git derivation is unavailable (shallow clone, CI, etc.)
strata promote rollback \
  --remote tf_landscape \
  --to prd \
  --from-version v2.3.0
```

**`previous_version` resolution — three-tier fallback:**

| Tier | Source                                                                                  | When available                       |
| ---- | --------------------------------------------------------------------------------------- | ------------------------------------ |
| 1    | Activity log (`.strata/promotions/{target}-{version}-{ring}.yaml`)                      | Local machine, log present           |
| 2    | Git merge base: `git merge-base HEAD main` → read `versions/{ring}.yaml` at that commit | Always, unless shallow clone         |
| 3    | `--from-version` explicit flag                                                          | Escape hatch for CI / shallow clones |

Rollback applies the reverse edit using the **same strategy** — if the `prd` ring required
canary-first going forward, it requires canary-first going backward. Writes
`outcome: rolled-back` to the promotion record.

#### `strata promote matrix`

```bash
strata promote matrix
strata promote matrix --remote tf_landscape
```

Reads `versions/<ring>.yaml` lock files (plus any scoped overlays) and deployment
manifests. Outputs a table grouped by ring:

```
Remote: tf_landscape

Ring: dev
┌─────────────┬─────────┬──────────┬──────────┐
│ Environment │ Shared  │ acme     │ contoso  │
├─────────────┼─────────┼──────────┼──────────┤
│ dev1        │ v2.5.0  │ —        │ —        │
│ dev2        │ v2.5.0  │ —        │ —        │
│ internal-dev│ v2.4.0  │ —        │ —        │
└─────────────┴─────────┴──────────┴──────────┘

Ring: prd  (require: any_one ← qas ✔)
┌─────────────┬─────────┬──────────┬──────────┐
│ Environment │ Shared  │ acme     │ contoso  │
├─────────────┼─────────┼──────────┼──────────┤
│ prod-be [1] │ v2.4.0  │ v2.4.0 ⚡ │ v2.4.0   │
│ prod-us [2] │ v2.3.0  │ —        │ —        │
│ prod-sg [2] │ v2.3.0  │ —        │ —        │
└─────────────┴─────────┴──────────┴──────────┘
[1][2] = ring wave number   ⚡ = scoped lock overlay active (deployment wave in progress)
```

#### `strata promote status`

Shows active/in-flight promotions from `.strata/promotions/`:

```
In-flight promotions:
  tf_landscape → prd  v2.3.0 → v2.4.0  ring-wave: 1/2 (prod-be)  deploy-wave: canary (1/2)  started: 2026-06-23
```

#### `strata promote history`

Queries completed `kind: promotion-record` documents from the artifact store:

```bash
strata promote history --ring prd --last 5
strata promote history --remote tf_landscape
```

### Gates

Gates are preconditions evaluated before `strata promote start` proceeds.

| Gate                        | Behavior                                                                                                                                                                                                                                                                                                                  |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `require_progression_order` | Refuses promotion if the previous ring's quorum is not met. The quorum policy is defined on the ring via `require: any_one` (default) or `require: all`. Example: can't promote to `prd` if the `qas` ring has no environment on this version yet (`any_one`), or if not all qas environments are on this version (`all`) |

**Future gates** (not in Phase 3, added incrementally):

| Gate                          | Behavior                                                              |
| ----------------------------- | --------------------------------------------------------------------- |
| `require_plan_clean`          | Refuses if the last Terraform plan for this deployment showed drift   |
| `require_healthy`             | Refuses if the deployment's health check (external) reports unhealthy |
| `require_no_active_promotion` | Refuses if another promotion for the same target+env is in progress   |

Gates are evaluated at `start` time. If a gate fails, strata prints the reason and exits
with code 3 (validation failure). No branch is created, no files are edited.

### State Management

#### Activity log (diagnostic — `.strata/promotions/`)

- One file per active promotion: `{target}-{version}-{environment}.yaml`
- Append-only event log: timestamped actions, gates, files modified, commits
- Gitignored — local diagnostic only. Strata does NOT require this file to function
- All promotion state can be derived from `versions/<ring>.yaml` lock files + git history
- Used by `strata promote status` and `strata promote log`

#### Promotion record (audit — artifact store)

- Written when the **last ring wave** is committed (`strata promote start`) or when rollback edits are committed (`strata promote rollback`)
- `kind: promotion-record` — Kubernetes-style schema, stored in the configured `spec.remotes` artifact store
- Never deleted — permanent audit trail
- Used by `strata promote history`
- **Fields captured:** target (type, name, from/to versions), strategy, progression, ring list, outcome (`completed` \| `partial` \| `rolled-back`), `rollback_of` (rollback link), identity (`initiated_by`, `hostname`), timestamps, git branch + per-wave commit SHAs, gate results (each gate: name, ring, quorum, passed, detail), ring wave summary (environments, files modified/removed, committed_at), links to deployment manifests
- **`outcome: partial`** — written by `strata promote rollback` for a promotion where some waves committed but the last wave never ran
- **`deployment_manifests`** — populated by `strata deploy run` after the PR merges and CI deploys; may be empty at initial write time

### Git Flow

```
main ─────────────────────────────────────────────────────────────────────────►
       │
       │ promote/tf_landscape-v2.4.0-prd
       ├──── ring-wave 1: write versions/prd.acme.yaml (scoped overlay) ─ PR ─ merge ─►
       │                                                            │
       │                                                     (CI runs strata deploy run
       │                                                      → writes deployment manifest)
       │
       ├──── ring-wave 2: set pin in versions/prd.yaml ──────────── PR ──── merge ────►
       │           + delete versions/prd.acme.yaml overlay
       │           + write promotion-record ◄── written here (last wave commit)
```

Each wave is an explicit `strata promote start --wave {name|int}` invocation. The operator
decides when to advance (after observing canary, after CI passes, after stakeholder
approval). There is no auto-advance — strata is a config tool, not a runtime system.

### Interaction with Existing Systems

| System                      | Interaction                                                                                             |
| --------------------------- | ------------------------------------------------------------------------------------------------------- |
| `strata validate`           | Phase 2+: warns if a version jump skips a progression step; warns if a lock pin shadows a hand override |
| `strata deploy list`        | Resolves effective config through the merge chain with version-locks applied as the top layer           |
| `strata build`              | Unchanged — builds whatever version is in the resolved config                                           |
| CI/CD pipeline              | Unchanged — deploys on merge to main. Strata creates the branch and edits, CI validates and deploys     |
| `versions/<ring>.yaml`      | The file written by promotions. Authoritative for pinned targets; slots in above the merge chain        |
| `spec.overrides.remotes`    | Still honored for targets not pinned in a lock. A lock pin shadows the matching override                |
| `spec.tenant`               | Drives scope-overlay resolution for canary waves (which `versions/<ring>.<scope>.yaml` to write)        |
| `meta.labels`               | Matched by `match_labels` for wave assignment. Informational labels live here                           |
| `spec.layers`               | Used by `scope` predicate to filter which deployments participate in waving                             |
| `spec.environments[].scope` | Identifies whether a wave writes the ring lock (`"shared"` = final wave) or a scoped overlay (canary)   |

### Constraints and Non-Goals

- **No runtime monitoring.** Strata does not watch deployments, check health, or auto-advance.
  The operator (or CI) decides when to proceed.
- **No percentage-based wave sizing.** Wave membership is always deliberate (explicit iteration
  or label matching). No random selection, no "deploy to 25% of tenants."
- **No cross-environment atomicity.** Each environment is promoted independently. The
  progression gate ensures ordering but doesn't create atomic multi-env transactions.
- **No promotion of arbitrary fields.** Only `remote` (git ref), `helm_chart` (chart version),
  and `image` (container image tag) targets can be pinned in a version-lock. Type `tool` is
  deferred to Phase 4.
- **Lock files are version-only.** A `version-lock` pins versions and nothing else. Config
  overrides (replica counts, VM sizes, feature flags) stay in environment files — the lock
  never carries them.
- **No auto-discovery of what to promote.** The operator specifies the target and version
  explicitly. Strata doesn't scan registries or detect new versions.

## More Information

- [VCT-INT Architecture](../issues/VCT-INT-architecture.md) — landscape versioning section
- [Environment Configuration](../config/environment.md) — remote reference overrides
- [At Scale Guide](../guides/at-scale.md) — variable flow, tenant model, tier environments

---

## Future Directions — Ideas from Package Managers

These concepts are not implemented. They are recorded here as candidates for future ADRs,
ordered by likely value. All were informed by the patterns in npm, NuGet, and uv/PyPI.

---

### F-1 — Floating versions for CI/CD-driven application rings

**The problem:** Application images on the dev ring are driven by CI/CD at high velocity
— every merge to `main` produces a new image tag. AKS (and similar) can auto-pull the
latest image on pod restart (`imagePullPolicy: Always` + a rolling tag like `main` or
`latest`). Requiring a manual `strata promote start` for every CI build in dev creates
friction with no safety benefit — dev is intentionally unstable.

**The proposal:** A pin can declare `track: latest` instead of `version: exact`. A
floating pin records what the cluster is actually running (best-effort, via CI callback)
but does not block deployment on an explicit promotion step.

```yaml
# versions/dev.yaml — dev ring uses floating for app images
spec:
  ring: dev
  pins:
    - target: { type: image, name: app }
      track: latest                  # float — CI/CD drives this, no promotion step
      resolved: v1.4.2               # last known resolved value (recorded by CI callback)
      resolved_at: 2026-07-10T09:14Z # when the resolved value was last recorded
    - target: { type: remote, name: iac_core }
      version: v2.5.0                # infra is always pinned, even in dev
```

**Ring-level default:** A ring can declare `track_by_default: [image]` so image targets
are floating unless explicitly pinned. Infrastructure targets (`remote`, `helm_chart`)
remain pinned regardless.

```yaml
progressions:
  - name: standard
    rings:
      - name: dev
        track_by_default: [image]   # app images float in dev; infra stays pinned
        environments: [dev1, dev2]
      - name: test
        environments: [test]
        require: any_one            # test requires dev to have a pin (not just floating)
```

**The promotion boundary:** When an operator wants to advance the application from dev
to test, they must choose a specific version — `strata promote start --image app
--version v1.4.2 --to test`. The gate on `test` requires `any_one` dev environment to
have that version pinned (not just floating) before proceeding. This creates a deliberate
checkpoint: floating in dev → operator chooses which CI build to promote → pinned in
test/qas/prd.

**`promote matrix` display for floating pins:**
```
Image: app
  dev   [floating → v1.4.2 @ 2026-07-10T09:14Z]   ← latest resolved value
  test  v1.3.0  (pinned)
  prd   v1.2.0  (pinned)
```

**Design notes:**
- `track: latest` only valid on `type: image` and `type: remote` (branch tracking). Not
  valid on `type: helm_chart` — chart repos don't have a meaningful "latest" concept.
- The `resolved` field is updated by a CI callback (`strata promote record-resolved`) or
  by `strata deploy run` after each deployment. It's informational — the lock file remains
  machine-owned and the floating target is never blocked.
- Rollback on a floating pin: strata pins the `resolved` value at the time of rollback
  request (`track: latest` → `version: v1.4.2`), then proceeds with the rollback.

---

### F-2 — Artifact digests for immutability verification

**The problem:** Image tags and git tags are mutable — `v2.4.0` can be moved to a
different commit or a different image layer. In production, you need proof that what
deployed is exactly what you approved.

**The proposal:** Strata records the resolved immutable reference alongside the mutable
tag in the pin. Written by `strata promote start` at pin time by resolving the tag
against the registry/repo.

```yaml
pins:
  - target: { type: remote, name: iac_core }
    version: v2.5.0
    resolved_sha: abc123def456    # git commit SHA the tag pointed to at pin time

  - target: { type: image, name: app }
    version: v1.4.0
    digest: sha256:a1b2c3...      # OCI content digest — immutable even if tag moves

  - target: { type: helm_chart, name: traefik }
    version: "28.1.0"
    digest: sha256:d4e5f6...      # chart archive SHA
```

`strata validate --deep` verifies that the current tag still resolves to the recorded
digest. Mismatch = warning (tag was moved after pinning — possible supply chain issue).

**Value:** Tamper detection + immutability proof for compliance evidence, especially for
prd rings where the audit trail must prove exactly what was deployed.

---

### F-3 — `strata promote outdated`

**The problem:** Without querying registries, operators don't know when a new version is
available for a pinned target. Discovery is manual today.

**The proposal:** A new command queries each pinned target's source (git tags, Helm repo,
OCI registry) and reports the gap between current pin and latest available.

```bash
strata promote outdated
strata promote outdated --ring prd
```

```
Target: iac_core  (type: remote)
  dev  v2.5.0   latest: v2.6.0  ← newer available
  qas  v2.4.0
  prd  v2.3.0

Target: traefik  (type: helm_chart)
  dev  28.1.0   latest: 28.2.0  ← minor available
  prd  28.0.0   latest: 28.2.0  ← 2 behind

Target: app  (type: image)
  dev  [floating → v1.4.2]  latest: v1.4.2  ✔
  prd  v1.2.0   latest: v1.4.2  ← 2 versions behind
```

Floating pins always show as current (they track by definition).

---

### F-4 — Strict lock mode (`--require-lock`)

**The problem:** In production CI, an operator could forget to run `strata promote start`
before deploying, causing the stack default to be used instead of the intended promoted
version. There's no error today — it silently falls back.

**The proposal:** A flag that fails the build/deploy if any promotable target for the
current ring lacks a lock pin.

```bash
strata build run -f deploy/prd.yaml --require-lock
# → fails with exit 3 if versions/prd.yaml is missing or any expected target has no pin
```

Can also be declared on the ring itself:

```yaml
rings:
  - name: prd
    require_lock: true   # strata build/deploy fails if lock is absent or incomplete
```

Equivalent to `npm ci` over `npm install` — enforces "lock must exist before proceeding"
as a hard CI rule rather than a suggestion.

---

### F-5 — Pre-release channel rules

**The problem:** CI produces `v1.5.0-rc.1` release candidates. An operator could
accidentally promote a release candidate to production.

**The proposal:** Rings declare which version channels are allowed. Strata parses the
semver pre-release identifier and blocks promotion if the channel is not allowed.

```yaml
rings:
  - name: dev
    allowed_channels: [alpha, beta, rc, stable]
  - name: test
    allowed_channels: [beta, rc, stable]
  - name: prd
    allowed_channels: [stable]       # rc and below blocked — gate at promote start
```

Strata parses the pre-release label from the version string:
- `v1.5.0` → `stable`
- `v1.5.0-rc.1` → `rc`
- `v1.5.0-beta.2` → `beta`
- `v1.5.0-alpha.1` → `alpha`
- `v1.5.0-SNAPSHOT` → treated as `alpha` (unrecognized → most restrictive)

**Value:** Prevents release candidate leakage into production without requiring
manual review to catch pre-release version strings.

---

### F-6 — Central version catalog

**The problem:** Different teams might pin incompatible versions of shared infrastructure
(e.g., one team pins `iac_core: v2.5.0`, another pins `iac_core: v2.3.0`). There's no
platform-wide policy on what versions are approved.

**The proposal:** A new `kind: version-catalog` file owned by the platform team that
declares approved version ranges per target. Individual ring locks must stay within those
ranges; `strata validate` rejects a lock pin outside the catalog.

```yaml
# versions/catalog.yaml — platform team owns this, not strata-generated
apiVersion: strata.huybrechts.xyz/v1
kind: version-catalog
meta:
  name: platform
spec:
  targets:
    - target: { type: remote, name: iac_core }
      allowed: ">=v2.0.0,<v3.0.0"    # semver range
      deprecated: ["v2.0.x", "v2.1.x"]   # warn if pinned to these
    - target: { type: helm_chart, name: traefik }
      allowed: ">=28.0.0"
    - target: { type: image, name: app }
      allowed: "*"                    # no constraint — any version allowed
```

Separates two responsibilities:
- **Platform team:** Which versions are approved/supported (`version-catalog`)
- **Operators:** Which approved version each ring runs (`version-lock`, managed by strata)

This is the NuGet Central Package Management pattern applied to infrastructure.

---

### F-7 — Ring-restricted pins

**The problem:** Some targets should never reach certain rings. For example, a
monitoring/debugging sidecar should never be pinned in production, or an alpha feature
flag module should only exist in dev.

**The proposal:** A `rings` constraint on a pin or catalog entry that prevents promotion
beyond a specified ring.

```yaml
# In version-catalog or as an annotation on a stack definition:
- target: { type: helm_chart, name: grafana-debug }
  rings: [dev, test]     # gate: strata blocks promoting this beyond test
```

`strata promote start` refuses if the target ring is not in the `rings` allow-list.
`strata validate` warns if a ring lock contains a pin for a target that is restricted
from that ring.

---

### F-8 — Version constraints (ranges, not just exact pins)

**The problem:** A lock file holds exact pins. But some version requirements are not
about an exact version — they are rules that must hold across all future promotions:

- *"This chart must be >= 28.0.0 because 27.x has CVE-2025-1234"*
- *"This service must be < 2.5.0 because 2.5.0 removed the `/api/v1/health` endpoint
  that the auth service depends on"*
- *"Allow any patch update within 1.2.x, but never cross the minor boundary"*

These rules survive promotions. An exact pin doesn't capture the intent — next time
someone runs `strata promote start` with a version that violates the constraint, nothing
blocks them without constraints.

**The distinction:**

| Concept        | What it is             | Where it lives                     | Who writes it            |
| -------------- | ---------------------- | ---------------------------------- | ------------------------ |
| **Pin**        | Exactly this version   | `versions/<ring>.yaml` (lock file) | Strata (`promote start`) |
| **Constraint** | Must satisfy this rule | Stack module, version catalog      | Operator / platform team |

**Syntax — Terraform-compatible constraint strings:**

Strata targets infrastructure engineers already familiar with Terraform's provider and
module version constraints. The same syntax applies here, backed by a standard semver
parsing library.

```
>= 28.0.0           # minimum (inclusive)
< 30.0.0            # maximum (exclusive)
>= 28.0.0, < 30.0.0 # range — comma = AND
~> 28.1             # pessimistic: >= 28.1.0, < 29.0.0 (minor + patch)
~> 28.1.0           # pessimistic: >= 28.1.0, < 28.2.0 (patch only)
!= 28.1.3           # exclusion — skip a known bad patch
```

**Two declaration forms — short and expanded:**

```yaml
# Short form — preferred for simple rules
spec:
  source:
    chart: traefik
    version: ">= 28.0.0, < 30.0.0"
```

```yaml
# Expanded form — when the constraint needs a documented reason
spec:
  source:
    chart: traefik
    constraints:
      - rule: ">= 28.0.0"
        reason: "28.0.0 patches CVE-2025-1234 — do not downgrade"
      - rule: "< 30.0.0"
        reason: "30.x removes ingress annotations used by tenant routing"
```

Both forms resolve to the same validation logic. Expanded form is for constraints that
operators need to understand when they hit them.

**Where constraints are declared:**

```yaml
# 1. Stack module file — scope: this deployment only
# stack/legacy-service-module.yaml
spec:
  source:
    chart: legacy-service
    version: ">= 1.0.0, < 2.5.0"

# 2. Version catalog — scope: platform-wide (see F-6)
# versions/catalog.yaml
spec:
  targets:
    - target: { type: helm_chart, name: traefik }
      version: ">= 28.0.0"
      constraints:
        - rule: ">= 28.0.0"
          reason: "28.0.0 patches CVE-2025-1234"

# 3. Ring config — scope: specific ring only
progressions:
  - name: standard
    rings:
      - name: prd
        targets:
          - name: app
            version: "~> 1.2"   # prd only allows 1.2.x patch updates
```

**Validation at `promote start`:**

When an operator runs:

```bash
strata promote start --helm legacy-service --version 2.5.1 --to prd
```

Strata checks the proposed pin `2.5.1` against all applicable constraints in order:
1. Version catalog (hardest limit — cannot be overridden)
2. Ring config constraints
3. Stack module constraints

```
Error: legacy-service version 2.5.1 violates constraint "< 2.5.0"
       defined in: stack/legacy-service-module.yaml
       Reason: 2.5.0 removed /api/v1/health — compatibility break with auth-service
       Allowed range: >= 1.0.0, < 2.5.0
       Tip: latest version satisfying this constraint is 2.4.9
```

The exact pin is never written to the lock file if it violates a constraint.

**`strata validate --deep` retroactive check:**

If a constraint is added after a pin was already written, `validate --deep` catches the
drift:

```
Warning: prd lock pin legacy-service=2.5.1 violates constraint "< 2.5.0"
         added to stack/legacy-service-module.yaml on 2026-07-01
         The pin was valid when written but is now out of constraint.
         Run: strata promote start --helm legacy-service --to prd
              to resolve by promoting a compliant version.
```

**`promote matrix` with constraints:**

```
Helm: legacy-service   constraint: >= 1.0.0, < 2.5.0
  dev   v2.4.1  ✔
  test  v2.4.1  ✔
  prd   v2.3.0  ✔
```

If a ring's lock pin violates its constraint, the matrix flags it:

```
Helm: legacy-service   constraint: >= 1.0.0, < 2.5.0
  dev   v2.5.1  ✗  violates < 2.5.0
  test  v2.4.1  ✔
  prd   v2.3.0  ✔
```

**Relationship to other future directions:**

- **F-5 (pre-release channels):** Complements constraints — channels filter by stability
  label (`rc`, `beta`), constraints filter by version range. Both run at `promote start`.
- **F-6 (version catalog):** The catalog is the natural home for platform-wide
  constraints. F-8 defines the constraint syntax; F-6 defines the catalog `kind`.
- **F-4 (strict lock mode):** Orthogonal — strict mode requires a lock to exist;
  constraints validate what version the lock may contain.

---

### F-9 — Versions manifest: centralized human- and tool-editable versions file

**The problem:** Versions are currently scattered across dozens of stack module files.
Updating 40 services requires editing 40 files or running 40 `promote start` commands.
External tools (CI pipelines, renovate-style bots) have no single file to read from and
write to. There is no human-friendly answer to "what version is everything on right now?"

**The proposal:** A new `kind: version` — a centralized, flat, human-editable
file per ring. Strata can generate it as a starting point; humans or tools maintain it;
strata reads it as a version source.

**Where it fits in the model:**

| Layer | File                               | Who writes               | Wins over  |
| ----- | ---------------------------------- | ------------------------ | ---------- |
| 1     | Stack module files                 | Human (structure only)   | —          |
| 2     | Environment overrides              | Human                    | Layer 1    |
| 3     | **Version manifest** ← new         | Human or tool            | Layers 1–2 |
| 4     | Lock file (`versions/<ring>.yaml`) | Strata (`promote start`) | All layers |

For dev and test rings the manifest is often sufficient — no formal promotion ceremony
needed. For qas and prd, `promote start` still runs the full gate and audit process and
writes the lock file, which wins over the manifest.

**File format — flat and tool-friendly by design:**

```yaml
# versions/dev.manifest.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: dev
spec:
  ring: dev
  pins:
    images:
      app:                  v2.1.0
      worker:               v2.1.0
      frontend:             v1.3.2
      migrations:           v2.1.0

    charts:
      traefik:              "28.2.0"
      cert-manager:         "1.16.0"
      kube-prometheus-stack: "58.4.0"
      loki:                 "6.7.0"

    remotes:
      iac_core:             v2.6.0
      iac_network:          v1.9.0
```

The nested-flat structure is intentional. A tool that knows nothing about strata can
update a single value with standard YAML tooling or simple text replacement:

```bash
# CI updates one image after a successful build
yq e '.spec.pins.images.app = "v2.2.0"' -i versions/dev.manifest.yaml

# Renovate-style tool updates multiple charts at once
yq e '
  .spec.pins.charts.traefik = "28.3.0" |
  .spec.pins.charts.cert-manager = "1.17.0"
' -i versions/dev.manifest.yaml
```

No strata knowledge required for the update step — the tool only needs to know the
target name and new version string.

**Strata commands:**

```bash
# Generate starting point — strata reads all stack modules and collects their versions
strata versions init --ring dev
# → writes versions/dev.manifest.yaml

# Export current state for external tool consumption
strata versions export --ring dev --format json
# → { "images": { "app": "v2.1.0", ... }, "charts": { ... }, "remotes": { ... } }

# Apply — strata reads the manifest and updates the ring
strata versions apply -f versions/dev.manifest.yaml
# → updates versions/dev.yaml (lock) from the manifest, creates PR branch with diff
```

**The external tool workflow:**

```
1. CI/tool calls:  strata versions export --ring dev --format json
   → receives current version state as structured JSON

2. Tool compares against registries, determines what to update

3. Tool writes back:
   yq / sed / jinja updates versions/dev.manifest.yaml

4. Tool commits the changed manifest file and opens a PR
   → one PR containing all version changes, however many targets

5. Human reviews and merges (or auto-merge on CI pass for dev)

6. CD pipeline calls: strata versions apply -f versions/dev.manifest.yaml
   → strata validates constraints, writes lock, deploys
```

The tool never needs to understand promotions, rings, or strata internals. It reads a
flat JSON export and writes back to a flat YAML file.

**This also solves the batch problem:**

Updating 40 services is one manifest edit (by a human or a tool), one PR, one review,
one `strata versions apply`. No `promote start` per service. No 40 commands.

```yaml
# versions/dev.manifest.yaml after CI rebuilt all services on a new base image
spec:
  pins:
    images:
      service-a:  v2.1.0   # was v2.0.1
      service-b:  v2.1.0   # was v2.0.1
      service-c:  v1.9.0   # was v1.8.3
      # ... 37 more ...
```

**Jinja / templating integration:**

For teams that already use templating in their GitOps pipelines, the manifest can be
generated from a template with CI-injected variables:

```yaml
# versions/dev.manifest.yaml.j2  (Jinja template, rendered by CI)
spec:
  pins:
    images:
      app:      {{ APP_VERSION }}      # injected from $APP_VERSION env var
      worker:   {{ WORKER_VERSION }}
    charts:
      traefik:  {{ TRAEFIK_VERSION | default("28.2.0") }}
```

The rendered file is committed as `versions/dev.manifest.yaml` and strata reads it. The
template lives in the repo; the values come from CI.

**Relationship to version constraints (F-8):**

`strata versions apply` validates all manifest versions against declared constraints
before writing the lock file. If a manifest version violates a constraint, the apply
fails with the same error as `promote start`:

```
Error: manifest pin traefik=30.1.0 violates constraint "< 30.0.0"
       defined in stack/traefik-module.yaml
       Reason: 30.x removes ingress annotations used by tenant routing
```

The manifest is the human interface. The constraint system is the safety net.

---

### Priority summary

| #   | Idea                                  | Value      | Effort | Notes                                                                           |
| --- | ------------------------------------- | ---------- | ------ | ------------------------------------------------------------------------------- |
| F-1 | Floating versions (CI/CD auto-pickup) | **High**   | Medium | Core to application side automation; extends pin model                          |
| F-2 | Artifact digests                      | **High**   | Low    | Store at pin time; validate on demand                                           |
| F-3 | `promote outdated`                    | **High**   | Medium | Needs per-target registry query                                                 |
| F-4 | Strict lock mode                      | **High**   | Low    | Flag + optional ring config field                                               |
| F-5 | Pre-release channels                  | **Medium** | Medium | Semver parsing + ring config                                                    |
| F-6 | Version catalog                       | **Medium** | High   | New `kind`, new validation pass                                                 |
| F-7 | Ring-restricted pins                  | **Medium** | Low    | Annotation + validation gate                                                    |
| F-8 | Version constraints (ranges)          | **High**   | Medium | Terraform-compatible syntax; two forms (short + expanded)                       |
| F-9 | Versions manifest                     | **High**   | Medium | Centralized human/tool-editable file; solves batch updates and tool integration |

---

## Version System — Implementation Plan

### Overview

The version system is the foundational layer for ADR 0011. The `strata promote` commands
build on top of it. This plan covers the minimum needed for version files to load, resolve,
and apply during `strata build` and `strata deploy` — without any promotion workflow yet.

### Phase 1 — Models and kind registration

All work in `src/strata/models/` and `src/strata/services/unknown_service.py`.
No logic changes — pure data layer. Everything else depends on this phase.

**1.1 `PlatformKind` — two new enum values**
File: `src/strata/models/common_models.py`
- Add `VERSION_LOCK = "version-lock"`
- Add `VERSION_MANIFEST = "version"`

**1.2 `version_lock_model.py` — new file**
File: `src/strata/models/version_lock_model.py`
```
VersionPinTargetType   enum: remote | helm_chart | image | tool
VersionPinTargetModel  fields: type, name
VersionPinModel        fields: target, version, track (optional), resolved (optional), resolved_at (optional)
VersionLockSpecModel   fields: ring, scope, scope_selector, pins: List[VersionPinModel]
VersionLockModel       fields: apiVersion, kind=VERSION_LOCK, meta, spec
```

**1.3 `version_manifest_model.py` — new file**
File: `src/strata/models/version_manifest_model.py`
```
VersionManifestPinsModel  fields: images: Dict[str,str], charts: Dict[str,str], remotes: Dict[str,str]
VersionManifestSpecModel  fields: ring, pins: VersionManifestPinsModel
VersionManifestModel      fields: apiVersion, kind=VERSION_MANIFEST, meta, spec
```

**1.4 `DeploymentVersionRef` + `versions` field on deployment**
File: `src/strata/models/deployment_model.py`
- New `DeploymentVersionRef` model — `file: str` (same pattern as `DeploymentEnvironmentRef`)
- Add `versions: Optional[List[DeploymentVersionRef]]` to `DeploymentSpecModel` after `environments`
- Add `coerce_version_strings` validator (same pattern as `coerce_environment_strings`)

**1.5 Kind routing — `unknown_service.py`**
File: `src/strata/services/unknown_service.py`
- `PlatformKind.VERSION_LOCK` → `VersionLockService`
- `PlatformKind.VERSION_MANIFEST` → `VersionManifestService`

### Phase 2 — Services and resolution layer

Makes version files apply during build/deploy. Depends on Phase 1.

**2.1 `version_lock_service.py` and `version_manifest_service.py` — new files**
Files: `src/strata/services/version_lock_service.py`, `src/strata/services/version_manifest_service.py`
- Thin `BaseService` wrappers — used by `unknown_service` routing and `strata validate`

**2.2 `version_service.py` — new file**
File: `src/strata/services/version_service.py`
```
VersionService.load(path)            → VersionLockModel | VersionManifestModel
VersionService.resolve_pins(files)   → Dict[str, str]   # target_name → version, list order, later wins
VersionService.apply_to_workspace(workspace_model, pins)  # applies resolved pins to workspace
```
Normalises both model formats to a flat `{name: version}` dict before applying.

**2.3 Hook into deployment resolution**
File: `src/strata/services/deployment_service.py` (or workspace resolution path)
- After environments are merged, if `spec.versions` is present:
  1. Load each file in list order via `VersionService.load()`
  2. `VersionService.resolve_pins()` → flat pin dict
  3. `VersionService.apply_to_workspace()` → overrides resolved versions

### Phase 3 — Validation

**3.1 Shadowed-override warning**
When a `spec.overrides.remotes[].reference` (or equivalent image/chart field) is present
AND the same target has a lock pin, emit a validation warning:
`"override for 'iac_core' is shadowed by versions/prd.yaml — the override has no effect"`
Runs during `strata validate --deep` on a deployment file.

**3.2 Wire into `strata validate`**
`VersionLockService` and `VersionManifestService` respond to `strata validate <file>`
so version files can be validated directly.

### Phase 4 — `strata versions` CLI ✅ Implemented (2026-07-11)

Depends on Phases 1–3. Commands: `init`, `export`, `apply`, `refresh`.

**4.1 Scaffold** ✅
- `src/strata/commands/cli_versions.py` — Click group `strata versions` (wiring only)
- `src/strata/commands/versions/` — `BaseVersionsCommand` + four subcommand modules
- `src/strata/controllers/version_controller.py` — `VersionController` (business logic)
- Registered in `src/strata/cli.py`

**4.2 `strata versions init`** ✅
- Scaffolds `versions/<ring>.yaml` with empty pins structure
- `--out` to specify a custom path; `--force` to overwrite

**4.3 `strata versions export`** ✅
- Loads a `kind: version` or `kind: version-lock` file via `VersionService`
- Prints resolved flat pin map; supports `--output json|text|console`

**4.4 `strata versions apply`** ✅
- Converts a `kind: version` manifest into a `kind: version-lock` file
- `--out` for custom lock path; `--force` to overwrite existing lock

**4.5 `strata versions refresh`** ✅ (added beyond original plan)
- Scans workspace YAML files (`kind: module`, `workspace`, `configuration`, `environment`)
  and diffs against the manifest's current pins
- New targets are added with seed versions; stale targets are reported or removed with `--remove-stale`
- `--dry-run` to preview changes without writing

### Dependency order

```
Phase 1 (models)  →  Phase 2 (resolution)  →  Phase 3 (validation)
                                            →  Phase 4 (CLI)
```

### Minimum viable slice (start here)

1. Phase 1.1 + 1.2 — `VERSION_LOCK` kind and model
2. Phase 1.4 — `versions` field on deployment (parse, no apply yet)
3. Phase 2.1 + 2.2 + 2.3 — load and apply during build/deploy
4. Phase 3.2 — `strata validate versions/prd.yaml` works

This gives: declare a versions file in a deployment → it applies during build → validate
the file directly. The `strata versions` CLI and the full promote commands layer on top.

---

## Implementation Status

| Phase | Description                                                                                                                                                                                                                                                                                                                                              | Status | Completed  |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | ---------- |
| 1     | Models and kind registration (`VERSION_LOCK`, `VERSION_MANIFEST`, deployment field)                                                                                                                                                                                                                                                                      | ✅ Done | 2026-07-11 |
| 2     | Services and resolution layer (`VersionService`, `_apply_version_pins` hook)                                                                                                                                                                                                                                                                             | ✅ Done | 2026-07-11 |
| 3     | Validation wiring (`platform_validator.py`, `cli_schema.py`)                                                                                                                                                                                                                                                                                             | ✅ Done | 2026-07-11 |
| 4     | `strata versions` CLI (`init`, `export`, `apply`, `refresh`)                                                                                                                                                                                                                                                                                             | ✅ Done | 2026-07-11 |
| P-1   | Promote Phase 1 — strategy model + validation (`promotion_model.py`, spec fields, env ring ref check)                                                                                                                                                                                                                                                    | ✅ Done | 2026-07-11 |
| P-2   | Promote Phase 2 — `strata promote` CLI group: start / rollback / status / matrix / history / log                                                                                                                                                                                                                                                         | ✅ Done | 2026-07-11 |
| P-3   | Promote Phase 3 — validation wiring (`PromotionRecordService`, `platform_validator.py`, `unknown_service.py`) + CLI tests                                                                                                                                                                                                                                | ✅ Done | 2026-07-11 |
| P-4   | Promote Phase 4 — Strict lock mode (F-4): `ProgressionRingModel.require_lock`, `--require-lock` flag on `build run` / `deploy run`, `DeploymentService.check_require_lock_mode`                                                                                                                                                                          | ✅ Done | 2026-07-11 |
| P-5b  | Promote Phase 5b — Shadowed-override warnings in `strata validate --deep`: `VersionService.find_shadowed_overrides()`, `DeploymentService._check_version_pin_shadows()`, `BaseValidator` warnings infrastructure, `PlatformValidator` warning collection, `ValidateCommand` console + JSON output                                                        | ✅ Done | 2026-07-11 |
| P-5a  | Promote Phase 5a — `type: tool` support: `WorkspaceIacModel.version` field, `VersionManifestPinsModel.tools` field, `VersionService.apply_to_workspace()`, `DeploymentService._apply_tool_version_pins()` wired after workspace load, manifest `tools` dict included in `resolve_pins`                                                                   | ✅ Done | 2026-07-11 |
| F-2   | Artifact digest policy — `ProgressionRingModel.require_digests` field; `VersionService.validate_sha_format()` (git SHA / OCI digest format rules); `DeploymentService.check_digest_policy()` (ring-policy errors + format warnings); `--verify-digests` flag on `strata validate --deep` wired through `ValidateCommand` → `PlatformValidator`; 29 tests | ✅ Done | 2026-07-11 |
