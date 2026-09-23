# v1 Feature Priority — What haven and cfg-int-deployment Actually Depend On

- Status: partially-implemented — findings final; rebuild order not started
- Date: 2026-09-23
- Related: [ADR-0001](0001-v1-schema-analysis-findings-for-v2.md) (v1 schema
  analysis — this ADR is the runtime/CLI-usage counterpart: what v1 *code
  paths* are exercised, not what its *schema* looks like),
  [ADR-0019](0019-version-pinning.md) (same evidence method — census real
  production files/workflows rather than trusting v1's documented surface)

## Context and Problem Statement

v2 is being rebuilt from v1 field-by-field and command-by-command, but v1's
CLI surface is large (13 command groups, ~40 subcommands per
`.github/strata.instructions.md`). Building all of it before any real
consumer can cut over is the wrong order if most of that surface is never
exercised. The two production consumers of v1 —
`haven` (Hetzner Hearth/Forge platform) and `cfg-int-deployment` (Azure
integration landscape) — already have a large body of real, working CI that
shows exactly which commands, flags, config schema fields, and provisioner
behaviors are load-bearing today.

The question is not "what can v1 do" but "what would break if v2 replaced
v1 tomorrow" — that is the rebuild priority.

## Method

Read the REAL CI workflows (not just documentation) in both repos:

- `haven/.github/workflows/*.yml` (11 workflows: infra, hearth init/config/
  deploy/restore, forge init/config/deploy/maintenance/restore, ci-build)
- `cfg-int-deployment/.github/workflows/*.yml` (deploy.yml — a
  `strata new pipeline`-generated template — and deploy-spoke-z01-s01.yml,
  its one real wired instance)
- Both repos' `.strata/{cli.yaml,configuration.yaml,logging.yaml,solution.json}`
  and `.github/strata.instructions.md` (haven's AI-agent operating doc, which
  documents the full CLI surface — used to know what's *available*, cross-
  checked against what's *actually invoked*)

This mirrors ADR-0019's method: a documented feature and a used feature are
not the same thing, and only usage should drive build order.

## Findings

### Tier 1 — proven critical path (every real deploy workflow in both repos uses these)

| Feature | Evidence |
| --- | --- |
| `strata validate -f <file> --deep` | First step of every deploy/CI workflow in both repos. |
| `strata build run --file <file>` | Renders **both** Terraform and Helm artifacts (haven's Forge deploy depends on Helm rendering, its Hearth/infra workflows on Terraform). |
| `strata deploy run --file <file> --force [--dry-run] [--stage X] [--scope infra\|apps] [--verbose]` | `--scope infra` isolates haven's API-only `deploy-infra.yml` from Helm/app stages; `--stage applications_forge` targets Forge's Helm apps specifically. Terraform provisioner auto-injects resolved secrets as `TF_VAR_<KEY>` (confirmed by an inline comment in `deploy-infra.yml`). |
| `strata values get KEY1 KEY2 ... -f <file> --output json` | Used in 4 of haven's workflows to pull Infisical-resolved secrets into Ansible `extra-vars` files. Response envelope: `{success, data:{results:{KEY:val}}, errors, messages}`; non-zero exit + `errors[]` on failure (every call site checks `$?` and reads `.errors[]?`). |
| `.strata/` auto-discovery + `STRATA_OUTPUT`/`STRATA_WORK_PATH` env vars | State dir (`cli.yaml`, `configuration.yaml`, `solution.json`, `audit.log`, `cache/`, `logs/`, `integrations/`, `schemas/`, `templates/`) is committed to git in both repos; every workflow sets `STRATA_OUTPUT: json`. |
| Config schema (`kind: configuration`) fields used in production but **not modeled in v2** | `integrations` (git/terraform/infisical/azure-keyvault/azure-appconfig/elk types; `capabilities`; `required`/`enabled`; `validation.command`/`min_version`; `authentication.method` — cli/managed_identity/oauth2), `security.allowed_secret_stores`/`allowed_variable_stores`/`allowed_feature_stores`, `zones` (region groupings), `remotes` (bundled cross-repo sources), `audit` (`policy.events`, `sinks`, `journal` path/rotation, `repository.push`), `deployment.manifest`/`deployment.outputs`, `policies` (zone-isolation, path-convention enforcement), `paths` (path-convention resolution, e.g. `customers/{code}/tenant.yaml`). Confirmed absent by v2's own `ConfigurationSpecModel` docstring, which explicitly defers all of these ("ported only when the corresponding v2 kind/feature that needs them is built"). |
| Secret/value store backends actually resolved at runtime | Infisical (OAuth2 client-credentials via `INFISICAL_CLIENT_ID`/`CLIENT_SECRET`/`PROJECT_ID`), Azure Key Vault + Azure App Config (managed identity), GitHub secrets (passthrough, no strata involvement). Bitwarden appears only inside a generated pipeline **template** (`deploy.yml`'s commented-out sections) — not proven used by any real workflow. |
| Helm provisioner (Forge/k3s) | OCI `chart_repository` support; `${KEY}` secret substitution in **any** nested `env:` dict (not just one level deep — a real bug fixed in v1 1.8.2 per a workflow comment); must **not** Jinja2-render a local chart's own `templates/` dir (Go-template syntax collision, also an 1.8.2 fix). `strata deploy run` for Helm expects `KUBECONFIG` already set by the caller (haven tunnels it via SSH port-forward itself — strata does not manage the tunnel). |

### Tier 2 — documented in `strata.instructions.md` but not proven used by any real workflow

`init`, `repo add/remove/list/sync/status`, `profile add/remove/list/activate/show`,
`config set/unset/list` — local interactive bootstrap only; real CI never calls
these (relies entirely on committed `.strata/` state + auto-discovery, no
`strata init`/`repo add`/`profile activate` step appears in any workflow read).

`build plan`, `build clean`, `deploy status/history/health/destroy`,
`audit list`, `tools status/check` — operational/observability commands, not
on the critical deploy path (never invoked in CI; only documented as
available for manual troubleshooting).

Ansible provisioner support inside strata's **own** deploy pipeline — v1
documents `provisioner: ansible` with `ssh_private_key_secret`/`extra_vars`,
but haven's real Hearth workflows invoke `ansible-playbook` directly instead
of routing through `strata deploy run`. Not proven depended upon by either
consumer.

`ref env/config/data/secret` (`@repo_name/path` cross-repo file notation) —
implied by cfg-int-deployment's `spec.remotes` block (`env-int`, `iac-int`
repositories) but no direct invocation of the `ref` command group was found
in either repo's workflows.

### v2 current state

Only `validate` and `version` commands exist
([src/strata/commands/cli.py](../../src/strata/commands/cli.py)). Models/
services exist for the relevant config kinds (`ConfigurationModel`,
`DeploymentModel`, etc. under
[src/strata/models](../../src/strata/models)) but only perform Phase 1
pydantic validation — there is no build/deploy/values execution machinery at
all yet.

### Open discrepancy (unresolved, flagged for whoever builds `build run`)

Build output path differs between the two repos: haven uses `build/` at the
repo root; cfg-int-deployment uses `.strata/build/`. Likely a strata version
drift — haven pins `strata-version: "==1.9.3"` explicitly via the
`setup-strata` action everywhere; cfg-int-deployment's workflows just
`pip install xyz-strata` unpinned. Needs a decision (not just a port) once
`build run` is designed: v2 should pick one, not silently inherit whichever
version happened to produce the observed behavior.

## Decision Outcome

Build v1-equivalent functionality in this order, driven strictly by Tier 1
above:

1. **`values get`** — smallest command, no builders required; proves out the
   resolver architecture (Infisical + Azure Key Vault/App Config + constant)
   end-to-end before anything else depends on it.
2. **`validate --deep`** — Phase 2 dynamic validation wired to controllers
   (Phase 1 pydantic validation already exists for every kind).
3. **`build run`** — Terraform provisioner first (used by more of haven's
   workflows and by cfg-int-deployment), then the Helm provisioner (Forge
   only, with the two 1.8.2 substitution/rendering fixes carried forward as
   requirements, not optional polish).
4. **`deploy run`** — Terraform (`plan`/`apply`, `--scope`/`--stage`
   filtering, `TF_VAR_` secret injection) first, then Helm (`helm upgrade`
   wrapper expecting an external `KUBECONFIG`, plus the "clear stuck
   `pending-*` releases" pre-flight haven's workflow currently does by hand).
5. Everything in Tier 2 — `init`/`repo`/`profile`/`config`/`audit`/`tools`/
   `deploy status`/`history`/`health`/`destroy`/`build plan`/`clean` — as
   follow-up work once Tier 1 is done. None of it blocks either consumer's
   redeploy path.

Config schema fields required to unblock steps 3–4
(`integrations`, `security`, `zones`, `remotes`, `audit`, `deployment.manifest`/
`outputs`, `policies`, `paths`) must be ported into `ConfigurationSpecModel`
as each consuming feature is built, per that model's own existing convention
— not all at once up front.

### Consequences

- Good: rebuild effort is spent on the ~5 commands and handful of schema
  fields that both real consumers actually exercise, instead of v1's full
  documented surface.
- Good: Tier 2 commands can be built later without any risk of blocking a
  cutover, since neither consumer's CI calls them today.
- Bad: this is an inventory of two consumers only. A third future consumer
  could depend on a Tier 2 feature; this ADR does not guarantee those are
  safe to defer indefinitely, only that they are safe to defer *for now*.
- Neutral: the build-output-path discrepancy (`build/` vs `.strata/build/`)
  is left as an open question rather than resolved here — resolving it
  belongs to the ADR that designs `build run` itself.

## Remaining Work

- Nothing in Tier 1 is implemented yet beyond `validate` (Phase 1 only,
  `--deep`/Phase 2 not wired).
- `values get` — not started.
- `build run` (Terraform, then Helm) — not started.
- `deploy run` (Terraform, then Helm) — not started.
- `ConfigurationSpecModel` extensions (`integrations`, `security`, `zones`,
  `remotes`, `audit`, `deployment.manifest`/`outputs`, `policies`, `paths`) —
  not started; port incrementally alongside the command that needs each.
- Resolve the `build/` vs `.strata/build/` output-path discrepancy when
  designing `build run`.
- Tier 2 commands — not started, intentionally deferred.
