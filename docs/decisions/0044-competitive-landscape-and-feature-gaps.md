# Competitive Landscape and Feature Gaps

- Status: accepted
- Date: 2026-07-15

## Context and Problem Statement

Strata is not the first tool to tackle infrastructure orchestration above Terraform and
Ansible. Multiple open-source and commercial products address overlapping concerns. This
ADR catalogues those tools, identifies what they do well (or better), and assesses whether
each capability is relevant for strata to adopt.

This is an inventory — individual features that warrant implementation will get their own
ADRs.

## Competitor Tools

| Tool               | Type              | Primary Strength                                                       |
| ------------------ | ----------------- | ---------------------------------------------------------------------- |
| Terragrunt         | OSS (Gruntwork)   | DRY Terraform configs, dependency orchestration, environment promotion |
| Terramate          | OSS               | Stacks, change detection, code generation, orchestration               |
| Atmos              | OSS (Cloud Posse) | YAML-driven stacks, components, vendoring, environment inheritance     |
| Spacelift          | SaaS              | Policy-as-code, drift detection, approval workflows, VCS integration   |
| Env0               | SaaS              | Self-service environments, cost guardrails, TTL environments           |
| Scalr              | SaaS              | Hierarchical workspaces, OPA policies, RBAC, cost tracking             |
| Pulumi Deployments | SaaS + OSS        | Real-language IaC, deployment automation, environments                 |
| Crossplane         | OSS (CNCF)        | Kubernetes-native infra provisioning, compositions, XRDs               |
| Humanitec / Score  | SaaS + OSS        | Platform engineering, workload spec abstraction                        |
| ArgoCD / Flux      | OSS (CNCF)        | GitOps reconciliation for Kubernetes workloads                         |

## Feature Comparison

Legend:
- **Strata status**: ✅ exists | 🔨 ADR proposed | ❌ not present
- **Useful for strata?**: yes / maybe / no — with rationale
- **Ref**: existing ADR if applicable

### 1. DRY Configuration & Inheritance

|                         |                                                                                                                                              |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Terragrunt (`include` blocks), Atmos (stack inheritance), Terramate (globals)                                                                |
| **What they do**        | Eliminate repetition across environments via layered inheritance, includes, and variable cascading                                           |
| **Better than strata?** | Terragrunt's `include` is more flexible. Atmos has deep inheritance with mixins. Strata's environment composition is functional but simpler. |
| **Strata status**       | ✅ Environment composition exists                                                                                                             |
| **Useful for strata?**  | Maybe — current flat-merge works (ADR 0024) but mixin/include patterns could reduce duplication in large repos                               |
| **Ref**                 | [ADR 0024](0024-environment-composition-flat-merge-fix.md)                                                                                   |

### 2. Dependency Graph / Execution Order

|                         |                                                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Terragrunt (`dependency` blocks), Spacelift (stack dependencies), Atmos (component deps)                         |
| **What they do**        | Automatically determine deployment order from declared dependencies. Parallel where safe, serial where required. |
| **Better than strata?** | Yes — strata stages are linear and manually ordered. No automatic parallelism within a deployment.               |
| **Strata status**       | 🔨 Partially proposed                                                                                             |
| **Useful for strata?**  | Yes — would enable parallel stage execution and cross-deployment dependency resolution                           |
| **Ref**                 | [ADR 0015](0015-flow-command-dependency-graph.md)                                                                |

### 3. Drift Detection

|                         |                                                                                                   |
| ----------------------- | ------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Spacelift (scheduled drift runs), Env0 (drift alerts), Crossplane (continuous reconciliation)     |
| **What they do**        | Periodically compare actual infrastructure state against declared state. Alert or auto-remediate. |
| **Better than strata?** | Yes — strata has no scheduled drift detection today.                                              |
| **Strata status**       | 🔨 ADR proposed                                                                                    |
| **Useful for strata?**  | Yes — critical for production governance. Could run as `strata deploy health` extension.          |
| **Ref**                 | [ADR 0008](0008-infrastructure-drift-detection.md)                                                |

### 4. Policy Engine / Guardrails

|                         |                                                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------------------ |
| **Who does it well**    | Spacelift (OPA + built-in), Scalr (OPA), Env0 (custom policies), Terramate (checks)              |
| **What they do**        | Evaluate policies before/after plan — block deploys that violate cost, security, or naming rules |
| **Better than strata?** | Yes — strata has the ADR but no implementation. Spacelift's is production-hardened.              |
| **Strata status**       | 🔨 ADR proposed                                                                                   |
| **Useful for strata?**  | Yes — especially for enterprise/multi-tenant scenarios                                           |
| **Ref**                 | [ADR 0006](0006-policy-engine-for-deployment-guardrails.md)                                      |

**Implementation approach — OPA (Open Policy Agent):**

OPA is a CNCF-graduated, language-agnostic policy engine. Policies are written in Rego
(a declarative query language). OPA evaluates JSON input and returns structured
allow/deny decisions.

**How competitors integrate OPA:**

