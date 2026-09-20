# strata — Changelog

Concise, user-facing summary of each release. Full implementation detail (per-phase notes,file/method names, bug-fix specifics) lives in [HISTORY.md](./HISTORY.md). Design rationale lives in the ADRs under [docs/decisions/](../docs/decisions/).

This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

## [1.11.2] - 2026-09-20

### Fixed

- **1.11.1's variable/secret severity fix was itself only partial** — an unscoped workspace still reported every undeclared *variable* and *secret* as an error; only features correctly warned. Root cause: the "who asked" set was read from `variable_refs`/`secret_refs`, which are dual-purpose (they also feed the requirements inventory) and are populated with **every** environment variable/secret regardless of whether anything referenced them — there is no equivalent unconditional collector for features, which is why that path looked correct. Now backed by a dedicated set populated only where `spec.references` is actually declared.

## [1.11.1] - 2026-09-20

### Fixed

- **An environment key a Terraform root does not declare is now a warning, not a build error (ADR-0084)** — composing an externally-authored root into a shared environment was impossible: every key the root did not consume failed the build. Terraform itself treats these as `Value for undeclared variable` warnings and plans normally (verified on 1.12.2). Severity now depends on *who asked*: a key named by a component's `spec.references` **or** by a stage's `secrets:` allowlist is still an **error** when the root cannot accept it — that is a real mismatch — while a key merely present in the environment is a **warning**. The "did you mean?" suggestion is kept on both, because a near-miss like `daily_quota_gb` against `log_workspace_daily_quota_gb` is exactly the signal worth surfacing. This changes severity only: the root still receives every value, as before.

## [1.11.0] - 2026-09-20

> **Upgrade notes — this release contains breaking changes.** It ships as a minor
> rather than a major because a fix here is needed downstream now, with further
> work already in flight; treat it as you would a major. All three breaks affect
> CI rather than local use. Read before upgrading:
>
> 1. **`strata deploy run` exits `3` instead of `1`** when a policy or AI plan
>    review denies a deployment. This one fails *quietly*: a pipeline branching on
>    `exit == 1` stops matching and falls through to whatever its default is.
>    Search your pipelines for `deploy run` exit-code checks.
> 2. **`resources[].references` is now rejected** rather than dropped with a
>    warning. Delete the key — nothing ever read it.
> 3. **`resources[].condition` is now rejected.** Replace it with `enabled:`,
>    which an environment can override per environment.
>
> Two further behaviour changes worth checking: `stages[].depends_on` now
> **reorders execution** (a file already in a valid order is unaffected; one
> declared out of order will be reordered, and a dangling, self- or circular
> reference now fails validation), and `deploy show --stage` now selects stages
> instead of being silently ignored.

### Added

- **`strata deploy run --ai` / `--strict-ai-review` now exist** — the AI plan gate that reviews a stage's plan between `plan` and `apply` was fully implemented and documented, but the two CLI flags that switch it on were never declared, so it could not be reached and was inert on every deployment. `--ai` prompts on high risk (blocks when non-interactive; `--force` overrides); `--strict-ai-review THRESHOLD` blocks outright and is **not** overridable by `--force`. Neither runs under `--dry-run`. See [commands.md](../docs/platform/commands.md#deploy-run).
- **Declarative stage gating via `stages[].enabled` (ADR-0083)** — a deployment stage can declare `enabled: ${feature:my_flag}` (or a literal `true`/`false`) and is skipped per-environment without constructing its deployer or touching its state backend. Skips cascade through `depends_on`, matching GitHub Actions' `needs:` default. A skipped stage is **recorded** as `status: skipped` with the expression and resolved value in both the deployment manifest and the deploy-log, so an audit can tell "deliberately not deployed at this version" from "deployed, no changes". Gates `deploy run` only: `deploy destroy`, `build`, and drift detection still see every stage, so turning a flag off never strands infrastructure. `--stage` cannot select a disabled stage. See [deployment.md](../docs/config/deployment.md#enabled--declarative-stage-gating).
- **`strata build plan` marks stages that `deploy run` would skip** — a plan still previews every stage (it is not gated), but a disabled one now carries `would_skip` / `skip_reason`, so "disabled in this environment" is distinguishable from "deleted from the deployment".
- **`strata validate` now redirects `condition:`/`when:`/`if:` to `enabled`** — strata accepts one spelling with no alias, so the unknown-field error teaches the word instead of listing every valid field. Fuzzy matching alone never reached `enabled` from those words.

### Changed

- **BREAKING: `strata deploy run` now exits `3`, not `1`, when a policy or AI plan review denies the deployment** — a governance decision was indistinguishable from a crashed provider, so a pipeline retrying on exit `1` would retry a deliberate refusal. `strata policies check` has always documented "`3` — one or more deny-enforcement policies failed" for the *same* policies evaluated through the *same* engine; `deploy run` was the outlier. Exit `1` still covers execution failures, **including a lifecycle hook returning non-zero** — a hook that blocks deliberately cannot be distinguished from one that crashed. Exit `4` (lock) and `5` (gate paused, resumable) are unchanged and still take priority. Update any pipeline that branches on `deploy run` exiting `1`.
- **`strata deploy show` is now a faithful preview of `deploy run`** — its stage list marks every stage a run would skip (`would_skip`/`skip_reason`), and `--stage` now selects stages like it does on every other command instead of being silently ignored. `--scope` was added for parity. Disabled stages are still listed, never filtered out. Secrets narrow to the selected stages' allowlists when either flag is given; out-of-scope secrets stay listed with `in_scope: false` and no value. See [commands.md](../docs/platform/commands.md#deploy-show).
- **`stages[].depends_on` now controls execution order (ADR-0083)** — previously it was inert at deploy time, used only to draw diagram edges. Stages now run in dependency order, with ready stages keeping their declaration order, so a deployment file already written in a valid order runs exactly as before (verified against every shipped example). A file whose stages were declared out of dependency order **will** now be reordered, and a `depends_on` naming an unknown stage, listing itself, or forming a cycle now fails validation instead of being ignored. `deploy destroy` is deliberately not reordered — teardown needs the reverse order, which strata does not derive.
- **`strata deploy destroy --stage <unknown>` now reports the same message as `deploy run`** — the two commands' `--stage`/`--scope` filters were duplicated and had drifted to different wording; both now share one implementation (ADR-0083 Phase 1). Groundwork for declarative stage gating; no behaviour change beyond the error text.
- **`deploy status`, `deploy plan`, `deploy health`, `deploy drift` and `build plan` now report the same `--stage` not-found message as `deploy run`** — seven commands hand-rolled the same filter with three different messages between them; all now share one implementation (ADR-0083 Phase 8). `deploy status` additionally drops its separate "Deployment model not loaded" variant. No behaviour change beyond the error text.

