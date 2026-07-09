# Promotion strategies for version progression across environments

- Status: Accepted
- Date: 2026-06-23

## Context and Problem Statement

Strata manages deployments across multiple environments (dev, test, acceptance, production)
and multiple tenants. Version changes — whether Terraform landscape references or Helm
chart versions — must progress through environments in a controlled, auditable way.

Today, promotion is entirely manual: an operator edits a `spec.overrides.remotes[].reference`
or a module `chart_version` in an environment YAML file, commits, and deploys. There is no
guardrail preventing a direct jump to production, no canary mechanism, no rollback tracking,
and no visibility into what version is running where across the fleet.

The platform needs a structured promotion system that:

- Defines allowed progressions through ordered **rings** (dev → test → qas → prd), where each ring
  can contain multiple environments (e.g., `prd` = prod-be, prod-us, prod-sg)
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

### Terraform vs Helm: different risk profiles

| Dimension               | Terraform (remote ref)                             | Helm (chart version)                                  |
| ----------------------- | -------------------------------------------------- | ----------------------------------------------------- |
| What changes            | `spec.overrides.remotes[].reference`               | Module override `chart_version` or `services[].image` |
| Blast radius            | Infrastructure — add/remove/modify cloud resources | Application — new containers, config values           |
| Validation before apply | `terraform plan` diff is essential                 | Helm diff is nice-to-have                             |
| Rollback cost           | High — may require state surgery                   | Low — helm rollback or redeploy previous version      |
| Canary feasibility      | Hard — infra is usually shared per zone            | Natural — each tenant has own helm release            |
| Shared vs isolated      | Shared: all tenants in a zone use the same AKS     | Isolated: each tenant has own namespace + release     |

This means the promotion strategy must be type-aware — the same progression
may use different approaches depending on what's being promoted.

### A promotion is a YAML edit

The mechanism is always the same:

1. Determine which file to edit and what value to change
2. Create a branch, make the edit, commit
3. CI validates (plan, lint, overlap check)
4. PR reviewed and merged
5. CI deploys

Strata's job is steps 1-2 and providing visibility. Git and CI handle steps 3-5.

### Canary is a special case of waves

Waves operate at two independent levels:

**Deployment waves** — within a single environment, controls which tenants/deployments
receive the version first. Membership is declared on `kind: deployment`.

| Approach     | Waves | Wave 1 target                                       |
| ------------ | ----- | --------------------------------------------------- |
| All-at-once  | `1`   | Shared environment file (all deployments)           |
| Canary-first | `2`   | Deployments matching wave 1, then shared env file   |
| Multi-wave   | `3`   | Deployments declaring iteration 1, then 2, then all |

The underlying mechanic is always: "which deployments get this version in this wave?"
Wave membership is determined per-deployment via `spec.promotion.wave` (explicit
`iteration` or `match_labels`). Deployments without wave config default to the last wave.

**Ring waves** — within a ring, controls which environments receive the version first.
Membership is declared on the ring's `environments[]` list via a numeric `wave:` field.
This is distinct from deployment waves: it sequences *environments* (e.g., prod-be before
prod-us) rather than *deployments* within one environment.

| Approach       | Environments in ring        | Ring waves |
| -------------- | --------------------------- | ---------- |
| All-at-once    | prod-be, prod-us, prod-sg   | 1 (all together) |
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
        progression: standard
        waves:
          - name: canary                      # first: deployments with iteration: 1
          - name: all                         # last: everyone else
        scope: tenant                         # only tenant-layer deployments are waved
        gates:
          require_progression_order: true     # previous ring quorum must be satisfied

      - name: app-wave
        type: module                          # promotes chart_version / image tags
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
manifests) on promotion completion or rollback. This is the audit evidence:

```yaml
# Stored in artifact remote, e.g. manifests/promotions/prom-20260623-001.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: promotion-record
meta:
  name: prom-20260623-001
  labels:
    target: tf_landscape
    ring: prd
spec:
  target:
    type: remote
    name: tf_landscape
    from_version: v2.3.0
    to_version: v2.4.0
  strategy: infra-cautious
  progression: standard
  rings: [dev, test, qas, prd]
  outcome: completed                          # completed | rolled-back
  ring_waves:
    - ring_wave: 1
      environments: [prod-be]
      deployment_waves:
        - wave: canary
          deployments: [acme]
          started: 2026-06-23T10:00:00Z
          deployed: 2026-06-23T15:00:00Z
    - ring_wave: 2
      environments: [prod-us, prod-sg]
      deployment_waves:
        - wave: all
          deployments: all
          started: 2026-06-24T14:30:00Z
          deployed: 2026-06-24T15:10:00Z
  initiated_by: brady
  started: 2026-06-23T10:00:00Z
  completed: 2026-06-24T15:10:00Z
  branch: promote/tf_landscape-v2.4.0-prd
  manifests:                                  # links to deployment manifests produced
    - acme_eu_prod-be/manifest-20260623.yaml
    - contoso_eu_prod-be/manifest-20260624.yaml
```

**Why two artifacts?**

| Concern      | Activity log (`.strata/promotions/`)                          | Completed record (artifact store)            |
| ------------ | ------------------------------------------------------------- | -------------------------------------------- |
| Purpose      | Diagnostic trace — watch what strata is doing, debug failures | Audit evidence — who, when, what, outcome    |
| Required     | No — promotion works without it (all state derived from git)  | Yes — authoritative audit trail              |
| Lifetime     | Kept permanently (gitignored — local only)                    | Permanent — never deleted                    |
| Content      | Timestamped event log: actions, gates, files, commits         | Summary: waves, outcome, manifests produced  |
| Storage      | Local workspace (`.strata/`), gitignored                      | Same artifact remote as deployment manifests |
| Queryable by | `strata promote status` (in-flight diagnostics)               | `strata promote history` (historical)        |
| Retention    | Always kept locally — accumulates for debugging               | Always written                               |

The completed record follows the same pattern as deployment manifests: it uses the
Kubernetes-style schema (`apiVersion`, `kind`, `meta`, `spec`), is stored in a configured
`spec.remotes` artifact store, and provides the audit trail showing that version changes
followed the declared promotion strategy.

**Git flow for wave progression:**

Strata resolves all deployments in the target environment, evaluates each deployment's
`spec.promotion.wave` config (iteration, match_labels, or default=last), and groups
them into waves defined by the strategy.

Wave 1 (canary — deployments matching wave 1):
- For each wave-1 deployment, edits the existing tenant file directly:
  `tenants/acme.yaml` → adds `spec.overrides.remotes[tf_landscape].reference: v2.4.0`
- Already in the merge chain — no new files, no auto-discovery needed
- This is exactly what a manual promotion does today, just automated

Wave N (final — all remaining):
- Edits the shared environment file:
  `environments/production.yaml` → `spec.overrides.remotes[tf_landscape].reference: v2.4.0`
- Removes per-tenant overrides (shared file now covers everyone, tenant-level pin is redundant)

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

2. **The YAML edit is the promotion.** Strata already owns the YAML schema, validation, and
   deployment. Generating the correct YAML edit for a canary vs full rollout is a natural extension.

3. **State tracking enables visibility.** Without tracking, "what version is running where"
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

**Deferred — `strata promote matrix`:**
`promote matrix` requires loading the full merged environment model for every registered
deployment to read effective versions — a fleet-wide `EnvironmentService` traversal that
is expensive without a resolved-model cache. This command is deferred until a `.strata/`
caching strategy is in place (see OQ-17). When implemented, `promote matrix` will read
from the cache rather than re-resolving every environment file on every invocation.

The same caching mechanism would benefit other fleet-wide operations: bulk validation,
drift detection, and any future command that needs a resolved view of all deployments
without a full re-load. That design belongs in a separate ADR.

### Consequences