- **Spacelift:** Converts Terraform plan to JSON → evaluates against Rego policies stored
  in the platform → blocks apply on deny. Supports `plan`, `init`, and `notification`
  policy types with different trigger points.
- **Scalr:** OPA policies attached at account/environment/workspace levels (hierarchical).
  Policies run on `terraform plan` JSON output. Supports `enforced` (hard block) and
  `advisory` (warn only) severity.
- **Env0:** Custom policies via OPA or cost-based rules. Integrates with `conftest` for
  local evaluation.

**How strata could implement it:**

```
strata validate <file> --policy ./policies/    # evaluate against local Rego policies
strata build plan -f deploy.yaml --policy ...  # evaluate plan output against policies
strata deploy run -f deploy.yaml --policy ...  # pre-deploy gate
```

Integration points in the strata lifecycle:
1. **Pre-build (schema + policy):** Evaluate the resolved YAML model against naming,
   tagging, and structural policies before any Terraform runs.
2. **Post-plan (plan output):** Convert `terraform plan -json` output to OPA input,
   evaluate against cost/security/compliance rules.
3. **Pre-deploy gate:** Final policy check before `terraform apply`. Block if any
   `enforced` policy fails.

Policy file structure (candidate):
```
policies/
├── naming.rego          # resource naming conventions
├── security.rego        # no public endpoints, encryption required
├── cost.rego            # instance size limits, region restrictions
└── compliance.rego      # tagging requirements, data residency
```

Example Rego policy — "all resources must have cost-center tag":
```rego
package strata.policy

deny[msg] {
    resource := input.planned_values.root_module.resources[_]
    not resource.values.tags["cost-center"]
    msg := sprintf("Resource %s missing required tag: cost-center", [resource.address])
}
```

**Dependencies:** `opa` binary (Go, single binary, ~50MB) or Python `regopy` /
`opa-python` for embedded evaluation without subprocess.

**Severity model:**
- `enforced` — hard block, exit code 3
- `advisory` — warn in output, proceed with deploy
- Configurable per-environment (e.g., advisory in dev, enforced in prod)

### 5. Cost Estimation

|                         |                                                                                                    |
| ----------------------- | -------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Env0 (Infracost integration), Spacelift (cost policies), Scalr (cost dashboard)                    |
| **What they do**        | Estimate cloud costs from Terraform plan output before apply. Set budgets and alert on thresholds. |
| **Better than strata?** | Yes — strata has no cost estimation today.                                                         |
| **Strata status**       | 🔨 ADR proposed                                                                                     |
| **Useful for strata?**  | Yes — especially pre-deploy cost visibility in `strata build plan` output                          |
| **Ref**                 | [ADR 0031](0031-cost-estimation-and-visibility.md)                                                 |

**How competitors compute costs — Infracost:**

The dominant approach for *pre-deploy* estimation is **Infracost** (not the cloud cost APIs,
which report *actual* spend after the fact).

| Cloud cost API            | What it actually is                                   |
| ------------------------- | ----------------------------------------------------- |
| AWS Cost Explorer         | Historical spend — post-deploy only                   |
| Azure Cost Management API | Actual/amortized costs, budgets, alerts — post-deploy |
| GCP Billing Export        | Detailed billing data for analysis — post-deploy      |

Infracost takes a different approach:
1. Parses `terraform plan -json` output
2. Maps each resource type to a pricing lookup using its own bundled price database,
   scraped from cloud provider pricing pages:
   - AWS: Bulk Pricing API
   - Azure: Retail Prices API (`prices.azure.com`)
   - GCP: Cloud Billing Catalog API
3. Returns monthly cost estimate per resource + a before/after diff

**Infracost pricing/licensing:**

| Tier            | Cost            | Limits                                                |
| --------------- | --------------- | ----------------------------------------------------- |
| Community (OSS) | Free, self-host | Unlimited — Apache 2.0                                |
| Cloud Free      | Free            | 1,000 runs/month — hosted API, PR comments, dashboard |
| Cloud Team      | ~$50/user/month | Unlimited runs, SSO, custom price books               |
| Enterprise      | Custom          | On-prem, air-gapped, custom SKU mappings              |

The OSS binary ships with a **bundled pricing database** — no API key required for basic
estimation. The cloud API key is only needed for latest-price updates or the hosted
dashboard. License is Apache 2.0.

**How strata could integrate it (Option B in ADR 0031):**

```bash
# After terraform plan
terraform show -json tfplan > plan.json
infracost diff --path plan.json --format json
```

Add `infracost` to `strata tools status` as an optional tool (same pattern as `terraform`
and `ansible`). Surface cost diff in `strata build plan` output. No licensing cost for
standard use — same situation as Terraform itself.

**Note:** ADR 0031 chose Option C (scenario-based model with human-readable dimensions)
over direct Infracost integration, because Option C requires zero cloud pricing
knowledge from users and is cloud-agnostic. Infracost remains a viable Option B
alternative — faster to implement but requires users to understand Terraform plan output.

### 6. Approval Workflows / Gates