### Removed

- **BREAKING: `resources[].references` is now rejected rather than ignored (ADR-0078)** — its deprecation shim shipped the warning in v1.10.0 and has served its cycle, so the key now fails validation under `extra="forbid"`. Upgrading from v1.10.0 means you were already warned; upgrading from ≤v1.9.x skips the warning phase, so remove any `references:` key first.
- **BREAKING: `resources[].condition` and its environment override are now rejected (ADR-0083)** — inert (never parsed by any engine; its documented `'{{ environment }} == production'` syntax matched nothing in the codebase) and redundant, since an environment can already switch a resource off by overriding `enabled`. Removed **without** a warning release: the field did nothing in any version that accepted it, so no working configuration depended on it. Replace `condition:` with `enabled:`. The phantom example has also been removed from the `strata sln init` scaffold.

### Fixed

- **`build plan` now reports whether each stage would actually change anything** — plan rows carry `has_changes` (terraform's own `-detailed-exitcode` verdict; `null` when the stage failed to plan). The `build-plan` GitHub Action previously derived its `has_changes` output from row *presence*, so it was `true` on every run where terraform executed — including runs that found nothing to do, making the documented `if: has_changes == 'true'` recipe fire unconditionally.
- **`strata build plan` now fails when the plan could not run** — a stage whose `terraform init`/`validate`/`plan` errored produced a row with `error` set, but the command still exited **0**. Worse, the `build-plan` GitHub Action's `has_changes` check counts plan *rows*, not successful ones, so a wholly broken plan was reported to CI as "plan shows changes". It now exits `1`. **Finding changes remains exit `0`** — that is what the `has_changes` output is for, and PR-preview pipelines that branch on it are unaffected.
- **`strata build plan --strict-ai-review` now actually fails, with exit `3`** — it printed "Plan blocked" and exited **0**, so a CI pipeline using it as a gate reported green on exactly the change it was asked to stop. Exit `3` matches the flag's own help text and keeps a gate rejection (block the PR, never retry) distinguishable from a broken plan (exit `1`, alert). `deploy run`'s equivalent gate was unaffected.
- **`strata build plan` now reports a stage whose `enabled` expression cannot be resolved** — previously the error was discarded and the stage appeared in the plan as ordinary and unmarked, even though `deploy run` would abort on it. It is now reported as a failed row naming the unresolvable reference.
- **`CheckovPolicy` artifact path resolution (ADR-0051)** — no longer silently passes a `deny`-enforcement policy without scanning anything; now resolves each terraform provisioner's build directory via the canonical `get_provisioner_path()`, adds a `scope` config (`staged` default | `all` | `<stage-name>`) for multi-provisioner workspaces, and surfaces every skip as a warning instead of a silent pass. See ADR-0051 / HISTORY.md.
- **Stage → provisioner resolution is now strict everywhere** — a typo'd `stage.provisioner`/`stage.topology` no longer silently falls back to a different provisioner (previously only logged a warning); it now fails `validate_workspace()` outright. May surface previously-silent stage/provisioner config errors. See ADR-0051 / HISTORY.md.
- **`CheckovPolicy` now also scans Bicep and Ansible provisioners** (`configuration.framework: bicep|ansible`), not just Terraform. One policy still scans one framework — declare a `checkov` policy per framework for multi-framework coverage. See ADR-0051 / HISTORY.md.
- **Helm's build-time secret check is now scoped to the deploying stage** — a module referencing a secret that's registered in the environment but excluded from the deploying stage's `secrets:` allowlist is now flagged at build time with a distinct message, instead of only failing later at real deploy time with a message indistinguishable from "never registered". See ADR-0051 / HISTORY.md.
- **`CheckovPolicy` now also scans Helm charts** (`configuration.framework: helm`) — local charts only (registry-pulled charts are skipped with an explicit warning, since there's no local source to scan). Findings are reported per `namespace/module`. See ADR-0051 / HISTORY.md.

## [1.10.0] - 2026-09-15

### Added

- **Machine-to-machine (M2M) bearer token verification (ADR-0067 Step 10)** — `GET /v1/whoami` verifies a caller's bearer token against one or more configured `--m2m-trusted-issuer` OIDC issuers (GitHub Actions, Azure DevOps, a Client-Credentials IdP, ...) and returns the verified claims, structurally separate from the admin token and human OIDC login.
- **`docker-compose.server.yml` and a `charts/strata-server` Helm chart** for running the state-service (ADR-0065) with a bundled single-pod PostgreSQL backend — self-contained quickstart/small-team deployments, not a production HA topology. Point `db.existingSecret`/`postgresql.enabled: false` at a managed database instead for production.
- **`spec.references` on a workspace provisioner (Terraform, script, etc.) now opts that provisioner into scoped Terraform build validation (ADR-0078)** — new `ProvisionerReferencesModel`, same `{variables, secrets, features}` shape as the existing per-kind `references` on resource/module/dns/provider. When any resource, module, or provider in the workspace declares `spec.references`, every one of them must (a build error otherwise), and the Terraform input cross-check is scoped to the union of their declared keys instead of every key declared anywhere in the environment — the workaround of declaring a dummy, unread `variable {}` block purely to satisfy the validator is no longer needed for keys consumed by a different provisioner. Workspaces that declare no `references` anywhere are completely unaffected — this is opt-in. Deploy-time injection scoping (which values a running deploy actually receives) is a deliberately separate, not-yet-implemented follow-up; see the ADR.

### Removed

- **The cross-resource `references` field on workspace resource entries and environment resource overrides has been removed (ADR-0078)** — it was declared but never implemented: no resolver, no validation, no use in any shipped configuration. It could not have worked, since workspace resources are build-time metadata while outputs are post-apply and keyed by provisioner, not resource. A `references:` key is now dropped with a deprecation warning rather than failing validation; the shim goes away next minor. Use `inputs_from` for cross-provisioner values, or native Terraform references within a root module. ADR-0068 records the removed schema and what a revival would require.

### Changed

- **BREAKING (targeting 2.0.0): Terraform provisioners now bind to a `configuration.spec.integrations[]` entry via an explicit, optional `integration:` field instead of an incidental name coincidence.** Previously, `TerraformDeployer` looked up the integration by the workspace provisioner's own `name` — undocumented, and it silently broke for anyone using semantically-named provisioners (e.g. `control_infra`, `core_iac`) instead of naming them `terraform`. Now: set `integration: <name>` to bind explicitly, or leave it unset to auto-bind to the sole registered Terraform-compatible integration (an error, not a guess, if zero or more than one exist). `strata validate --deep` resolves this ahead of time so a missing/ambiguous binding is caught before `deploy run`, not partway through it. Workspaces with exactly one `type: terraform` integration (the common case, and every example shipped in `config/`) need no changes. `OpenTofuIntegration` continues to satisfy `provisioner: terraform` unchanged. See ADR-0079.
- **BREAKING (targeting 2.0.0): declaring a `type: ansible`/`azure_cli`/`docker`/`helm` integration in `configuration.spec.integrations` now actually has an effect, for the first time.** `AnsibleDeployer`, `BicepDeployer`, `ComposeDeployer`, and `HelmDeployer` each built a throwaway, hardcoded integration inline and never consulted `configuration.spec.integrations` at all — any integration declared for these types was silently dead YAML. Ansible and Bicep are provisioner-scoped (like Terraform) and gain the same optional `integration:` field with identical auto-bind/explicit-name semantics — with one difference: zero declared integrations is **not** an error for these two (falls back to today's bare default, so existing workspaces are unaffected). Compose and Helm aren't provisioner-scoped (a stage can deploy many namespaces/charts) — they auto-bind the same way but have no `integration:` field to target explicitly. Ambiguity (2+ compatible integrations registered, no way to disambiguate) is a real, new limitation for Compose/Helm — reduce to one registered integration of that type if hit. See ADR-0080.

### Fixed

- **`strata push`/audit git-forwarding failed with "You are not currently on a branch" from a detached-HEAD checkout** (e.g. a CI runner pinned to a specific ref) — `GitIntegration.push()` now always sends a `HEAD:<branch>` refspec when a target branch is given, which pushes the currently checked-out commit regardless of local branch state, instead of relying on a local branch of that exact name existing.
- **`strata build run` warned `Required variable 'X' (no default) is not supplied by any input` on every build for required Terraform variables supplied via `spec.properties`/`spec.custom`** — a permanent false positive. The check only knew about `spec.variables`/`features`/`secrets`, but properties and custom blocks are emitted as their own `properties.auto.tfvars.json`/`custom.auto.tfvars.json` files and are genuine inputs. Their top-level keys are now recognised, honouring the provisioner's `output:` profile so the warning still fires when the corresponding file isn't emitted.

## [1.9.11] - 2026-09-11

### Fixed

- **`@repo/...` references resolved differently depending on which directory `strata` was run from, silently ignoring an explicitly-supplied `--work-path`** — `SolutionController.get_repo_map()` resolved `type: local` repositories against the process working directory (`os.getcwd()`) while resolving `type: gitops` repositories against the workspace root. Running any command from a subdirectory therefore resolved `@repo/...` to `<work_path>/<subdir>/<ref>` instead of `<work_path>/<ref>`, failing with a diagnostic that blamed the profile's config references rather than the resolution — even when `--work-path` was passed correctly. Both repository types now resolve against the workspace root, which is the only stable base for workspace-scoped state. The same `os.getcwd()` bug in `generate_workspace()` (which made generated `.code-workspace` folder paths depend on where `strata repo add` was run) is fixed identically. See ADR-0077.
- **`strata build plan` (including `--artifacts-only`) failed with `Terraform source directory not found` for any workspace whose Terraform provisioner uses a non-local `source.repository`** — even though the exact same deployment built fine via `strata build run`. `PlanBuildCommand._build_to_temp()` called `TerraformBuilder.build()` without passing `repo_map` at all, so every provisioner source silently resolved relative to the workspace root instead of the registered repository path. Fixed to compute and pass `repo_map` the same way `strata build run` already does. Audited every other builder call site in the codebase for the same omission — no others found.

## [1.9.10] - 2026-09-11

### Added

- **`strata values get` gained a `--format` option (`table`/`raw`/`env`/`export`)** for direct shell/script consumption — `raw` prints the bare value (single key only), `env` prints `KEY=value` lines, `export` prints shell-quoted `export KEY='value'` lines ready for `eval`. Mutually exclusive with `--output`. Previously the only way to extract a single value into a script was piping `--output json` through `jq`.

## [1.9.9] - 2026-09-10

### Fixed

- **`strata build run` silently dropped `properties.auto.tfvars.json`/`variables.auto.tfvars.json`/`flags.auto.tfvars.json` for any Terraform provisioner without an explicit `output:` block** — despite `OutputProfileModel`'s own docs saying it "defaults to format: strata when absent", an absent profile (`None`) was treated as "emit nothing beyond workspace/providers/topologies" instead of the documented default (emit everything). A deployment could pass `validate --deep` and `build run` with no errors while `environment_info`/flat variables/feature flags silently never reached Terraform at all. Fixed so an absent `output:` block now behaves exactly like `format: strata`, matching the documented default.
- **`strata build run` wrote a spurious `ansible/` build folder (with `strata_workspace.yml`/`strata_providers.yml`/`strata_topologies.yml`) for workspaces with zero `ansible` provisioners** — e.g. a Terraform-only workspace. `AnsibleBuilder` never checked whether the workspace actually declared an `ansible` provisioner before generating and writing its variable files; it now skips Ansible artifact generation entirely when none is declared.
- **Same bug as above, in `TerraformBuilder`**: a workspace with zero `terraform` provisioners (e.g. Bicep/Compose/Helm-only) got a spurious `terraform/` build folder with tfvars output. Audited every builder (`Bicep`, `Compose`, `Helm`, `Sync`) for the same "generate output regardless of whether the matching provisioner/module exists" pattern — only `Terraform` and `Ansible` were affected; both now skip entirely when the workspace declares none of their respective provisioner type.

## [1.9.8] - 2026-09-10

### Fixed

- **`strata build run` dropped `role`/`count` for topology components backed by a `managed_by: provisioner` resource** — `PlatformBuilder` only built its resource → role/count lookup maps when at least one full resource *service* existed in the workspace, so a topology component referencing a provisioner-managed resource (no backing resource service) silently fell back to `role: null`/`count: 1` in the generated platform artifact. The maps are now built unconditionally from `spec.resources`, independent of whether any resource services exist.

## [1.9.7] - 2026-09-10

### Added

- **CI now builds and publishes the `strata-server` Docker image (ADR-0065)** — `Dockerfile.server` previously had no automation at all. It now gets the same `edge`/PR-preview/release publishing as the CLI and docs images: an `edge` tag pushed to GHCR (and Docker Hub, if configured) on every merge to `main`, a `pr-<N>` preview image on GHCR for pull requests (cleaned up on close), and versioned tags pushed to GHCR + Docker Hub on release.

### Fixed

- **`Dockerfile.server` failed to build in CI with `apt-get install ... exit code: 100`** — both build stages used the floating `python:3.13-slim` tag, which drifted to a newer Debian codename than the hardcoded `packages-microsoft-prod.deb` Debian-12 (bookworm) repo used to install `msodbcsql18`, breaking dependency resolution. Both stages now pin `python:3.13-slim-bookworm` explicitly.
- **`strata audit status` reported `integration_declared: true` even when a sink's integration had a nonexistent/misspelled `type`** — the check only verified the sink's `integration:` name matched *some* `spec.integrations[]` entry, never that its `type` actually resolves to a registered integration (`IntegrationModel.type` is a free-form string, not an enum, so a typo passed validation silently). Now also checks `IntegrationFactory.is_known_type()`; a new `integration_type` field on each reported sink shows the resolved type (or `null` if the name wasn't found) for diagnosis.
- **`strata versions lock`/`strata versions refresh` silently deleted any comments in the version-manifest file** — both rewrote the file via plain `yaml.safe_load()`/`yaml.dump()`, which don't preserve comments. Both now use a comment-preserving round-trip YAML reader/writer (new `ruamel.yaml` dependency), so hand-authored comments survive.

## [1.9.6] - 2026-09-08

### Fixed

- **`strata build run` never resolved `spec.extends` (ADR-0039)**, unlike `deploy run` and `validate --deep` — a leaf deployment file that passed `validate --deep` still failed `build run` with "Workspace not found in deployment" because inherited fields (`workspace`, `stages`, ...) were never merged in. Fixed via a shared resolver helper now used by both build and deploy; `build run` also rejects partial deployments the same way `deploy run` already did, and this failure (and others like it) is now properly surfaced in `get_validation_errors()` instead of only appearing in the stderr log.
- **`RemoteModel.deploy_path` was optional but effectively mandatory for gitops remotes** — an unset value silently dropped the remote from `@remote/...` resolution and made repo-fetch checks look for the wrong local path. Now required for `type: gitops`, failing loud at config-validation time instead of at deploy time with a misleading error.

## [1.9.5] - 2026-09-08

### Added

- **Deployment change-reference tracking (ADR-0074)** — `deploy run`/`deploy destroy` accept `--change-id`/`--reason`/`--change-system`/`--change-title`/`--change-url`/`--change-classification` (or `STRATA_CHANGE_*` env vars), recorded on the deployment manifest/log; a new `change_reference_required` policy can enforce it before any deploy or destroy.

### Fixed

- **Terraform backend config could silently deploy with an unresolved `${var:}`/`${secret:}` reference, and Helm value substitution only matched inside `env:`-keyed dicts with an exact whole-value match** — both provisioners now share one typed `${var:}`/`${secret:}`/`${feature:}` resolver (ADR-0075) that fails loud on any unresolved reference and works anywhere in the document, plus a matching build-time check. **Breaking:** removes Helm's old untyped `${KEY}` syntax.

## [1.9.4] - 2026-09-07

### Fixed

- **Every CLI invocation crashed on Windows** (`SystemError: ConsoleRenderer with colors=True ... requires colorama`) — `colorama` wasn't declared as a dependency. Added it (Windows-only); the console formatter also now falls back to uncolored output instead of crashing if it's still missing.
- **Partial deployments (`spec.partial: true`) never checked whether their `environments[]` file references existed**, since full Phase 2 validation is intentionally skipped for them — dangling references went undetected until a leaf deployment extending the partial file was built/deployed. A lightweight existence-only check now runs for partial files under `--deep` validation.
- **Layer segment values could capture a deployment file's own filename as a directory segment** when the file sat exactly one level shallower than its convention's segment depth — segment value extraction now strips the filename first. See ADR-0072.

## [1.9.3] - 2026-09-04

### Fixed

- **Bicep provisioners had no builder-side copy step**, so `strata build run` produced nothing for them and `deploy run` always failed — new `BicepBuilder` copies `.bicep` source like every other provisioner. See ADR-0071.
- **`source.reference` (git-ref pinning) was silently ignored by Ansible and Helm's local-chart copy** — both now honor a pinned ref via a shared `BaseBuilder` helper.
- **`WorkspaceIacModel.backend`/`.output` validated on non-Terraform provisioners despite having zero effect** — now rejected at validation time.
- **`argocd`/`flux` sync provisioners wrongly required a `source:` block** — an enum/string comparison bug always evaluated the "not required for sync" check false. Fixed.
- **`strata deploy lock status`/`release`/`history` could act on the wrong Terraform provisioner's lock** in multi-provisioner workspaces — removed a duplicate, non-stage-aware resolver; all lock subcommands now match `deploy run`'s stage-aware resolution.

### Changed

- **Terraform/Ansible/Bicep/Helm/Compose/Sync now resolve build output paths through one canonical `SolutionController` helper** instead of each independently re-deriving the shape (ADR-0071). No user-facing behavior change.

## [1.9.2] - 2026-09-02

### Fixed

- **`environment.spec.properties`/`custom`/`overrides.properties` were shallow-merged across environment files, silently dropping nested sibling keys on override** — now uses a shared `deep_merge()` (new `strata.utils.dict_merge`), consistent with the Terraform tfvars merge which already did this correctly.
- **Resource/module `configuration`/`custom` overrides had the same shallow-merge bug**, despite comments claiming "Deep merge" — fixed to use the same shared `deep_merge()`.

## [1.9.1] - 2026-09-02

### Changed

- **BREAKING: `configuration.spec.layering`/`spec.layerings` removed, merged into `spec.paths`; `deployment.spec.layers` reshaped to `{follows, segments}`** — replaces two inconsistent path-matching engines (plain `fnmatch` vs. a genuinely segment-aware matcher) with one convention model. No backward-compatible fallback — models use `extra: forbid`, so an unmigrated config fails fast with an "unknown field" error. See [configuration.md](../docs/config/configuration.md) and ADR-0072.

### Added

- **`spec.custom` and dict-aware `validate:` rules** — `validate:` rules can now reach into freeform `spec.configuration`/`spec.properties`/`spec.custom` dicts, which previously validated nothing silently.
- **`strata deploy run --namespace NAME` (repeatable)** scopes the helm provisioner to specific namespace(s) for a single run, via a new `stages[].helm_namespaces` allowlist. See [deployment.md](../docs/config/deployment.md#namespace-vs-helm_namespaces--kubernetes-namespace-scoping).

## [1.8.3] - 2026-08-27

### Fixed

- **`strata deploy run` always failed with `ServiceNotValidatedError: Service 'EnvironmentService' must be validated before use` (regression since v1.7.0)** — `RunDeployCommand` had a stale no-op `_load_related_services()` override (`return True`, loading nothing) left over from before that method became an overridable hook for lightweight, environment-only commands (`deploy show`, `values get`/`list`/`resolve`). Once the base class started calling that hook instead of loading services directly, `deploy run`'s override silently skipped the entire workspace + environment load — `strata validate --deep` and `strata build run` on the same file both succeeded, masking the bug until `deploy run` crashed immediately on every invocation. Removed the incorrect override so `deploy run` inherits the base class's real, full-load implementation, matching its pre-v1.7.0 behavior.

## [1.8.2] - 2026-08-25

### Fixed

- **Terraform input validation flagged secrets belonging to a different provisioner as errors** — `strata build run`'s `variables.tf` cross-check (`TerraformBuilder._collect_declared_input_keys()`, added in v1.7.0) swept every secret declared anywhere in `environment.yaml` into every Terraform provisioner's "declared inputs" set, with no scoping by stage or provisioner. In any workspace intentionally sharing one `environment.yaml` across multiple provisioner types (Terraform for infra + Ansible/Compose/Helm for app secrets — a standard pattern given strata's own stage-level `secrets:` allowlist design), this unconditionally failed the build the moment a single non-Terraform secret existed in the shared file. Declared secrets are now scoped to the stage(s) that actually resolve to that Terraform provisioner (by `stage.provisioner` name, `stage.topology` → `topology.provisioner`, or the sole workspace provisioner), mirroring `ResolvedValues.for_stage()`'s identical `stage.secrets` allowlist already used at deploy time. Variables and features are unaffected (never stage-scoped at deploy time, so they remain a global check). Workspaces with no stages at all, or where a provisioner isn't referenced by any stage, keep the previous unscoped behavior (no regression for setups not yet using multi-provisioner stage scoping).
- **Local Helm charts with standard Go-template syntax crashed the entire build** — `strata build run` copies a local chart's source directory (`spec.source.repository`/`source_path`, no `chart_repository`) into the build output, then Jinja2-renders every text file in it for strata's own `${STRATA_*}`/`variables.*`/`features.*` substitution. Since that substitution pass is effectively always active once a deployment is loaded, any chart whose `templates/` directory used real Helm Go-template syntax (e.g. `{{ .Release.Name }}`) failed with `TemplateSyntaxError: unexpected '.'`, aborting the build — making the documented local-chart feature unusable for any real-world chart. `templates/` (Helm's own template scope, rendered by Helm itself at deploy time) is now always excluded from strata's templating pass; as defense in depth, any other file that isn't valid Jinja2 is now skipped with a warning instead of crashing the build.

### Added

- **Helm `${TOKEN}` secret substitution now works at any nesting depth, not just `entry.env.KEY`** — `HelmDeployer`'s `${KEY}` → `--set-string` substitution previously only matched a top-level entry's `env` sub-dict (strata's own `svc.environment`-generated shape), so any real-world chart with chart-mandated deep nesting (e.g. Immich's `controllers.main.containers.main.env.DB_PASSWORD`) or a flat `{env: {...}, image: {...}}` shape had no way to opt into token substitution at all. `_find_env_tokens()` now walks the whole values document looking for any dict node keyed literally `env` (dict-shaped: `env: {KEY: value}`), at any depth — still scoped to `env`-keyed dicts specifically (not an unrestricted tree walk) to avoid matching a user-typed `${...}`-shaped string in unrelated pass-through configuration. The Kubernetes-native list shape (`env: [{name: KEY, value: value}]`) remains unsupported.

## [1.8.1] - 2026-08-25

### Fixed

- Corrected `VERSION.txt`, which had been bumped to the invalid `1.8.0-alpha` prerelease suffix in the Kroki integration commit — setuptools' dynamic-version PEP 440 normalization turns this into `1.8.0a0` (no hyphen) at install time, which fails `strata`'s own strict-semver self-checks (`tests/strata/commands/test_version.py`). No prior release in this repo's history had ever used a prerelease suffix; reverted to the established plain `X.Y.Z` convention. No functional changes.

## [1.8.0] - 2026-08-25

### Added

- **`strata diagram show --format svg|png` via Kroki (ADR-0034)** — new `kroki` integration (`diagram_render` capability) renders Mermaid diagrams to real SVG/PNG image files. Zero-config by default (public `https://kroki.io`, no account/API key/CLI install needed); self-hostable via `STRATA_KROKI_ADDRESS` or a declared `type: kroki` integration with a custom `endpoints.address`. `--format` is a new flag distinct from `--output` (which still controls the console/json/text response envelope). See `strata help --topic kroki`.
- **4 new built-in diagrams completing the non-flowchart cookbook set (ADR-0034)** — `drift-summary` (`pie`), `gate-sequence` (`sequence`), `environment-complexity` (`quadrant`), and `secret-store-flow` (`sankey`) join `timeline` (`gantt`) as the first worked example of each non-flowchart Mermaid type. All hand-written `spec.template` (these types aren't sugar-generatable) and honestly scoped to data the existing `drift`/`approvals`/`environments`/`secrets` sources already expose — no new source types were added.
- **VS Code Diagram Builder + `/diagram create` chat generation (ADR-0034 Phase 4)** — `strata diagram show --output json` now always includes the parsed `sources`/`layout`/`style` (and a `has_template` flag) alongside the rendered output, enabling the VS Code extension's new visual Diagram Builder to round-trip a sugar-based definition without re-parsing YAML client-side. See the VS Code extension changelog for the Builder itself.
- **8 new built-in diagrams (ADR-0034 Phase 3)** — `strata diagram show -f <name>` now ships `stages`, `promotion`, `network`, `services`, `environments`, `secrets`, `timeline`, and `architecture` alongside the existing `refs`/`topology`, covering the ADR's full "Top 10" built-in list. Most are defined with zero Jinja — just `spec.sources` + `spec.layout`/`spec.style` — the generator sugar's first real end-to-end use in a shipped built-in. `timeline` is the first shipped `gantt`-type diagram (milestones from deploy history, since the audit trail has no per-stage duration).
- **`strata validate --deep` catches broken `strata://` links in hand-authored diagrams (ADR-0034)** — a `kind: diagram` with a hand-written `spec.template` containing a `click <id> "strata://..."` line now has that link checked against the workspace; a renamed/removed target is reported as a validation error instead of only being discovered by clicking a dead node in the VS Code diagram preview. Generated diagrams (no `spec.template`, built from `spec.layout`/`spec.style`) are unaffected — their URIs are always freshly computed at render time.

### Fixed

- **`strata diagram show -f refs` (and `-f topology`) resolved file references relative to the referencing file's directory instead of the workspace root** — `GraphController` joined `spec.workspace.file`, resource/module/namespace/network/firewall/dns `file:` references against `source_file.parent` rather than `work_path`, doubling the path prefix for any reference nested below the workspace root (e.g. `config/config/stack/workspace.yaml`) and marking every such node `:::missing`. File references in strata YAML are always workspace-root-relative, matching `BaseService._resolve_file_path()`. See ADR-0015.
- **Required-integration validation could false-fail commands unrelated to the integration's capability (ADR-0069)** — `strata values get` and similar commands previously failed on a `required: true` `terraform` integration they never needed (they only need secret/variable/feature stores). `IntegrationService.validate_required_integrations()` now accepts an optional capability filter, and each command scopes validation to only the capabilities it actually needs — fixes the false failure without weakening validation for commands (`build`/`deploy`) that do need the provisioner.

## [1.7.0] - 2026-08-12

### Breaking Changes

- **Cost estimation now requires an `infracost` integration declaration** — `strata cost show`, `strata cost diff`, and the automatic post-plan cost diff in `deploy run --dry-run` previously worked off of any `infracost` binary found on PATH, regardless of `configuration.yaml`. They now require an explicit `infracost` entry under `spec.integrations` (with `capabilities: [cost]`, `enabled: true`), matching how every other integration (secret stores, provisioners) is gated. An installed binary with no declaration no longer does anything. `strata cost history` is unaffected — it reads past `cost show` snapshots and needs no estimator.

### Added

- **CLI login for a control plane or any OIDC service (ADR-0067)** — new `identity` integration capability with six first-class providers (`azure_ad`, `google`, `aws_identity_center`, `auth0`, `github_oauth`, `generic_oidc`), declared under `spec.integrations` like any other integration. No dedicated `strata login` command — login triggers lazily and is checked/driven via `strata sln doctor --deep --login`. When a control-plane session is active, its authenticated identity outranks the CLI-local `actor` resolution chain from ADR-0066. See ADR-0067 and `strata help --topic identity`.
- **Provisioner-managed resources** — workspace resources can now declare `managed_by: provisioner` to indicate the provisioner (Terraform/Ansible) fully owns the resource definition. No resource file is required. This simplifies multi-tenant IaC deployments where all resource details (VMs, databases, networks) are defined entirely in Terraform modules. The resource declaration still participates in topology wiring, but strata skips spec validation and file loading.
- **Git ref pinning on `SourceModel` (ADR-0063, Gap 1)** — Provisioner sources now accept an optional `reference` field (branch, tag, or commit SHA) that overrides the workspace-level remote default. This allows two provisioners referencing the same remote to pin different versions (e.g., platform baseline on `v1.4.0` and team module on `main`). Resolution priority: `source.reference` → environment remote override → remote default. When a ref is pinned, `git archive` extracts the subtree without mutating the working tree.
- **Structured variable types (ADR-0063, Gap 2)** — `VariableStoreModel` now accepts an optional `type` field (`string`, `number`, `bool`, `object`, `list`, `map`) that declares the intended HCL type. When set, strata validates that the YAML value matches the declared type and emits it as a native JSON type in `.auto.tfvars.json` instead of always stringifying. Complex values can now be authored as native YAML mappings/sequences instead of JSON-in-string.
- **Terraform input validation (ADR-0063, Gap 3)** — `strata build run` now cross-checks declared variable/feature/secret keys from environment YAML against the module's `variables.tf` declarations. Undeclared inputs (typos) are errors that block the build with fuzzy-match suggestions. Unsupplied required variables (no default) are reported as warnings. Eliminates a class of silent deployment failures where misspelled variable names were silently dropped by Terraform.
- **Helm values validation (ADR-0063, Gap 3)** — For local Helm charts, `strata build run` now cross-checks `module.spec.configuration` keys against the chart's default `values.yaml`. Typos in top-level and one-level-deep keys are reported with fuzzy-match suggestions. Warnings only (does not block build). Registry charts are skipped (not available at build time).
- **Output passing between provisioners (ADR-0063, Gap 4)** — Provisioners now accept an `inputs_from` field that declares explicit dependencies on other provisioners' outputs. Supports `mapping` (key rename), `prefix` (add prefix to all keys), and `select` (allowlist) modes. Validated at schema level: unknown provisioner references, self-references, and circular dependencies are rejected. Mapped keys are treated as "supplied" by Gap 3 input validation. The existing `stage_outputs` injection mechanism applies the mapping at deploy time.
- **Combined deployment outputs artifact (ADR-0063, Gap 5)** — After a successful `deploy run`, strata now writes a `deployment-outputs.json` file that merges all stages' Terraform outputs into a single registry-consumable document. Outputs are keyed by stage name; sensitive output keys are listed but values omitted. Includes deployment metadata (name, version, workspace, environment, tenant) and provenance (completed stages). The artifact is the contract surface for service registry integration.

---

## [1.6.1] - 2026-08-03

### Added

- **SQLite-backed resolved-model cache — `strata cache` (ADR-0026)** — deployments' resolved model is now cached keyed by a hash of its source YAML files, so `build run`/`build plan`/`policy check` don't need to re-resolve unchanged deployments. New `strata cache warm/status/clear/export` commands; the affected build/policy commands auto-warm on success (`--no-cache-warm` to opt out). The VS Code extension warms the cache in the background on save and startup. See ADR-0026.

### Fixed

- **`deploy run`/`deploy destroy` exit codes for invalid deployments** — a deployment file that fails schema/cross-reference validation now correctly exits 3 (was 1); a missing/unresolvable deployment file now exits 2. See ADR-0004.

### Security

- **Secret/variable/feature store outages no longer conflated with "not found"** — integrations (Infisical, HashiCorp Vault/OpenBao, Bitwarden, Azure Key Vault) now raise a new `SecretStoreUnavailableError` on connectivity/auth failures instead of returning `None`. Previously a transient outage could be mistaken for a missing secret, triggering silent auto-generation of a fresh value (for `generate:` secrets) or letting a deploy proceed with a blank value. `deploy run` now aborts instead of continuing when a store is unavailable.
- **Pre-flight availability checks for secret stores and provisioners** — `deploy run` now verifies all referenced secret/variable/feature stores and required provisioner tools (terraform, ansible, etc.) are reachable before resolving any values or acquiring the deployment lock, failing fast instead of partway through a multi-stage deploy.

---

## [1.6.0] - 2026-07-31

### Breaking Changes

- **`env` command group removed (ADR-0062)** — its six commands are redistributed: `env show`/`env output`/`env drift` merge into the existing `deploy` equivalents, `deploy status` is revived with corrected live-state behavior, `env status --all/--path` becomes the new `rollout status` group, and `env info`/`env doctor` move to `sln status`/`sln doctor`. No deprecation shim — update any `strata env ...` usage in scripts, pipelines, or MCP tool calls.
- **Unified `spec.gates` schema (ADR-0059)** — `spec.approvals`/`approvers` and the separate ADR-0057 `spec.gates` block are merged into a single deployment-level `spec.gates` list with typed approver refs (`github-team`/`user`/`ado-group`). Existing YAML using either old shape fails validation; see ADR-0059 for the field-by-field migration mapping.

### Fixed

- **`ref_convention` policy / `strata repo status` design drift (ADR-0017)** — tag naming conventions now live in one place, `spec.remotes[].conventions` (`RemoteConventionsModel`), instead of being duplicated inside the policy's own config. `repo status` no longer guesses release/quality tags from hardcoded name prefixes — it links a local repo to its configured remote by comparing normalized git remote URLs and only classifies tags when that remote declares `conventions`. No backward compatibility with the old policy-level `configuration.remotes[]` shape (never released/documented before this fix).

### Changed

- Subprocess execution consolidated onto a single `run_command()` path with consistent SIGTERM handling and timeout parity across builders, deployers, and controllers (ADR-0061).

## [1.5.0] - 2026-07-27

### Added

- Deployment workflow orchestration — approval/cost/security gates, work-item lifecycle (`strata workitem`), `deploy run --resume`, exit code 5 for hand-off. See ADR-0057.
- Comprehensive help documentation for all 12 platform YAML kinds; `docs/help/` is now the single source of truth, synced to the CLI and VS Code extension at build time.
- AI agent integration — advisory LLM analysis across build/deploy/validate/policy/audit commands via `--ai`, plus VS Code chat participant commands (`/review`, `/diagnose`, `/sbom`). See ADR-0025.

## [1.4.0] - 2026-07-24

### Added

- `--timeout` for `deploy run`/`deploy destroy` (ADR-0027) and SIGTERM graceful shutdown (ADR-0028).
- Cloud CLI integrations + lifecycle scripts for GCP (ADR-0055), AWS, and Azure.
- Scoped multi-scheme layering (ADR-0042), path convention validation (ADR-0052), Checkov IaC security scanning (ADR-0051), Bicep provisioner (ADR-0046), Azure CLI integration (ADR-0053), OPA policy integration (ADR-0050).
- UTC datetime standardization across all timestamps (ADR-0045).

### Changed

- Deployment layers no longer require the final layer to be named `"environment"`; `spec.layering` deprecated in favor of `spec.layerings`.

### Breaking Changes

- Removed hardcoded final-layer-name constraint — review configs that relied on it.

## [1.3.1] — 2026-07-22

### Added

- Cost estimation via Infracost — `strata cost show/diff/history`, `cost_threshold` policy (ADR-0031 Phase 1).
- VS Code extension reworked around deployment-centric UX — Deployments/Operations views replace the flat Files view.

### Changed

- Removed unused provider `engine` and resource `unit_cost` fields.

## [1.2.1] — 2026-07-20

### Changed

- `strata new --output-file` (was `--path`); `strata validate run --pattern` (was `--path`).
- Exit code 4 for deployment lock conflicts.

### Fixed

- `strata secret mask` positional-argument dash handling; Sphinx docs build warnings.

## [1.2.0] — 2026-07-16

### Added

- GitOps controller integration — `argocd`/`flux` provisioner types with reconciliation health checks (ADR-0041).
- Platform artifact convenience fields — name, labels, revision, resolved_variables, chart/image versions.

### Changed

- `WorkspaceIacModel.source` is now optional for sync-type provisioners.

## [1.1.1] — 2026-07-15

### Added

- `strata new --validate` — validates generated files immediately after creation.

### Changed

- Command lifecycle migrated to `_execute()` across the entire command layer; `execute()` sealed on `BaseCommand` (ADR-0030).

## [1.1.0] — 2026-07-14

### Added

- Environment provider overrides — per-environment provider file/configuration swaps (ADR-0036).
- Promotion strategy framework groundwork (ADR-0011); tenant scaffolding bundle template (`strata new tenant`).

## [1.0.1] — 2026-07-09

### Added

- Tenant `spec.environments`/`spec.properties`/`spec.custom` now applied at build time.
- `sln deployment` subcommand group (`add`/`remove`/`list`/`scan`).

### Fixed

- Tenant `spec.environments` was validated but never merged into the build pipeline.

## [1.0.0] — 2026-07-08

### Added

- S3 distributed lock backend (ADR-0007).
- VS Code extension reached CLI feature parity — Values Inspector, lock status/release, drift detection, SBOM generation, stage-targeted deploy, repository write operations.
- Umbrella JSON schema (`strata.json`) — a single schema entry replaces 12 per-kind entries.

### Changed

- Unified exception handling and structured logging across all commands.

## [0.16.1] — 2026-07-06

### Fixed

- `uv sync --group doc` dependency group resolution.

## [0.16.0] — 2026-07-05

### Added

- Environment composition — flat merge now covers all 8 spec sections with provenance tracking (`values list --trace`) (ADR-0024).

## [0.15.0] — 2026-07-03

### Added

- Configurable Terraform build output profiles (ADR-0019).
- `strata env status` (renamed from `env state`); VS Code Environments and Audit Trail panels.

## [0.14.0] — 2026-06-26

### Added

- Deployment audit and traceability — SIEM sinks for Sentinel, ELK, and OpenTelemetry (ADR-0018).

## [0.13.0] — 2026-06-24

### Added

- Guided onboarding experience — `strata console` REPL, `validate graph`/`--explain`, batch validation (ADR-0014).

## [0.12.0] — 2026-06-23

### Added

- Auto-generated secrets and seed-on-missing for variables/features (ADR-0013).

## [0.11.0] — 2026-06-23

### Added

- Promotion strategies system — waves, progressions, `strata promote` (ADR-0011).

### Changed — Breaking

- Renamed `customer` → `tenant` throughout the platform (ADR-0012). See migration guide in [HISTORY.md](./HISTORY.md).

## [0.10.0] — 2026-06-22

### Added

- `strata deploy show`/`list`, bundle templates for `strata new`, cross-manifest overlap validation (`validate --path`), remote reference overrides per environment.

## [0.9.3] — 2026-06-20

### Added

- Helm module build/deploy improvements — chart coordinates in `meta.yaml`; `--wait`/`--atomic` on `helm upgrade`.

## [0.9.2] — 2026-06-19

### Fixed

- Ansible builder no longer writes empty variable files for unused sections.

## [0.9.1] — 2026-06-19

### Changed

- SBOM collector warnings are silenced by default (use `--verbose` to see them).

## [0.9.0] — 2026-06-18

### Breaking Changes

- Configuration YAML: `spec.repositories` renamed to `spec.remotes` (ADR-0010).

### Added

- Self-SBOM generation in CI; SBOM attached to every GitHub Release.

## [0.8.2] — 2026-06-17

### Fixed

- CI docs workflow dependency group and tag-trigger condition.

## [0.8.1] — 2026-06-17

### Added

- GitHub Pages CI workflow for documentation.

### Fixed

- Topology volume `type` field restriction removed.

## [0.8.0] — 2026-06-17

### Added

- CVE audit (`strata build sbom --audit`) via Trivy/Grype; `sbom_license` policy; 6 new lockfile parsers (NuGet, Maven, Gradle, Gemfile, Cargo, Composer); `.strata/lockfile_parsers/` auto-discovery.

## [0.7.0] — 2026-06-16

### Added

- `strata policy check` standalone command; `strata deploy outputs`; deploy-phase policy hook.

## [0.6.0] — 2026-06-15

### Added

- Policy engine — declarative guardrails at validate/build/plan phases (`customer_zone`, `required_tags`, `naming_pattern`, `script` policy types); `strata policy list`.

## [0.5.0] — 2026-06-12

### Added

- SBOM generation (CycloneDX 1.6) — `strata build sbom`; container image, Helm, Terraform provider, and Ansible collection collectors.

## [0.2.0] — 2026-06-09

### Added

- `strata env status`/`drift`, `strata values set`/`resolve`, environment module overrides (image/chart pinning per environment).

## [0.1.1] — 2026-06-07

### Added

- Cross-module `depends_on` (`@module/service` syntax); `strata output`.

## [0.1.0] — 2026-05-14

_First real release._

- Layered architecture, Pydantic v2 models for all YAML kinds, Click CLI, exit codes 0-3, structured logging.
- Core commands: `validate`, `build`, `deploy`, `diff`, `sln`, `repo`, `profile`, `config`, `vars`, `log`, `tools`, `schema`.
- Terraform subprocess integration; secret resolution (Bitwarden, Azure Key Vault, HashiCorp Vault, env vars).

---

<!--
To release a new version:
- Move entries from [Unreleased] into a new ## [x.y.z] — YYYY-MM-DD section.
- Update VERSION.txt to match.
- Tag: git tag vx.y.z && git push origin vx.y.z
- Keep entries to 1-3 lines per feature, referencing the ADR for detail.
  Full narrative detail goes in HISTORY.md only if truly needed for archaeology.
-->

## Legend

- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** in case of vulnerabilities
- **Infrastructure** for CI/CD and build changes
- **Documentation** for doc-only changes