- Good: Unified promotion model for both infrastructure and application changes.
- Good: Strategies are configuration-as-code — auditable, versioned, team-shared.
- Good: Phased implementation means model + validation ships before automation; matrix deferred until caching lands.
- Good: Git remains the source of truth — strata automates the edits but the PR/merge flow is unchanged.
- Good: Unpromotion uses the same strategy, preventing unsafe shortcuts under pressure.
- Good: Completed promotion records stored in the same artifact remote as deployment manifests — no new infrastructure for audit storage. Reuses existing `spec.remotes` configuration.
- Good: `kind: promotion-record` follows the Kubernetes-style schema, consistent with all other strata documents.
- Good: No required runtime state — all promotion state derived from environment files and git. Activity log is diagnostic-only.
- Good: Terraform landscapes benefit equally — remote reference versions progress through environments via the same progression gate. Zone-layer infra uses a single `all` wave (all-at-once per env, still gated by `require_progression_order`). Tenant-layer infra can canary via `scope: tenant`.
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

7. ~~**Promotion override file not in merge chain:**~~ Resolved — no new files needed.
   Promotion edits the existing tenant file's `spec.overrides.remotes` field directly
   (wave 1), then edits the shared environment file and removes the per-tenant override
   (wave N). Same files, same merge chain, same mechanism as manual promotion.
   No auto-include or glob needed.

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
    field on `kind: environment`. Phase 1 `status` and `matrix` commands scan
    `spec.overrides.remotes[]` in the merged environment model. This requires loading
    the full environment model per environment file via `EnvironmentService`, not a
    lightweight YAML parse.

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

    The promotion controller identifies which file to edit per wave by matching
    `entry.scope` against the strategy's `scope` field:
    - `scope: "tenant"` entry → edited for canary/early waves (per-deployment override)
    - `scope: "shared"` entry → edited for the final wave (shared environment file)
    - `scope: null` entries → not targeted for wave-specific edits

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

| Concept              | Definition                                                                                                                         |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **Ring**             | A named group of environments at the same delivery tier (e.g., `prd` groups `prod-be`, `prod-us`, `prod-sg`). Progressions advance ring-by-ring. |
| **Progression**      | An ordered list of rings that a version must traverse (e.g., `dev → test → qas → prd`). Each ring can contain multiple environments. |
| **Strategy**         | A named policy that governs HOW a version moves into a specific ring (wave count, scope, gates)                                    |
| **Wave**             | An ordering unit. *Deployment wave* (named): a subset of deployments within one environment that receive the version together. *Ring wave* (integer): a subset of environments within a ring that receive the version together. |
| **Scope**            | A layer name from `configuration.spec.layering[]` that determines which deployments participate in deployment waving               |
| **Gate**             | A precondition that must pass before promotion proceeds (e.g., quorum of previous ring must have the version)                      |
| **Promotion target** | The thing being versioned — a remote reference (`spec.overrides.remotes[].reference`) or a module field (`chart_version`, `image`) |

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

      - name: app-gradual
        type: module
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

| Field   | Type        | Required | Description                                                      |
| ------- | ----------- | -------- | ---------------------------------------------------------------- |
| `name`  | string      | yes      | Unique identifier for the progression                            |
| `rings` | list[Ring]  | yes      | Ordered list of rings. Position = promotion order                |

**Ring fields:**

| Field          | Type                    | Required | Description                                                                                                        |
| -------------- | ----------------------- | -------- | ------------------------------------------------------------------------------------------------------------------ |
| `name`         | string                  | yes      | Ring identifier (e.g. `dev`, `test`, `qas`, `prd`)                                                                |
| `environments` | list[string \| RingEnv] | yes      | Environment names in this ring. Bare strings = `{ name, wave: null }` (all together)                              |
| `require`      | enum \| null            | no       | Inbound gate quorum: `any_one` (default), `all`, `null` (no gate — first ring). Evaluated only when strategy gate `require_progression_order: true` |