|                         |                                                                                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Spacelift (approval policies), Env0 (environment policies), Scalr (run approval)                              |
| **What they do**        | Require human or automated approval before production deploys. Role-based, time-windowed, environment-scoped. |
| **Better than strata?** | Yes — strata has `--force` but no structured approval flow.                                                   |
| **Strata status**       | 🔨 ADR proposed                                                                                                |
| **Useful for strata?**  | Yes — mandatory for regulated environments                                                                    |
| **Ref**                 | [ADR 0032](0032-approval-workflows-and-gates.md)                                                              |

**How competitors do it:**

- **Web UI button** — run pauses, authorized user logs into dashboard and clicks Approve
- **Slack/Teams bot** — approval request posted to a channel, approver clicks inline button
- **PR merge as gate** — plan on PR open, apply on PR merge (Atlantis, Spacelift)
- **OPA auto-approve** — if all policies pass, no human needed; OPA flags trigger human gate
- **Time-windowed** — approvals only valid inside maintenance windows (Env0, Scalr)
- **Multi-party** — N of M approvers required (Scalr enterprise)

**How strata can do it differently — git as the approval system:**

Strata cannot run without git. Every operator is already authenticated to a git repository
before `strata deploy` is ever invoked. This means strata does not need to build an auth
system — it already has one.

A git artifact is tamper-evident cryptographic proof of approval:
- **Signed tag** — `git tag -s approve/<deploy-id> <commit>` ties an approval to a GPG
  key, a commit hash, and a timestamp. Verifiable forever via `git log --show-signature`.
- **PR merge commit** — the merge author and timestamp are permanent repo history.
  GitHub/GitLab branch protection rules enforce who can merge = who can approve.
- **Branch creation** — an approver creating `approved/<deploy-id>` from a known commit
  is attributable and auditable without any external system.

The audit trail is the git log — immutable, signed, and already satisfying most compliance
requirements without a separate database. See [ADR 0018](0018-deployment-audit-traceability.md).

**Simplest viable implementation:**
```bash
# Requester — pauses deploy, writes pending record
strata deploy run -f deploy-prd.yaml --require-approval
# → pauses, prints: approval required: strata deploy approve <id>

# Approver (different identity, different terminal/CI step)
strata deploy approve <id>
# → reads git identity, creates signed tag, resumes deploy
```

### 7. GitOps Reconciliation

|                         |                                                                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **Who does it well**    | ArgoCD, Flux, Crossplane                                                                                                                   |
| **What they do**        | Continuously reconcile desired state in git with actual cluster state. Self-healing.                                                       |
| **Better than strata?** | Different paradigm — strata is push-based (imperative deploy), GitOps is pull-based (declarative reconciliation). Not directly comparable. |
| **Strata status**       | 🔨 **In active design** — ADR 0041                                                                                                          |
| **Useful for strata?**  | Yes — being implemented as a provisioner type, not a competing approach                                                                    |
| **Ref**                 | [ADR 0041](0041-gitops-controller-integration.md)                                                                                          |

**What ADR 0041 decided — strata is the decision-maker, the controller is the executor:**

The key insight is that ArgoCD and Flux are excellent at *reconciling declared state to a
cluster* but are not designed to *decide what that state should be across a heterogeneous
fleet*. Letting the controller manage versions natively fails at fleet scale because:

- **No promotion control** — all tenants update simultaneously when a new chart appears.
  Strata's wave/ring model (ADR 0011, ADR 0037) gates version progression per cohort.
- **No per-tenant version pinning** — semver constraints apply uniformly. Strata's
  version-lock files express per-deployment pins.
- **No coordinated rollout** — the controller acts independently per Application. Strata
  orchestrates atomic wave deploys across 50–200 tenants.
- **No audit trail** — the controller tracks what is running, not who decided it. Strata's
  version-lock commits are the auditable decision record.
- **No cross-concern coordination** — version bumps require matching variable/secret/flag
  changes. Strata resolves the full context as a single atomic unit.

**The integration contract:**

```
strata decides WHAT (versions, variables, per-tenant pins, wave ordering)
    → writes resolved state to git
        → controller reconciles git state TO the cluster
            → strata verifies reconciliation succeeded
```

**No new commands needed.** ArgoCD and Flux plug in as provisioner types inside the
existing stage model:

| Existing command       | For terraform          | For argocd/flux                                      |
| ---------------------- | ---------------------- | ---------------------------------------------------- |
| `strata build run`     | Generates TF artifacts | Renders Jinja2 template → controller input file      |
| `strata deploy run`    | Runs `terraform apply` | Commits rendered output to git (triggers controller) |
| `strata deploy health` | Checks infra state     | Queries controller for reconciliation status         |

A deployment stage declares `provisioner: argocd` or `provisioner: flux` — everything
else (layers, environments, tenant scoping, partial files) works identically to Terraform
stages. See ADR 0041 for full design including namespace scoping, ApplicationSet
generation, and health verification.

### 8. Self-Service Environments (Ephemeral / TTL)