**RingEnv fields (when environments need intra-ring wave ordering):**

| Field  | Type    | Required | Description                                                                           |
| ------ | ------- | -------- | ------------------------------------------------------------------------------------- |
| `name` | string  | yes      | Environment name                                                                      |
| `wave` | integer | no       | Ring wave number (1, 2, 3…). Environments with the same wave number execute together. Omit for all-at-once. |
| ------------- | -------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`        | string         | yes      | Unique identifier for the strategy                                                                                                           |
| `type`        | enum           | yes      | `remote` (promotes `spec.overrides.remotes[].reference`) or `module` (promotes `chart_version` / `image`)                                    |
| `progression` | string         | yes      | References a named progression                                                                                                               |
| `waves`       | list[Wave]     | yes      | Ordered list of wave definitions. Position = execution order                                                                                 |
| `scope`       | string \| null | no       | Layer name from `configuration.spec.layering[]`. Only deployments with this layer key participate in waving. `null` = all-at-once, no waving |
| `gates`       | dict           | no       | Named gate conditions (see Gates section)                                                                                                    |

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

| Flag        | Required | Description                                                                                                                                                                                                                    |
| ----------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--remote`  | yes*     | Remote name being promoted (mutually exclusive with `--module`)                                                                                                                                                                |
| `--module`  | yes*     | Module name being promoted (mutually exclusive with `--remote`)                                                                                                                                                                |
| `--version` | yes      | Target version                                                                                                                                                                                                                 |
| `--to`      | yes      | Target ring name (e.g. `prd`). All environments in the ring are candidates; wave selection controls which are targeted.                                                                                                        |
| `--wave`    | no       | Integer = ring wave (which environments in the ring, e.g. `--wave 1`). Named string = deployment wave (which tenants within each environment, e.g. `--wave canary`). Defaults to ring wave 1 when omitted.                    |
| `--dry-run` | no       | Show what would be edited without making changes                                                                                                                                                                               |

**What `start` does:**

1. Resolves the target ring from the progression: finds all environments belonging to ring `--to`
2. If `--wave <int>`: filters to environments with that ring-wave number; if `--wave <name>`: targets all ring-wave-1 environments with that deployment wave
3. Loads strategy from any resolved environment's `spec.promotion.strategy`
4. Validates inbound gate: checks the `require` quorum of the previous ring (if `require_progression_order: true`)
5. Loads all deployments registered in `solution.json`, filters to deployments referencing any of the targeted environments
6. Filters by scope: only deployments where `strategy.scope in deployment.spec.layers` participate in deployment waving; others are all-at-once
7. Assigns deployments to deployment waves (iteration → match_labels → default last)
8. For single-layer / no scoped entries: degrades to all-at-once with a console notice
9. Identifies which files to edit via `spec.environments[].scope` matching
10. Creates branch `promote/{target}-{version}-{ring}`
11. Makes YAML edits, commits
12. Appends to activity log (`.strata/promotions/`)
13. Outputs: files modified, branch name, suggested PR command

**Wave mechanics — what gets edited:**

| Wave position                | Action                                                                                                                                                                                                |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| First/middle wave (not last) | For each wave-member deployment: edit the `spec.environments` entry where `scope == strategy.scope` (e.g. `scope: "tenant"`). Set `spec.overrides.remotes[{name}].reference: {version}` in that file. |
| Last wave (`all`)            | Edit the `scope: "shared"` environment file: set `spec.overrides.remotes[{name}].reference: {version}`. Remove scoped overrides written by earlier waves (they are now covered by the shared file).   |
| No scoped entries found      | Degrade to all-at-once: edit the `scope: "shared"` file (or the only env file if none are annotated). Log notice: `"No scoped deployments — falling back to all-at-once"`                             |

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