|                         |                                                                                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Env0 (TTL environments), Spacelift (preview stacks), Humanitec (on-demand envs)                               |
| **What they do**        | Spin up short-lived environments for PR previews or testing, auto-destroy after TTL or merge.                 |
| **Better than strata?** | Yes — strata has no ephemeral environment concept.                                                            |
| **Strata status**       | ❌ Not present — but primitives exist                                                                          |
| **Useful for strata?**  | Maybe — useful for dev/test workflows but adds complexity. Could be a deployment template pattern (ADR 0039). |
| **Ref**                 | [ADR 0039](0039-deployment-templates.md) (tangential)                                                         |

**How it works (all tools):**

The mechanism is just `terraform apply` on a trigger and `terraform destroy` on a timer
or event — with scaffolding around unique naming and state isolation:

```
PR opened / timer fires
    → strata deploy run    (create the environment)
    → environment lives
    → TTL expires OR PR merged/closed
    → strata deploy destroy (kill everything)
```

The hard parts are not apply/destroy — those already work in strata:

| Challenge                               | How competitors solve it                                      | Strata equivalent                       |
| --------------------------------------- | ------------------------------------------------------------- | --------------------------------------- |
| State isolation                         | Dynamic state key per env: `tf-state/pr-42/terraform.tfstate` | Terraform backend config per deployment |
| Unique naming                           | Variable injection: `var.environment_suffix = "pr-42"`        | Environment layers / variables          |
| Thin environments (app only, shared DB) | Partial stacks or environment templates                       | Deployment templates (ADR 0039)         |
| Destroy on close                        | Webhook on PR merge → `terraform destroy`                     | `strata deploy destroy`                 |
| TTL timer                               | Scheduled job fires destroy after N hours                     | ❌ **Missing piece**                     |

**The single missing piece — TTL:**

Strata has `strata deploy destroy` but nothing to *schedule* it. The gap is a `ttl:` field
on the deployment that triggers destroy automatically after a duration:

```yaml
spec:
  ttl: 8h   # strata deploy destroy fires after 8 hours
```

In the short term this can be a CI scheduled workflow. As a native feature it would need
a lightweight scheduler (a background process or a CI-generated cron job) that calls
`strata deploy destroy -f <file> --force` when the TTL elapses. No new infrastructure
concepts required — just a timer on top of existing destroy.

### 9. VCS Integration (PR comments, plan previews)

|                         |                                                                                        |
| ----------------------- | -------------------------------------------------------------------------------------- |
| **Who does it well**    | Spacelift (PR decoration), Env0 (PR plans), Atlantis (plan-on-PR)                      |
| **What they do**        | Post Terraform plan output as PR comments. Show cost diff. Gate merge on plan success. |
| **Better than strata?** | Yes — strata has the ADR but no PR decoration.                                         |
| **Strata status**       | 🔨 ADR proposed                                                                         |
| **Useful for strata?**  | Yes — high-value developer experience improvement                                      |
| **Ref**                 | [ADR 0033](0033-github-pull-request-integration.md)                                    |

**What a PR comment looks like (Spacelift / Atlantis style):**

> **🏗 strata plan** — `deploy/deploy-prd.yaml` · stage `provision`
>
> | | |
> |---|---|
> | ✅ Plan succeeded | `main` ← `feature/add-redis` |
> | 🕐 Run time | 42s |
>
> ```
> ~ azurerm_redis_cache.app    (update in-place)
>     capacity:  2 → 4
>     sku_name:  "Basic" → "Standard"
>
> + azurerm_private_endpoint.redis   (new resource)
>
> Plan: 1 to add, 1 to change, 0 to destroy.
> ```
>
> 💰 **Estimated cost change:** +$87.40/mo (`$312.00` → `$399.40`)
>
> ✅ All policies passed · ⚠️ Requires approval before apply
>
> [View full plan output](#) · [Approve deploy](#)

**How strata would generate this (CI pipeline):**

```yaml
# .github/workflows/pr-plan.yml
on: [pull_request]
jobs:
  plan:
    steps:
      - run: strata build plan -f deploy/deploy-prd.yaml --output json > plan.json
      - run: |
          COMMENT=$(cat plan.json | python scripts/format_plan_comment.py)
          gh pr comment ${{ github.event.pull_request.number }} --body "$COMMENT"
```

The `strata build plan --output json` already produces structured diff output. The
missing piece is a formatter that renders it as markdown and posts it via `gh pr comment`
(or the GitHub API). No new strata commands needed — just a thin script that reads the
JSON and calls the GitHub API. ADR 0033 proposes building this formatting + posting
natively into strata as `strata pr comment`.

### 10. State Locking / Concurrency Control

|                         |                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------ |
| **Who does it well**    | Terraform Cloud (native locking), Spacelift (run queuing), Terragrunt (state lock retries) |
| **What they do**        | Prevent concurrent applies to the same state. Queue or reject conflicting runs.            |
| **Better than strata?** | No — strata has full locking implemented and goes further than Terraform's native lock.    |
| **Strata status**       | ✅ **Implemented**                                                                          |
| **Useful for strata?**  | Already a differentiator — full pipeline lock, not just Terraform state lock               |
| **Ref**                 | [ADR 0007](0007-deployment-state-locking.md)                                               |

**Strata's locking is broader than Terraform's native lock.** Terraform only locks its
own state during `plan`/`apply`. Strata wraps the entire pipeline — lifecycle hooks,
Ansible runs, health checks, and policy evaluation — under a single distributed lock.

Implemented backends:

| Backend                                | File                               |
| -------------------------------------- | ---------------------------------- |
| Azure Blob Storage (`azurerm`)         | `lock_azurerm.py`                  |
| AWS S3 + DynamoDB (`s3`)               | `lock_s3.py`                       |
| Google Cloud Storage (`gcs`)           | `lock_gcs.py`                      |
| Terraform Cloud workspace lock (`tfc`) | `lock_tfc.py`                      |
| Consul (`consul`)                      | `lock_consul.py`                   |
| Local file (`local`)                   | `lock_local.py` — fallback for dev |

Lock backend is derived automatically from `workspace.spec.provisioners[].backend` —
no duplicate connection config. `strata deploy lock` and `strata deploy unlock` are
available as manual management commands.

### 11. Module / Component Registry (Private)

|                         |                                                                                             |
| ----------------------- | ------------------------------------------------------------------------------------------- |
| **Who does it well**    | Terraform Cloud (private registry), Spacelift (module management), Scalr (module registry)  |
| **What they do**        | Host versioned, private Terraform modules with documentation, examples, and access control. |
| **Better than strata?** | Yes — strata has the enterprise store concept but no registry UX.                           |
| **Strata status**       | 🔨 ADR proposed                                                                              |
| **Useful for strata?**  | Yes — `strata store` for sharing modules/configs across teams                               |
| **Ref**                 | [ADR 0035](0035-enterprise-store.md)                                                        |

**What users expect from a registry — and what hosting options are acceptable:**

Users come from different backgrounds and have different expectations:

| User expectation                 | What they picture                           | Is a git repo enough?               |
| -------------------------------- | ------------------------------------------- | ----------------------------------- |
| Browse what's available          | `terraform registry.terraform.io` search UX | Maybe — `strata store list` via CLI |
| Pin a version                    | `source = "acme/vpc" version = "2.1.0"`     | Yes — git tags serve as versions    |
| Private / not public             | Only my org can see it                      | Yes — any private git repo          |
| Governed / approved content      | PR review before publish                    | Yes — git branch policies           |
| Works in air-gapped env          | No internet access                          | Yes — self-hosted git (ADO, GitLab) |
| Docs + examples alongside module | README, examples/ folder                    | Yes — just files in the repo        |
| Access control per team          | Only infra team sees security policies      | Yes — repo-level permissions        |

**Is a GitHub repo OK?** Yes — for most teams a private GitHub repo IS the registry.
Strata's enterprise store (ADR 0035) is exactly this: a git repo with a `store.yaml`
manifest and a conventional folder structure. `strata repo add` is the install step.

**Is a private enterprise repo OK?** Yes — and it's the primary target:
- **GitHub Enterprise** — private repo, branch protection for governance
- **Azure DevOps** — private repo in any project, PAT or managed identity auth
- **GitLab** — private repo, deploy tokens for CI access
- **Gitea / self-hosted** — for air-gapped environments

**What a git repo cannot do (the gaps vs Terraform Cloud registry):**
- No web search/browse UI — discovery is `strata store list` or reading the README
- No automatic version indexing — versions are git tags (must be created manually)
- No built-in download stats or deprecation warnings
- No module input/output schema rendering

For most enterprise teams these gaps are acceptable — they already manage modules
via git and know what's in their repos. The Terraform Cloud registry UX matters more
for public/community modules than for internal platform content.

### 12. Change Detection (Only Deploy What Changed)

|                         |                                                                                                        |
| ----------------------- | ------------------------------------------------------------------------------------------------------ |
| **Who does it well**    | Terramate (change detection via git diff), Spacelift (affected stacks), Atmos (component filtering)    |
| **What they do**        | Detect which stacks/components changed in a commit and only plan/apply those. Saves time in monorepos. |
| **Better than strata?** | Yes — strata rebuilds everything. `strata diff show` exists but doesn't drive selective deploy.        |
| **Strata status**       | ✅ Partial (`strata diff show`)                                                                         |
| **Useful for strata?**  | Yes — critical at scale. Should drive `strata deploy run --changed-only`                               |
| **Ref**                 | None — candidate for new ADR                                                                           |

### 13. RBAC / Multi-Tenancy Access Control

|                         |                                                                                                                            |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Scalr (hierarchical RBAC), Spacelift (spaces + policies), Env0 (org/project/env scoping)                                   |
| **What they do**        | Fine-grained access: who can plan, who can apply, per environment/tenant/project.                                          |
| **Better than strata?** | Yes — strata has no access control layer (relies on git permissions).                                                      |
| **Strata status**       | ❌ Not present                                                                                                              |
| **Useful for strata?**  | Maybe — relevant for enterprise/SaaS but may be out of scope for a CLI tool. Git branch protection + CI gates may suffice. |
| **Ref**                 | None                                                                                                                       |

### 14. Real-Time Streaming / Progress UI

|                         |                                                                                    |
| ----------------------- | ---------------------------------------------------------------------------------- |
| **Who does it well**    | Spacelift (live log streaming), Terraform Cloud (run UI), Pulumi (rich CLI output) |
| **What they do**        | Stream plan/apply output in real-time with structured progress indicators.         |
| **Better than strata?** | Partially — strata has NDJSON streaming implemented but not a rich console UI.     |
| **Strata status**       | ✅ **NDJSON streaming implemented** — console progress UI not yet built             |
| **Useful for strata?**  | Already present for tooling. Console/VS Code UI is the remaining gap.              |
| **Ref**                 | [ADR 0029](0029-realtime-progress-streaming-ndjson.md)                             |

**What's already implemented:**

Strata supports `--output ndjson` on `strata deploy run`, `strata build run`, and related
commands. This streams structured events to stdout as each line is produced — not buffered
until completion:

```bash
strata deploy run -f deploy/deploy-prd.yaml --output ndjson
```

Event types emitted during a deploy:
- `stage_start` — fired when a stage begins, includes stage name + timestamp
- `log` — each subprocess output line (terraform, ansible) as it arrives
- `stage_complete` — stage outcome + duration
- `complete` — final envelope with success/failure, all messages and errors

Example stream:
```
{"event":"stage_start","stage":"provision","ts":"2026-07-15T10:00:00Z"}
{"event":"log","stage":"provision","step":"apply","stream":"stdout","text":"azurerm_resource_group.main: Creating..."}
{"event":"log","stage":"provision","step":"apply","stream":"stdout","text":"azurerm_resource_group.main: Creation complete after 2s"}
{"event":"stage_complete","stage":"provision","success":true,"ts":"2026-07-15T10:02:14Z"}
{"event":"complete","success":true,"command":"deploy.run","execution_id":"abc123","data":{...}}
```

**What's NOT yet built:**
- Rich console progress UI (spinners, stage progress bars) for human-readable `--output console`
- VS Code extension live status panel consuming the NDJSON stream
- ADR 0029 describes both — the NDJSON transport is the prerequisite and it's done

### 15. Workload Abstraction (Score / Platform Spec)

|                         |                                                                                                                              |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Humanitec (Score), Backstage (templates), Crossplane (Compositions/XRDs)                                                     |
| **What they do**        | Developers describe workloads in a simplified spec; the platform translates to actual infrastructure resources.              |
| **Better than strata?** | Different layer — strata operates at the infrastructure level, not the workload/app level.                                   |
| **Strata status**       | ❌ Not present                                                                                                                |
| **Useful for strata?**  | No — strata's sweet spot is infrastructure orchestration. Workload abstraction is a separate concern that runs above strata. |
| **Ref**                 | None                                                                                                                         |

### 16. Multi-Provisioner Orchestration (Terraform + Ansible + Helm under one roof)

|                         |                                                                                                    |
| ----------------------- | -------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Strata (native), Spacelift (Terraform + Pulumi + Ansible + CloudFormation)                         |
| **What they do**        | Orchestrate multiple IaC tools in a single deployment pipeline with shared context.                |
| **Better than strata?** | Spacelift supports more provisioners. Strata's pluggable framework is well-designed for extension. |
| **Strata status**       | ✅ Exists (Terraform + Ansible)                                                                     |
| **Useful for strata?**  | Already a differentiator — extend with more provisioners as needed (Helm, Pulumi)                  |
| **Ref**                 | [ADR 0023](0023-pluggable-provisioner-framework.md)                                                |

### 17. Server Mode / API-First Architecture

|                         |                                                                                                           |
| ----------------------- | --------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Terraform Cloud/Enterprise (REST API), Spacelift (SaaS API), Atlantis (local HTTP webhook server)         |
| **What they do**        | Run as a persistent process exposing an API — all clients (web UI, CLI, IDE, CI) talk to the same service |
| **Better than strata?** | Yes — strata is currently stateless CLI; each invocation bootstraps from scratch with no shared context   |
| **Strata status**       | ❌ Not present — but VS Code extension + MCP server make it the natural next step                          |
| **Useful for strata?**  | Yes — high value but high effort; a local daemon is the tractable first step                              |
| **Ref**                 | [ADR 0026](0026-resolved-model-cache.md) (prerequisite — cache is what a daemon would serve)              |

**The problem today:**

All three strata surfaces work the same way — each spawns a fresh CLI process:

```
VS Code extension  →  strata subprocess  →  exits
MCP server         →  strata subprocess  →  exits
User (terminal)    →  strata subprocess  →  exits
```

No shared state. No awareness between surfaces. Every invocation reloads all YAML,
re-resolves all models, reconnects to backends. For a 50-deployment fleet this is
prohibitively slow (ADR 0026).

**What a daemon would enable:**

- **Shared execution context** — extension and terminal CLI see the same running deploy
- **No per-invocation startup cost** — config and resolved models already in memory
- **Event subscriptions** — clients subscribe to the NDJSON stream instead of polling
  `strata deploy status` (the streaming transport from ADR 0029 is already built)
- **Remote execution** — run the daemon on a build server, control from local VS Code
- **Web UI** — once there is an API, a browser UI is just another client

**Progressive path (least to most effort):**

| Step                                         | What it is                                                              | Effort   | What it unlocks                                        |
| -------------------------------------------- | ----------------------------------------------------------------------- | -------- | ------------------------------------------------------ |
| **1. Resolved-model cache**                  | Cache on-disk between CLI invocations. No daemon, no API.               | Low      | Fleet commands become fast. ADR 0026.                  |
| **2. Local UNIX socket daemon**              | Persistent process, CLI/extension talk via socket. No auth, no network. | Medium   | Shared state, event subscriptions, no startup cost.    |
| **3. Local HTTP daemon** (`localhost:7420`)  | Same but HTTP. No auth required (localhost only).                       | Medium+  | Web UI possible, curl-able, language-agnostic clients. |
| **4. Remote HTTP API + fleet control plane** | Auth, TLS, multi-user, scheduler, state store, webhooks. Runs on AKS.   | Large    | Fleet ownership, drift scheduling, pipeline triggers.  |
| **5. React frontend**                        | Browser UI consuming the Step 4 API. Self-hosted or SaaS.               | Large    | Full dashboard — fleet status, approvals, cost, logs.  |
| **6. Hosted SaaS**                           | Terraform Cloud territory — multi-tenant hosted service.                | Enormous | Full platform — separate product, not a CLI feature.   |

Step 5 (React frontend) is the natural cap of Step 4 — once a REST API exists, a
browser UI is just another API consumer. It is what Spacelift and Env0 look like to
the user: a dashboard showing fleet health, running deploys, approval queues, cost
trends, and audit history. The API does the work; the React app is the face.

What the frontend would surface:
- **Fleet view** — all tenants, their deployed version, health, last deploy timestamp
- **Live deploy stream** — NDJSON events from ADR 0029 rendered as a progress UI
- **Approval queue** — pending deploys waiting for human gate (ADR 0032)
- **Cost dashboard** — per-tenant, per-environment spend trends (ADR 0031)
- **Drift alerts** — tenants whose actual state diverges from declared (ADR 0008)
- **Audit log** — who deployed what, when, from which commit (ADR 0018)

**Recommendation:** Step 1 (ADR 0026 cache) is the prerequisite — it solves the
performance problem without a daemon. Step 2 is the natural follow-on once the MCP
server and VS Code extension need shared state. Steps 4–6 are a separate product
decision, not a CLI feature — but every ADR in this document feeds into it.

**Note on the MCP server:** The MCP server is already a form of server mode — it runs
as a persistent process and accepts requests from AI agents. The gap is that it does not
share state with the CLI or the VS Code extension. A proper daemon would unify all three
under one running process.

**Note on Dev Containers:**

A local devcontainer (`.devcontainer/`) running the strata daemon is architecturally
identical to running the daemon directly on the developer's machine — it's still local,
just containerized. No meaningful difference for the server mode question.

However, devcontainers are valuable in two other ways:

1. **Toolchain distribution.** A `.devcontainer/` that pre-installs strata + terraform +
   ansible at pinned versions solves "it works on my machine" without requiring users to
   manage dependencies. Every team member gets the same environment. This is independent
   of server mode.

2. **Cloud devcontainers as a path to remote execution.** GitHub Codespaces, Gitpod, and
   Coder run devcontainers in the cloud. If the strata daemon runs inside a cloud-hosted
   devcontainer, remote team members connect via the VS Code Remote extension and share
   the same running process. This is effectively **Step 4 (remote HTTP API) without
   building a server** — the devcontainer host *is* the server, and VS Code's remote
   tunnel handles the connectivity and auth. This path is worth noting because it
   requires zero strata changes — just a `.devcontainer/` that starts the daemon.

**The fleet control plane use case — why a stateless CLI cannot do this:**

The developer daemon (Steps 1–3) solves the individual developer experience. There is a
separate, more fundamental problem at fleet scale that a stateless CLI structurally
cannot solve: **a DevOps team managing 50+ tenants across clusters needs a persistent
process that owns fleet state.**

Consider a platform engineering team with:
- Their strata repos (configuration, deployments, environments)
- An AKS cluster (or tooling VM) running their platform tooling
- CI pipelines in GitHub Actions / Azure DevOps
- 50–200 tenant deployments to track

What they need running on that infrastructure:

```
┌──────────────────────────────────────────────────┐
│  strata control plane (on AKS tooling cluster)    │
│                                                   │
│  ┌─────────────┐   ┌─────────────┐               │
│  │  REST API   │   │  Scheduler  │               │
│  │             │   │             │               │
│  │ VS Code ext │   │ drift runs  │               │
│  │ MCP server  │   │ TTL expiry  │               │
│  │ CI webhooks │   │ health polls│               │
│  └─────────────┘   └─────────────┘               │
│                                                   │
│  ┌─────────────────────────────────────────────┐ │
│  │  Fleet State Store                           │ │
│  │  "tenant X: v2.4.1, healthy, last deploy 2h"│ │
│  │  "tenant Y: v2.3.0, drifted, lock held"     │ │
│  │  "tenant Z: deploy running, stage 2/4"      │ │
│  └─────────────────────────────────────────────┘ │
│                                                   │
│  ┌─────────────────────────────────────────────┐ │
│  │  Event Bus                                   │ │
│  │  → trigger GitHub Actions pipeline           │ │
│  │  → receive webhook: pipeline complete        │ │
│  │  → start wave deployment batch               │ │
│  │  → emit NDJSON to subscribers (ADR 0029)     │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

**What the CLI cannot do that the control plane can:**

| Capability                                        | Stateless CLI                                 | Control plane                                     |
| ------------------------------------------------- | --------------------------------------------- | ------------------------------------------------- |
| "What is the current state of all 50 tenants?"    | Reads files every time — slow, no history     | Maintains live state, queryable instantly         |
| Drift detection on a schedule                     | Needs external cron + process manager         | Built-in scheduler, no external dependency        |
| React to a GitHub Actions webhook                 | Cannot — no running process to receive it     | Webhook endpoint → triggers deploy or batch       |
| Start a wave deployment, go offline, come back    | Not possible — process must stay alive        | Deploy runs in control plane regardless of client |
| "Show me which deployments are currently running" | Cannot — no shared state across CLI processes | Live dashboard — fleet state is always current    |
| TTL expiry for ephemeral envs                     | Needs external cron                           | Scheduler fires `deploy destroy` automatically    |

**This is what Spacelift, Env0, and Scalr ARE.** They are not CI wrappers — they are
fleet control planes. Their APIs, webhooks, dashboards, and schedulers are all
components of a persistent service that owns fleet state.

**For strata this is Step 5 on the progressive path** — it requires an API, a state
store, a scheduler, and webhook handling. Not a CLI feature. But the strata YAML schema,
lock backends, NDJSON streaming, and audit log are all designed in a way that would
feed naturally into a control plane implementation.

Related ADRs: [ADR 0037](0037-mass-wave-deployment.md) (batch fleet operations),
[ADR 0038](0038-multi-tenant-fleet-management-patterns.md) (fleet patterns),
[ADR 0008](0008-infrastructure-drift-detection.md) (drift — needs scheduling),
[ADR 0018](0018-deployment-audit-traceability.md) (audit — becomes the state store feed).

## Summary — Priority Assessment

| Priority | Feature                               | Status    | Rationale                                                           |
| -------- | ------------------------------------- | --------- | ------------------------------------------------------------------- |
| High     | Dependency graph / parallel execution | 🔨         | Scale blocker — manual ordering breaks above ~10 stages             |
| High     | Drift detection                       | 🔨         | Table-stakes for production governance                              |
| High     | Change detection → selective deploy   | Partial   | CI time savings compound at scale                                   |
| High     | State locking                         | ✅ Done    | Full pipeline lock across 6 backends — exceeds TF native lock       |
| Medium   | Policy engine (OPA)                   | 🔨         | Enterprise requirement, but `validate` covers some ground           |
| Medium   | Cost estimation                       | 🔨         | Developer experience — "what will this cost?" before apply          |
| Medium   | VCS / PR integration                  | 🔨         | Developer experience — plan-on-PR is expected                       |
| Medium   | Approval workflows                    | 🔨         | Compliance — regulated industries mandate it                        |
| Medium   | Progress streaming (NDJSON)           | ✅ Partial | `--output ndjson` done; console progress UI + VS Code panel pending |
| Medium   | GitOps integration (ArgoCD/Flux)      | 🔨 Active  | ADR 0041 in design — provisioner type, no new commands needed       |
| Low      | Ephemeral environments (TTL)          | Partial   | All primitives exist; only missing piece is TTL scheduler           |
| Low      | Private module registry               | 🔨         | Enterprise store ADR covers this; not urgent for small teams        |  | Low | Server mode / API daemon | ❌ | High effort; local cache (ADR 0026) is the tractable first step |  | N/A | RBAC | ❌ | Out of scope — git permissions + CI gates cover this for a CLI |
| N/A      | Workload abstraction                  | ❌         | Wrong layer — strata is infra, not app platform                     |

## Notes

- Strata's key differentiator remains: **single CLI, strict schema validation, multi-cloud
  multi-provisioner, SBOM built-in, full pipeline locking** — no competitor combines all five.
- State locking and NDJSON streaming are **implemented** and removed from the gap list.
- GitOps integration is in **active design** (ADR 0041) — not a gap, a roadmap item.
- The "High" items remaining are where competitors genuinely outperform strata today.
- Each feature row marked 🔨 already has a proposed ADR. Implementation priority should
  follow this table.