| Tier | Source                                                                    | When available                       |
| ---- | ------------------------------------------------------------------------- | ------------------------------------ |
| 1    | Activity log (`.strata/promotions/{target}-{version}-{ring}.yaml`)         | Local machine, log present           |
| 2    | Git merge base: `git merge-base HEAD main` → read env file at that commit | Always, unless shallow clone         |
| 3    | `--from-version` explicit flag                                            | Escape hatch for CI / shallow clones |

Rollback applies the reverse edit using the **same strategy** — if the `prd` ring required
canary-first going forward, it requires canary-first going backward. Writes
`outcome: rolled-back` to the promotion record.

#### `strata promote matrix`

```bash
strata promote matrix
strata promote matrix --remote tf_landscape
```

Scans environment files and deployment manifests. Outputs a table grouped by ring:

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
[1][2] = ring wave number   ⚡ = per-tenant override (deployment wave in progress)
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

| Gate                        | Behavior                                                                                                                                                                    |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
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
- All promotion state can be derived from environment files + git history
- Used by `strata promote status` and `strata promote log`

#### Promotion record (audit — artifact store)

- Written on completion or rollback to the configured `spec.remotes` artifact store
- `kind: promotion-record` — Kubernetes-style schema
- Contains: target, versions (from/to), strategy used, waves executed, outcome, timestamps,
  initiated_by, branch, links to produced deployment manifests
- Used by `strata promote history`
- Never deleted — permanent audit trail

### Git Flow

```
main ─────────────────────────────────────────────────────────────►
       │                              │
       │ promote/tf_landscape-v2.4.0-production
       ├──── wave 1: edit tenants/acme.yaml ──── PR ──── merge ───►
       │                                                  │
       │                                                  │
       ├──── wave 2: edit environments/production.yaml ── PR ── merge ─►
       │           + remove acme override
```

Each wave is an explicit `strata promote start --wave {name}` invocation. The operator
decides when to advance (after observing canary, after CI passes, after stakeholder
approval). There is no auto-advance — strata is a config tool, not a runtime system.

### Interaction with Existing Systems

| System                      | Interaction                                                                                             |
| --------------------------- | ------------------------------------------------------------------------------------------------------- |
| `strata validate`           | Phase 2+: warns if a version jump skips a progression step                                              |
| `strata deploy list`        | Already resolves effective config through the merge chain — sees promotion overrides natively           |
| `strata build`              | Unchanged — builds whatever version is in the resolved config                                           |
| CI/CD pipeline              | Unchanged — deploys on merge to main. Strata creates the branch and edits, CI validates and deploys     |
| `spec.overrides.remotes`    | The exact field being edited by promotions. Existing override merge chain handles layering              |
| `spec.tenant`               | Drives file resolution for wave-1 edits (which tenant file to edit). Not read by `match_labels`         |
| `meta.labels`               | Matched by `match_labels` for wave assignment. Informational labels live here                           |
| `spec.layers`               | Used by `scope` predicate to filter which deployments participate in waving                             |
| `spec.environments[].scope` | Identifies which environment file to edit per wave (`"shared"` = final wave, layer name = canary waves) |

### Constraints and Non-Goals

- **No runtime monitoring.** Strata does not watch deployments, check health, or auto-advance.
  The operator (or CI) decides when to proceed.
- **No percentage-based wave sizing.** Wave membership is always deliberate (explicit iteration
  or label matching). No random selection, no "deploy to 25% of tenants."
- **No cross-environment atomicity.** Each environment is promoted independently. The
  progression gate ensures ordering but doesn't create atomic multi-env transactions.
- **No promotion of arbitrary fields.** Only `spec.overrides.remotes[].reference` (type: remote)
  and module version fields (type: module) are supported promotion targets.
- **No auto-discovery of what to promote.** The operator specifies the target and version
  explicitly. Strata doesn't scan registries or detect new versions.

## More Information

- [VCT-INT Architecture](../issues/VCT-INT-architecture.md) — landscape versioning section
- [Environment Configuration](../config/environment.md) — remote reference overrides
- [At Scale Guide](../guides/at-scale.md) — variable flow, tenant model, tier environments
