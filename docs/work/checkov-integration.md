# Checkov Integration (`policies: checkov`) — v1 Capability Catalog and v2 Gap

- Status: **All 3 phases shipped (2026-10-07)** — models
  (`checkov_model.py`), `CheckovIntegration`, the `build_controller.py`
  step-loop prerequisite, `WorkspaceSpecModel.checkov_policy`, and
  `checkov_controller.py`'s inline evaluator (terraform/bicep/ansible,
  plus Helm's own namespace/module resolution path) are all built and
  tested, wired into `build run` end to end. **The `on_missing_data`
  granularity/configurability question is provisionally decided
  (2026-10-07) — see "v2 design decision" below.**
- Last updated: 2026-10-07
- Related: [policy-engine-architecture.md](policy-engine-architecture.md)
  (umbrella catalog — `checkov` is bucket C: needs a real external scanner,
  not derivable from strata's own document graph or a Terraform plan),
  [cve-scanner-integration.md](../archive/cve-scanner-integration.md) (sibling
  bucket-C concern and the framework doc for scanner-type integrations —
  `ScannerIntegration` capability, shared open questions all three face;
  also now contains the Trivy/Grype per-backend detail, merged back in
  from their own split-out docs 2026-10-07),
  [sbom-generation.md](sbom-generation.md) (sibling bucket-C
  concern, same "real external tool, not a strata-internal check" shape)

## Overview

`checkov` is one of v1's 19 real built-in policy types (`policies:
type: checkov`) — it wraps the actual [Checkov](https://www.checkov.io/)
CLI to scan rendered IaC artifacts (Terraform, Bicep, Ansible, Helm charts)
for security misconfigurations, gated by a configurable severity threshold.
v2 has no equivalent today — no `integrations/checkov.py`, no `PolicyModel`
for any type to attach to, nothing. This doc catalogs v1's real
implementation (read directly from `e:\SourcesXYZ\strata\src\strata\
integrations\checkov.py` and `validators\policies\checkov_policy.py`) as a
sizing/design reference for when this is picked up.

## v1: real implementation

### The integration (`integrations/checkov.py`)

Wraps the real Checkov CLI as a subprocess: `checkov --directory <path>
--output json [--framework ...] [--check ...] [--skip-check ...]
[--compact]`. Declared like any other v1 integration:

```yaml
integrations:
  - name: checkov
    type: checkov
    capabilities: [iac_security]
    required: false
    validation:
      command: checkov --version
      min_version: "2.0.0"
```

Parses Checkov's JSON output into two dataclasses:

- `CheckovFinding` — `check_id`, `check_name`, `resource` (e.g.
  `aws_s3_bucket.example`), `file_path`, `file_line_range`, `severity`
  (`CRITICAL|HIGH|MEDIUM|LOW|UNKNOWN`), `guideline` (remediation URL).
- `CheckovScanResult` — `passed`/`failed`/`skipped` counts, `findings:
  list[CheckovFinding]`, `scanner_version`, `framework`, `scanned_path`,
  plus `findings_at_or_above(severity)` (used directly by the policy's
  severity-gate check).

### The policy (`validators/policies/checkov_policy.py`)

Evaluates at the `build` phase. Configuration:

```yaml
policies:
  - name: terraform_security_baseline
    type: checkov
    phase: build
    enforcement: deny
    configuration:
      framework: terraform          # default: terraform | bicep | ansible | helm
      severity_gate: high           # critical|high|medium|low (default: high)
      scope: staged                 # staged (default) | all | <stage-name>
      skip_checks: [CKV_AWS_1, CKV_AWS_20]
      include_checks: []            # if set, run ONLY these checks
      custom_checks_dir: ".strata/checkov/custom/"
      timeout: 120                  # seconds, default 120
```

**`framework` selects both the Checkov framework AND which strata
provisioner type is scanned**, via `_FRAMEWORK_PROVISIONER_MAP`:

| `framework`           | provisioner scanned              | glob used to confirm artifacts exist |
| --------------------- | -------------------------------- | ------------------------------------ |
| `terraform` (default) | `provisioner: terraform`         | `*.tf`                               |
| `bicep`               | `provisioner: bicep`             | `*.bicep`                            |
| `ansible`             | `provisioner: ansible`           | `*.yml`/`*.yaml`                     |
| `helm`                | resolved differently — see below | n/a                                  |

For `terraform`/`bicep`/`ansible`, the artifact directory per provisioner
is resolved via `SolutionController.get_provisioner_path()` — **the same
single source of truth the matching builder (copy destination) and
deployer (working directory) already use**, honouring each provisioner's
`source.target_path`/`source.source_path`. The policy's own docstring
flags that an earlier revision guessed at flat candidate directories under
`context.build_path` that never consulted `source_path`, silently skipping
(and reporting `passed=True`) for any workspace whose provisioner used a
nested path — a real bug, fixed by routing through
`get_provisioner_path()` instead of re-deriving the path a second way.
**This is the one porting trap worth flagging up front for a v2 build**:
whatever v2's own equivalent of `get_provisioner_path()` ends up being
(build-command design, not yet built per `v1-consumer-usage.md`'s rebuild
order), the Checkov integration must resolve paths through it, not
re-implement path derivation independently.

`helm` has no single directory per provisioner (Helm build output is
organized per namespace+module), so it's resolved separately: `scope`
selects target namespace(s) via `helm_namespaces_for_stage()`, each
namespace's modules are enumerated via the already-loaded
`NamespaceService`, filtered to `spec.type == helm`. Only **local** charts
(no `chart_repository`) have source on disk to scan — a registry-pulled
chart's module is skipped with an explicit warning (only
`values.yaml`/`meta.yaml` exist for those at build time, never a
`Chart.yaml`). Findings for Helm are reported per `namespace/module`
instead of per provisioner name.

`compose`/`argocd`/`flux`/`script` remain unsupported in v1 itself:
`compose` has no Checkov framework at all; `argocd`/`flux` render from the
platform artifact with no stable build-time source directory; `script` is
not IaC.

**`configuration.scope`** controls which provisioner(s)/namespace(s) of the
selected framework are scanned when a workspace declares more than one:

- `staged` (default) — only provisioners reachable from at least one
  deployment stage (a shared module-library provisioner no stage targets
  is excluded by construction, no extra metadata needed).
- `all` — every provisioner of the selected framework, staged or not.
- `<stage-name>` — provisioner(s) reachable from that one named stage only
  (a static filter, not a runtime "when that stage deploys" binding —
  `build` evaluates once regardless of deploy stages).

When more than one provisioner/namespace is scanned, a breach in **any
one** denies the whole policy result (AND semantics) — findings are still
reported individually in `PolicyResult.details["provisioners"]`, and
violation strings are prefixed with the provisioner/namespace label (e.g.
`[control_infra] CKV_AWS_1: ...` or `[prod/nginx] CKV_K8S_1: ...`).

### Graceful degradation (never a silent pass)

Every skip condition appends an explicit, actionable message to
`PolicyResult.warnings` — surfaced even when `passed=True`, so "nothing was
enforced" is never indistinguishable from "scanned and clean":

- `severity_gate`/`scope`/`framework` invalid → pass (skip), warning
  explains which field and why.
- No IaC artifacts found for the selected scope → pass (skip).
- Checkov not installed → pass, **skip that one provisioner only** (other
  provisioners in the same policy still get scanned).
- Scan subprocess fails → pass, skip that one provisioner only.

**Note what v1 does NOT have, confirmed by re-reading its own real
`checkov_policy.py` config surface above**: there is no `on_missing_data`
field anywhere in Checkov's real v1 config (contrast with
[cve-scanner-integration.md](../archive/cve-scanner-integration.md)'s own
`on_missing_data: skip|warn|block`, a real, documented ADR-0082 field).
v1's Checkov degradation is hardcoded to "always skip, always warn" —
never configurable to block. This is the real shape the decision below
has to reconcile against, not an assumption.

### v2 design decision (provisional — decided ahead of implementation, 2026-10-07)

Per [cve-scanner-integration.md](../archive/cve-scanner-integration.md)'s own
Remaining Work item #3 ("decide explicitly when Checkov is picked up, not
by default"): made here as a **provisional**, pre-implementation design
call, not validated against real code yet — the user explicitly asked for
this to be decided now, in the abstract, rather than waiting for the
actual build. Revisit/confirm once Phase 1 of this integration is
actually built and tested.

**The question has two genuinely separable parts, not one — worth stating
precisely before answering either:**

1. **Granularity** — does a missing-data condition affect the whole
   policy result, or just the one provisioner/namespace it was found on?
2. **Configurability** — is the response to missing data
   (skip/warn/block) a config knob at all, or hardcoded?

**Part 1 — Decision: keep v1's real per-provisioner granularity, do NOT
normalize to a single whole-policy switch.** v1's own behavior here is
better, not just different, and worth preserving deliberately: Checkov
(via `scope: staged|all|<stage-name>`) can legitimately scan **N
independent provisioners/namespaces** in one policy evaluation — CVE never
has more than one `sbom.json` to scan, so its own `on_missing_data` is
necessarily a whole-policy scalar by construction, not a considered
design choice to *not* be per-unit. Collapsing Checkov to a single
whole-policy switch would be a real regression versus v1: one
misconfigured or unscannable provisioner (e.g. a registry-pulled Helm
chart with no local source, explicitly already skipped-with-warning
above) would block coverage of every *other*, perfectly scannable
provisioner in the same policy — exactly the "don't let one bad apple
spoil the batch" case v1's own docstring (already quoted above: "skip
that one provisioner only (other provisioners in the same policy still
get scanned)") was written to prevent. Normalizing away a deliberately
better, already-tested v1 behavior for the sake of superficial
consistency with CVE would be the wrong trade.

**Part 2 — Decision: ADD a real `on_missing_data: skip|warn|block`
field to v2's `checkov_policy`, unlike v1 — matching CVE's exact field
name and vocabulary, applied per-provisioner (per Part 1).** This is a
deliberate *improvement* over v1, not a straight port, and the
justification is the same one [cve-scanner-integration.md](../archive/cve-scanner-integration.md)'s
own "Graceful degradation" section already established and chose to
preserve/harden for CVE specifically: *"a security-flavored guardrail...
'never actually scanned' should not look the same as 'scanned and
clean'."* That reasoning is not CVE-specific —
Checkov is equally a security guardrail (CRITICAL/HIGH misconfiguration
findings), and v1's hardcoded "always skip, always warn" means an
operator can never configure a Checkov policy to actually *fail* a build
when, say, the `checkov` binary silently isn't installed on a CI runner —
the exact same silent-pass risk CVE's own docstring already flagged, and
v2 already decided to put a real, configurable knob on for CVE. Concretely:

- `skip` — no finding recorded for that provisioner at all (matches v1's
  only real behavior today).
- `warn` — a warning scoped to that one provisioner; the policy's overall
  `passed` is unaffected by it alone.
- `block` (recommended default, matching `cve_policy`'s own
  rationale/default) — an error scoped to that one provisioner. Note this
  does **not** stop *other* provisioners from still being scanned and
  reported (Part 1's own granularity decision) — but since any error
  anywhere fails `Diagnostics.ok`, a `block`-triggered missing-data
  condition on even one provisioner still fails the overall build, the
  same end result CVE's own `on_missing_data: block` produces for its one
  scan target.

**Net effect — one shared, consistent operator-facing vocabulary
(`on_missing_data: skip|warn|block`, same field name, same three values,
same `block` default) across every scanner-type policy, while each
policy's own internal evaluation granularity stays whatever its real
data shape demands** (one scalar check for CVE's one SBOM; N independent
per-provisioner checks for Checkov's N scannable targets). An operator who
already understands `cve_policy.on_missing_data` does not have to learn a
second concept for `checkov_policy` — only that it is evaluated once per
scanned provisioner instead of once per policy, a natural and discoverable
consequence of Checkov's own `scope` field already being plural, not a
new idea to teach.

## v2: current state

No `integrations/checkov.py`, no `PolicyModel`/`CheckovPolicyModel` for any
type to attach to — confirmed via `list_dir` on
`src/strata/{models,integrations}`.

**The one hard prerequisite this doc originally flagged — resolved, with
one real correction (2026-10-07 — see Open Question 2 below for the full
detail).** It read "no `build run` command exists yet either... no
`get_provisioner_path()`-equivalent to resolve paths through." `build run`
now exists (`build_controller.build_run()`, shipped) and already resolves
exactly this: each execution step computes `source_path = sync_source(
context.root, build_path, provisioner.source, remotes)` (the real,
already-materialised on-disk directory for that provisioner, honouring
`source.target_path`/`source.source_path` the same way the matching
provisioner's own render step does), and every dependency-only provisioner
(reachable only via another provisioner's `depends_on`, never its own
execution step) is resolved the same way into a `materialised: dict[str,
Path]` map inside `materialise_provisioner_sources()`. **Correction: these
are not "two dicts to assemble" — the step loop's own `source_path` is a
local variable, never persisted anywhere, and is already out of scope by
the time a Checkov evaluator would run (end of `build_run()`, matching
`evaluate_cve_policy()`'s own placement).** A future Checkov evaluator
needs one small, real addition to the step loop itself — writing each
step's `source_path` into a shared dict too — not just assembly of
already-existing data. The `helm` framework's own separate resolution
(namespace/module enumeration) is unaffected by this — still needs
`NamespaceService` enumeration as originally scoped.

## Sizing

Smaller than `tenant_zone`'s full plan-time tier (no Terraform-plan
parsing, no resource-level location heuristic) but has its own real
complexity, concentrated in path resolution rather than evaluation logic:

1. **`integrations/checkov.py` port** — subprocess wrapper + JSON-output
   parsing into `CheckovFinding`/`CheckovScanResult`. Mechanical, no v1
   design decisions to revisit.
2. **A `CheckovPolicy` (or v2-equivalent model)** — the framework→
   provisioner-type mapping, `scope` resolution (`staged`/`all`/
   `<stage-name>`), severity-gate comparison, **plus a new
   `on_missing_data: skip|warn|block` field v1 never had** (see "v2
   design decision" above — provisional, decided 2026-10-07). Previously
   blocked on `build run`'s provisioner-path resolution existing — **no
   longer blocked**, see "v2: current state" above. **Also needs the
   small step-loop change found in Open Question 2** (2026-10-07):
   persisting each execution step's own `source_path` into a shared dict
   alongside `materialise_provisioner_sources()`'s existing `materialised`
   map — not a Checkov-only concern, but a real, small `build_controller.py`
   change this integration's evaluator depends on, not purely additive
   like every other scanner-type policy so far.
3. **Helm's separate resolution path** — namespace/module enumeration via
   `NamespaceService`, local-vs-registry chart distinction. Can be deferred
   to a second phase (terraform/bicep/ansible first, matching
   `sbom-generation.md`'s own "ship a small real slice first" precedent).
4. **The `PolicyModel` question — resolved, not open.**
   [policy-engine-architecture.md](policy-engine-architecture.md)'s own
   Open Question 1 is decided (2026-10-07): bespoke, indefinitely — no
   generic `PolicyModel`/engine planned. `checkov` gets its own dedicated
   `WorkspaceSpecModel.checkov_policy` field + inline evaluator, matching
   `path_convention`/`tenant_zone`/`cve_policy`'s precedent exactly —
   nothing left to decide here when this is picked up.

## Implementation Plan

Phased, one focused change at a time, full check suite (`mypy src`,
`ruff check src/ tests/`, `ruff format src/ tests/`, `lint-imports`,
`pytest -q`) clean before moving to the next phase — same discipline as
every other feature in this repo. **All 3 phases shipped 2026-10-07**
(full check suite + 2302 tests passing at that point).

### Phase 1 — Models + `CheckovIntegration` (no policy wiring yet) — **DONE (2026-10-07)**

- New `src/strata/models/checkov_model.py` — a dedicated module, not
  folded into `sbom_model.py` the way CVE's models were: Checkov never
  touches an SBOM, it scans rendered IaC directly, so the SBOM-specific
  grouping that justified CVE's placement doesn't apply here.
  - `CheckovFindingModel`: `check_id`, `check_name`, `resource`,
    `file_path`, `file_line_range`, `severity`
    (`CRITICAL|HIGH|MEDIUM|LOW|UNKNOWN`), `guideline` — ported
    field-for-field from v1's real `CheckovFinding` dataclass.
  - `CheckovScanResultModel`: `passed`/`failed`/`skipped` counts,
    `findings: list[CheckovFindingModel]`, `scanner_version`,
    `framework`, `scanned_path`. `findings_at_or_above(severity)` ported
    as a real method (used directly by the evaluator's severity-gate
    check, matching v1's own usage), not re-derived ad hoc at each call
    site.
  - `CheckovPolicyModel`: `framework` (default `terraform`),
    `severity_gate` (default `high`), `scope` (default `staged`),
    `skip_checks: list[str] | None`, `include_checks: list[str] | None`,
    `custom_checks_dir: str | None`, `timeout: int` (default `120`),
    `on_missing_data: Literal["skip", "warn", "block"]` (default
    `"block"` — the new field v1 never had, decided above).
- New `src/strata/integrations/checkov.py` — `CheckovIntegration(
  ScannerIntegration)`. `CAPABILITIES = frozenset({Capability.SCANNER})`,
  never configured via an `IntegrationModel` document, matching
  `CveScannerIntegration`'s own "always built-in" precedent (the
  `"checkov"` string in `registry.py`'s known-type list is a
  placeholder today, not a real dispatch target). `ensure_available()`
  checks `shutil.which("checkov")`, actionable "install checkov
  (https://www.checkov.io/)" message when missing. `scan(directory:
  Path, *, framework: str, skip_checks: list[str] | None,
  include_checks: list[str] | None, custom_checks_dir: str | None,
  timeout: int) -> CheckovScanResultModel` — deliberately its own
  signature, not `CveScannerIntegration.scan_sbom()`'s shape (the
  `ScannerIntegration` ABC's own docstring already states why: IaC
  directory in vs. SBOM file in are genuinely different inputs).
  Subprocess: `checkov --directory <path> --output json [--framework
  ...] [--check ...] [--skip-check ...] [--compact]`, JSON parsed into
  `CheckovScanResultModel`, wrapped in a `_safe_parse()`-style guard
  (matching `cve_scanner.py`'s own fix for malformed/unexpected-shape
  JSON — a real bug class already found and fixed once in the CVE
  sibling, not repeated here).
- **`build_controller.py` step-loop change (Open Question 2's own
  finding — a real prerequisite, not purely additive) — implemented
  differently than originally planned, during actual coding:** rather
  than adding a second, parallel `provisioner_source_paths` dict that
  would sit unread for an entire phase (and trip `ruff`'s unused-variable
  check), the main step loop now writes its own `source_path` straight
  into the **existing** `materialised` dict (already shared across the
  whole `build_run()` invocation and already read/written throughout) —
  keyed by `provisioner.name`, same as `materialise_provisioner_sources()`'s
  own dependency-only entries. `materialised` is now the one unified
  mapping regardless of how a provisioner was reached, exactly the
  outcome this prerequisite needed, just without a second data structure.
- **Tests** (`tests/strata/integrations/test_integrations_checkov.py`,
  matching `test_integrations_cve_scanner.py`'s established stubbed-
  `run_command` convention): class-contract assertions;
  `ensure_available()`'s message when `checkov` isn't installed;
  `scan()` against fixture Checkov JSON output (passed/failed/skipped
  counts, finding field mapping, missing-optional-field degradation,
  `--check`/`--skip-check`/`--external-checks-dir` argv construction);
  malformed/unexpected-shape JSON degrades to a clean `IntegrationError`;
  process failure raises with the real stderr; `get_version()` failure
  degrades to `"unknown"` rather than raising. New
  `tests/strata/models/test_models_checkov.py` for the three new models
  (defaults, `findings_at_or_above()` severity filtering including an
  unrecognised-severity-treated-as-UNKNOWN case). New
  `tests/strata/controllers/test_build_controller.py` cases confirming
  the real `materialised` dict (captured via monkeypatching
  `materialise_provisioner_sources()`) now contains every execution-step
  provisioner's own path too, including one reached via both
  `depends_on` and its own execution step (the already-documented
  double-sync case — same path landed in the dict either way).

### Phase 2 — Config field + inline evaluator (terraform/bicep/ansible only, Helm deferred to Phase 3) — **DONE (2026-10-07)**

- `WorkspaceSpecModel.checkov_policy: CheckovPolicyModel | None` —
  same placement reasoning as `cve_policy` (`WorkspaceSpecModel` already
  owns `execution`/`provisioners`, the build-phase scope this policy
  belongs to). `CheckovPolicyModel` also gained a real `enforcement:
  deny | warn` field during this phase — a correction, not an addition:
  v1's own YAML example always showed `enforcement:` alongside
  `configuration:` (re-read directly), and Phase 1's model had missed
  it entirely.
- New `src/strata/controllers/checkov_controller.py` (not
  `sbom_controller.py` — no SBOM relationship exists here, same
  reasoning as Phase 1's model placement):
  `evaluate_checkov_policy(build_path: Path, workspace: WorkspaceModel,
  provisioner_source_paths: dict[str, Path | None]) -> Diagnostics`.
  For each provisioner matching `checkov_policy.framework` (via
  `_FRAMEWORK_TOOLS`, reusing `utils.builtin_types.TERRAFORM_COMPATIBLE_TYPES`
  directly rather than re-declaring `terraform`/`opentofu` — `bicep`→
  `tool: bicep`, `ansible`→`tool: ansible`) selected by `scope`, looks up
  its real path in `provisioner_source_paths`, scans it, and applies the
  severity-gate check. A breach on any one provisioner denies the whole
  policy (AND semantics, matching v1), but every provisioner is still
  scanned and reported independently — per-provisioner
  `on_missing_data` governs degradation for that one provisioner only
  (Part 1/Part 2 of the "v2 design decision" above), never the whole
  policy.
  **`scope` is build-time-native, re-interpreted during implementation —
  not v1's deployment-stage concept, and not what this plan originally
  assumed:** a direct read of `build_run()`'s own step loop confirmed it
  iterates every `workspace.spec.execution` step unconditionally,
  regardless of `DeploymentModel.spec.stages` (a deploy-time-only
  runtime-knobs concept — timeouts/health_checks/enabled — never
  consulted by `build run`). v1's "staged" (reachable from a deployment
  stage) has no build-time equivalent in v2, so `scope` was re-scoped to
  something that *is* build-time-native: `"staged"` (default) selects
  provisioners that have their own execution step; `"all"` selects
  every declared provisioner of the matching framework, staged or
  dependency-only; a bare execution-step name selects the one
  provisioner that step runs. This keeps the evaluator's signature
  exactly as planned (`workspace` + `provisioner_source_paths`, no
  `DeploymentModel` needed) rather than requiring a wider, unplanned
  signature change.
- `_checkov_policy_result()` in `build_controller.py` (mirroring
  `_cve_policy_result()` exactly): builds one `ManifestPolicyResultModel`
  entry (`policy_name="checkov_policy"`, `policy_type="checkov"`,
  `phase="build"`) from `evaluate_checkov_policy()`'s own unmerged
  `Diagnostics`, written via the already-generic
  `write_policy_results()` — no changes needed to
  `strata/controllers/policy_results.py` at all, it already accepts any
  `ManifestPolicyResultModel` list.
- Call-site wiring in `build_run()`: same capture-then-extend pattern
  `evaluate_cve_policy()`'s own call site already established, reading
  provisioner paths from the same `materialised` dict Phase 1 extended.
  **Also fixed a real, pre-existing fragility found while wiring this
  in**: the CVE-only call site wrote `policy_results.json` via its own
  independent `write_policy_results()` call — since that function
  overwrites the whole file every call (confirmed by reading its real
  implementation), a second, independent call for Checkov would have
  silently discarded the CVE result (or vice versa) the moment both
  policies were ever configured at once. Both results are now collected
  into one list and written together in a single call.
- **Tests**: `tests/strata/controllers/test_checkov_controller.py` (16
  tests) — no-op when unset; severity-gate pass/breach;
  `enforcement: deny` vs. `warn`; unsupported framework (`helm`)
  degrades via `on_missing_data`; `scope: staged` vs. `all` vs. a named
  step each select the right provisioner set; `tool` filtering
  (including `opentofu` matching `framework: terraform`); zero
  provisioners matching scope degrades via `on_missing_data`; each of
  `on_missing_data: skip|warn|block` for an unmaterialised provisioner
  and for a scan failure; the critical regression case — one
  unscannable provisioner does not block a scannable sibling in the
  same policy. Plus `test_build_controller.py` additions (4 tests):
  `policy_results.json` written on breach/warn/unset, and — the other
  critical regression case — both `cve_policy` and `checkov_policy`
  results land together in one `policy_results.json` when both are
  configured at once.

### Phase 3 — Helm's separate resolution path — **DONE (2026-10-07)**

- No `helm_namespaces_for_stage()`-equivalent exists, and none was
  built — the same Phase 2 finding applies here too: `build_run()`
  never consults `DeploymentModel.spec.stages` (confirmed again by
  reading `workload_controller.py`'s own module docstring, which
  independently states the workload pipeline is "a second, disconnected
  input shape", never a `ProvisioningStepModel` — so it was never going
  to share Phase 2's `"staged"` resolution mechanism either). A
  workspace's namespaces have no "has its own execution step"-equivalent
  distinction at all — a namespace is either referenced by the workspace
  or it isn't. So for `framework: helm`, **`"staged"` and `"all"` are
  deliberately equivalent** (both mean every namespace the workspace
  references via `ResolvedWorkspaceGraph.namespaces`) — only a bare
  namespace name genuinely narrows the set, to that one namespace's
  modules.
- `_evaluate_helm()` in `checkov_controller.py`: for each namespace in
  scope, enumerates `NamespaceSpecModel.modules` directly (no
  `NamespaceService` needed — `resolve_module()`, already established by
  `workload_controller.py`, is reused as-is), skips a disabled reference
  (`enabled: false`) and any non-`helm`-typed module entirely (silent,
  not a missing-data condition — same as v1 only ever enumerating
  `spec.type == helm` modules). A registry-pulled chart
  (`SourceModel.chart_name` set) degrades via `on_missing_data` with
  "registry-pulled chart has no local source to scan" — confirmed by
  reading `source_sync.sync_module_source()`'s own real behavior
  directly: it only ever writes an empty `module_dir` for a chart-based
  source (`values.yaml`/`meta.yaml` land there later, via
  `HelmIntegration`, never the chart's own templates). A local chart's
  real directory is `build_path/<namespace>/<module reference name>` —
  the exact same convention `workload_controller.build_workload_modules()`
  already establishes and documents, recomputed independently here
  rather than threading a new path dict through two already-disconnected
  pipelines (the provisioner loop and the workload pipeline were already
  established as "a second, disconnected input shape" before this
  phase — recomputing a well-documented, stable path convention was the
  smaller, less coupling-prone choice).
- Findings reported per `namespace '<name>' module '<name>'`, not per
  provisioner name, matching v1's own distinct violation-prefix format
  for this one framework.
- `evaluate_checkov_policy()`'s own signature gained two new optional
  keyword-only parameters for this — `namespaces: dict[str,
  NamespaceModel] | None` and `index: DocumentIndex | None` — both
  `None` by default (every Phase 2 call/test keeps working unchanged),
  populated by `build_run()`'s own call site from `graph.namespaces`/
  `index`, already in scope there.
- **Tests** (`test_checkov_controller.py`, 12): a local chart scans
  clean and can breach; a registry-pulled chart degrades via
  `on_missing_data` (block/warn); a namespace whose modules are *all*
  disabled, or *all* a non-helm type, degrades via `on_missing_data`
  at the whole-policy level (block/warn/skip — see the code-review
  finding below; an individual disabled/non-helm module within an
  otherwise-scannable namespace is still silently skipped, same as
  before); an unmaterialised local chart directory degrades via
  `on_missing_data`; namespaces/index both unset degrades at the
  whole-policy level; `scope: staged`/`scope: all` produce identical
  results; `scope: <namespace-name>` narrows correctly; an unknown
  namespace name degrades via `on_missing_data`. Plus one new
  `test_build_controller.py` end-to-end test reusing the existing
  `test_build_run_renders_helm_workload_modules()` fixture shape,
  confirming the evaluator scans the exact same directory the workload
  pipeline really materialised (not a separate, possibly-divergent
  path) and that a breach fails the build and lands in
  `policy_results.json`.

### Done when (all 3 phases) — **all satisfied, 2026-10-07**

- ✅ Full check suite clean (`mypy`, `ruff check`, `ruff format`,
  `lint-imports`, `pytest -q` — 2302 passed).
- ✅ An end-to-end test demonstrates a real `checkov_policy` breach
  failing `build run`, and a clean scan passing it, for terraform
  (`test_build_run_writes_policy_results_when_checkov_policy_breaches`)
  and for helm
  (`test_build_run_checkov_policy_helm_scans_the_real_materialised_chart`).
- ✅ `on_missing_data: block` on one unscannable provisioner fails the
  build while a sibling, scannable provisioner in the same policy is
  still scanned and reported
  (`test_evaluate_checkov_policy_one_unscannable_provisioner_does_not_block_a_sibling`)
  — the one behavior this whole design exists to preserve from v1.

## Open Questions

1. **Real-usage evidence — answered (2026-10-07), checked directly against
   both real repos, not assumed.** `haven` has zero policy infrastructure
   at all (no `policies.yaml` anywhere, confirmed earlier for
   `cve_max_severity` and re-confirmed here). `cfg-int-deployment`'s real
   `config/policies.yaml` has `checkov` present **only as a commented-out
   example** (`#   type: checkov`, `#     custom_checks_dir:
   ".strata/checkov/custom/"`) — identical pattern to every other inactive
   example policy in that file (`cve_max_severity`, `required_tags`,
   `naming_pattern`). A direct search of that repo's real Azure Pipelines
   YAML (`.azure/**`) for `checkov` returned zero matches — never invoked
   in any real CI pipeline. Same conclusion as `cve_max_severity`'s own
   real-usage check: zero evidence of current need, but the design is
   already fully sized and ready whenever a real consumer actually adopts
   it.
2. **Answered (2026-10-07), checked directly against `build_controller.py`'s
   real source, not assumed — "look up `source_path` by provisioner name"
   is NOT the whole story. Three real adjustments needed, found by
   actually reading the step loop and `evaluate_cve_policy()`'s own call
   site, not just this doc's own earlier prose summary of them:**

   - **No unified `dict[str, Path]` of every provisioner's source path
     exists today — this doc's own "v2: current state" section overstated
     it.** `materialise_provisioner_sources()`'s `materialised` dict is
     populated **only** for dependency-only provisioners (reached solely
     via another provisioner's `depends_on`, never their own execution
     step). The main step loop (`for step in
     ordered_by_depends_on(workspace.spec.execution or []): ...`) computes
     its own `source_path` as a **local variable**, used immediately for
     `integration.prepare(source_path, ...)`, then discarded — never
     written back into `materialised` or any other shared structure.
     Confirmed further: `evaluate_cve_policy()` (the established
     "native evaluator wired in dead last" precedent a Checkov evaluator
     would follow) runs *after* this entire loop has already finished, by
     which point every step's own local `source_path` is long out of
     scope. **A future Checkov evaluator therefore needs one small, real
     code change inside the step loop itself** — appending each step's
     `source_path` into a shared `dict[str, Path]` alongside
     `materialised` (or reusing `materialised` directly, since dependency
     provisioners and execution-step provisioners are both just
     "provisioner name → its synced source path") — not merely
     "assembling what's already there," as this doc's prior wording
     implied.
   - **`tool` is an open string in v2, not v1's closed 4-value
     `framework`.** Confirmed via `utils/builtin_types.py`'s real
     `ProvisionerType` enum: `terraform`/`opentofu`/`ansible`/`bicep`/
     `script`/`helm`/`compose`/`argocd`/`flux`, explicitly *not* a closed
     set a schema field is typed against (custom plugins are allowed). A
     genuinely new, v2-specific question v1 never had: does
     `framework: terraform` match only `tool: terraform`, or also `tool:
     opentofu` — confirmed elsewhere in the same module as "treated
     identically to TERRAFORM wherever terraform-specific validation
     applies," which argues for matching both.
   - **Helm is reachable two structurally different ways in v2, not one.**
     `tool: helm` is a real, recognized `ProvisionerType` — a Helm chart
     *could* be declared as a provisioner (main step loop, one
     `source_path`, same shape as terraform/bicep/ansible) — **separate
     from** `Namespace.spec.modules`'s own workload pipeline (a
     structurally disconnected second input shape, per `build_run()`'s
     own code comment), which is the *only* Helm shape v1's Checkov ever
     knew about (v1 has no concept of Helm-as-a-provisioner at all).
     Checked directly: zero real config or test anywhere in this
     workspace declares `tool: helm`/`tool: compose` — a real schema
     possibility with **no current real-world instance**, so a future
     Checkov evaluator can defer handling the provisioner-shaped Helm
     case without any real cost today, so long as the namespace-module
     path (already scoped as "Helm's separate resolution path" in Sizing
     #3) is still built.
3. **Resolved, not open — shared with [cve-scanner-integration.md](../archive/cve-scanner-integration.md)'s
   own "Framework" section.** [policy-engine-architecture.md](policy-engine-architecture.md)'s
   Open Question 1 (generic `PolicyModel`/dispatcher vs. bespoke) is decided
   (2026-10-07): bespoke, indefinitely. Checkov wires in via its own
   `WorkspaceSpecModel.checkov_policy` field + inline evaluator function,
   matching `cve_policy`/`tenant_zone`/`path_convention`'s established
   precedent — not a trigger for a generic dispatcher. See that doc's own
   Open Question 1 for the full, real-usage-evidence-backed reasoning.

## Changelog

- 2026-10-06: Created — catalogs v1's real `integrations/checkov.py` +
  `validators/policies/checkov_policy.py` (both read directly), sized
  against v2's current zero-equivalent state. Flagged the real porting
  trap v1 itself hit and fixed (guessing flat build-path directories
  instead of routing through `get_provisioner_path()`) as the one thing a
  v2 port must not repeat. Split out from
  [policy-engine-architecture.md](policy-engine-architecture.md)'s bucket-C
  catalog into its own tracked doc per direct request.
- 2026-10-07: Re-checked this doc's own "hard prerequisite" claim against
  v2's real, now-shipped `build_controller.build_run()` (not assumed
  stale — actually read) and found it resolved: `build run` already
  computes each provisioner's real materialised `source_path` via
  `sync_source()`, and a `materialised: dict[str, Path]` map for
  dependency-only provisioners, inside `materialise_provisioner_sources()`.
  Corrected "v2: current state"/Sizing #2's stale blocker framing and
  added two new Open Questions (the real output shape's exact
  implications; the shared generic-vs-bespoke policy framework decision,
  cross-referenced from the new
  [cve-scanner-integration.md](../archive/cve-scanner-integration.md) "Framework"
  section). Also linked the two sibling scanner-integration docs split out
  this same pass, `trivy-integration.md`/`grype-integration.md` (both
  later re-merged back into `cve-scanner-integration.md` on 2026-10-07 —
  no longer separate files). No code written — catalog
  correction only, this integration itself remains not started.
- 2026-10-07: Updated Sizing #4 and Open Question 3 — the shared
  generic-vs-bespoke policy engine question both pointed to is now
  resolved in [policy-engine-architecture.md](policy-engine-architecture.md)'s
  own Open Question 1 (2026-10-07: bespoke, indefinitely, no generic
  `PolicyModel`/engine planned, backed by direct real-usage evidence).
  Checkov will wire in via its own `WorkspaceSpecModel.checkov_policy`
  field + inline evaluator, same as every other policy type shipped so
  far — nothing left open here. No code changed — doc update only, this
  integration itself remains not started.
- 2026-10-07: Provisionally decided the `on_missing_data` granularity/
  configurability question (cve-scanner-integration.md's Remaining Work
  item #3), per direct request to make this call now rather than wait
  for Checkov's own implementation. Split the question into two separable
  parts: **granularity** (keep v1's real per-provisioner behavior — a
  whole-policy switch would regress real coverage, since Checkov can
  legitimately scan N independent provisioners per `scope`, unlike CVE's
  single `sbom.json`) and **configurability** (add a real
  `on_missing_data: skip|warn|block` field v1 never had, applied
  per-provisioner — a deliberate improvement over v1, reusing the exact
  same "a security guardrail shouldn't silently treat 'never scanned' as
  'scanned clean'" reasoning this doc's own CVE sibling already
  established and hardened). Net result: one shared operator-facing
  vocabulary across every scanner-type policy, evaluated at whichever
  granularity each policy's own real data shape demands. Marked
  explicitly provisional — not yet validated against real code, to be
  confirmed once this integration is actually built. No code changed —
  doc update only.
- 2026-10-07: Answered Open Question 1 directly — checked both real
  consumer repos and their real CI pipelines on disk, not assumed, same
  discipline already applied to `cve_max_severity`'s own real-usage
  check. `haven` has no policy infrastructure at all; `cfg-int-deployment`
  has `checkov` present only as a commented-out example in
  `config/policies.yaml`, identical to every other inactive example
  policy there, and never invoked in any real Azure Pipelines YAML. Zero
  evidence of current need — doesn't block picking this up, but confirms
  there's no urgency either. No code changed — investigation and doc
  update only.
- 2026-10-07: Answered Open Question 2 in full, per direct request to
  look deeper into it — read `build_controller.py`'s real step loop and
  `materialise_provisioner_sources()` directly, not assumed from this
  doc's own earlier summary. Found "look up `source_path` by provisioner
  name" was not the whole story, three ways: (1) **no unified
  `dict[str, Path]` of every provisioner's source path exists today** —
  `materialised` only covers dependency-only provisioners;
  execution-step provisioners' own `source_path` is a local variable,
  discarded before `evaluate_cve_policy()`'s own established "wired in
  dead last" placement would even run — corrected this doc's prior "just
  assemble the two" framing in "v2: current state," which overstated how
  ready this is. (2) **`tool` is an open string in v2** (confirmed via
  `utils/builtin_types.py`'s real `ProvisionerType` enum), not v1's
  closed 4-value `framework` — raises a genuinely new question v1 never
  had, whether `framework: terraform` should also match `tool: opentofu`.
  (3) **Helm is reachable two structurally different ways in v2** —
  as a provisioner (`tool: helm`, new, v1 never had this) or as a
  namespace module (v1's only shape) — checked directly and confirmed
  zero real config/test uses the provisioner shape today, so it can be
  deferred without real cost. Updated "v2: current state" and Sizing #2
  to reflect the real, now-known step-loop change this integration
  depends on. No code changed — investigation and doc update only.
- 2026-10-07: **Added a full 3-phase Implementation Plan**, per direct
  request ("design and create an implementation plan"), closing out the
  design entirely. Phase 1 (models in a new dedicated `checkov_model.py`
  — not folded into `sbom_model.py` the way CVE's models were, since
  Checkov has no SBOM relationship; `CheckovIntegration`; the
  `build_controller.py` step-loop fix found in Open Question 2 — a
  shared `provisioner_source_paths` dict, seeded alongside the existing
  `materialised` map). Phase 2 (config field, a new dedicated
  `checkov_controller.py::evaluate_checkov_policy()`, `_checkov_policy_
  result()` mirroring `_cve_policy_result()` exactly, reusing
  `write_policy_results()` unchanged — terraform/bicep/ansible only).
  Phase 3 (Helm's separate namespace-module resolution, deferred until
  1/2 are proven). Each phase lists concrete file names, model fields,
  and named test coverage, plus a cross-phase "Done when" criterion
  specifically targeting the one behavior most likely to regress
  (per-provisioner independence under `on_missing_data`). Holding on
  actual implementation per the confirmed zero-current-demand finding —
  design only, nothing built.
- 2026-10-07: **Implemented Phase 1**, per direct request ("design,
  plan, and implement phase 1"). Shipped: `src/strata/models/
  checkov_model.py` (`CheckovFindingModel`, `CheckovScanResultModel` +
  its `findings_at_or_above()` method, `CheckovPolicyModel`);
  `src/strata/integrations/checkov.py` (`CheckovIntegration`, built-in
  only, `--soft-fail` added to the real CLI invocation — a deliberate,
  evidence-grounded addition beyond v1's own documented flags, mirroring
  Trivy's own `--exit-code 0` "never let the subprocess's own exit code
  drive control flow" precedent, since Checkov's real default exit code
  is 1 whenever any check fails); the `build_controller.py` step-loop
  prerequisite from Open Question 2 — implemented more simply than
  originally planned, by extending the existing `materialised` dict
  instead of adding a second, parallel one (avoids an unread variable
  for an entire phase; functionally identical outcome). 51 new tests
  across `test_models_checkov.py`, `test_integrations_checkov.py`, and
  `test_build_controller.py` (including the double-sync/reached-both-
  ways regression case). Full check suite clean (`mypy`, `ruff check`,
  `ruff format`, `lint-imports`, `pytest -q` — 2271 passed). Updated this
  doc's Status header and the Implementation Plan's own Phase 1 section
  to reflect what was actually built (the `materialised`-reuse decision
  differs from the plan as originally written). Phase 2/3 remain not
  started.
- 2026-10-07: **Implemented Phase 2**, per direct request ("design,
  plan, and implement phase 2"). Shipped: `WorkspaceSpecModel.
  checkov_policy`; a real `enforcement: deny | warn` field added to
  `CheckovPolicyModel` — a correction found during implementation, v1's
  own YAML example always paired `enforcement:` with `configuration:`
  and Phase 1's model had missed it; new `src/strata/controllers/
  checkov_controller.py::evaluate_checkov_policy()` + `_FRAMEWORK_TOOLS`
  (reusing `utils.builtin_types.TERRAFORM_COMPATIBLE_TYPES` directly);
  `_checkov_policy_result()` and call-site wiring in `build_controller.py`,
  mirroring the CVE gate exactly. **`scope` was re-interpreted during
  implementation, not ported as originally planned**: a direct read of
  `build_run()`'s own step loop confirmed it iterates every execution
  step unconditionally regardless of `DeploymentModel.spec.stages` (a
  deploy-time-only concept `build run` never consults) — v1's
  deployment-stage-based "staged" has no build-time equivalent in v2, so
  `scope` now means `"staged"` (has its own execution step) /
  `"all"` (every declared provisioner of the framework) / an execution
  step name, keeping the evaluator's signature exactly as planned
  (`workspace` + `provisioner_source_paths`, no `DeploymentModel`
  needed). Also fixed a real pre-existing fragility: the CVE-only
  `write_policy_results()` call site would have silently discarded one
  policy's result the moment a second build-phase policy was ever
  configured at once (that function overwrites the whole file per
  call) — both results are now collected and written together. 20 new
  tests (`test_checkov_controller.py` ×16, `test_build_controller.py`
  ×4, including the "both policies land in one `policy_results.json`"
  regression case). Full check suite clean (`mypy`, `ruff check`,
  `ruff format`, `lint-imports`, `pytest -q` — 2291 passed). Updated
  this doc's Status header and the Implementation Plan's own Phase 2
  section to reflect what was actually built. Phase 3 remains not
  started.
- 2026-10-07: **Implemented Phase 3**, per direct request ("design,
  plan, and implement phase 3"), closing out this integration entirely.
  `_evaluate_helm()` added to `checkov_controller.py` — enumerates
  `NamespaceSpecModel.modules` per namespace, reusing `workload_
  controller.resolve_module()` as-is, skipping disabled/non-helm
  entries entirely (not a missing-data condition) and degrading a
  registry-pulled chart (`chart_name` set) via `on_missing_data`
  (confirmed directly by reading `source_sync.sync_module_source()`'s
  real behavior: it only ever creates an empty `module_dir` for a
  chart-based source). **Re-derived the same `scope` finding Phase 2
  already established, applied to namespaces this time**: a workspace's
  namespaces have no "has its own execution step" equivalent at all, so
  `"staged"`/`"all"` are deliberately identical for `framework: helm` —
  only a bare namespace name genuinely narrows the set. A local chart's
  real path (`build_path/<namespace>/<module reference name>`) is
  recomputed independently rather than threaded through from
  `workload_controller.py` — the provisioner loop and the workload
  pipeline were already established (ADR-0022 D5-D7) as two structurally
  disconnected input shapes, so reusing a well-documented, stable path
  convention was the smaller, less coupling-prone choice over wiring a
  new cross-pipeline path dict. `evaluate_checkov_policy()` gained two
  new optional keyword-only parameters (`namespaces`, `index`), both
  defaulting to `None` so every Phase 2 call/test kept working
  unchanged. 11 new tests (`test_checkov_controller.py` ×10,
  `test_build_controller.py` ×1 — an end-to-end test confirming the
  evaluator scans the exact directory the workload pipeline really
  materialised, reusing `test_build_run_renders_helm_workload_modules()`'s
  own fixture). Full check suite clean (`mypy`, `ruff check`,
  `ruff format`, `lint-imports`, `pytest -q` — 2302 passed). Updated
  this doc's Status header, the Implementation Plan's Phase 3 section,
  and the "Done when" checklist (now fully satisfied) to reflect what
  was actually built. All 3 phases shipped — nothing left open in this
  doc's own Implementation Plan.
- 2026-10-07: **Code review** (per direct request), covering all 3
  phases. Fixed 4 stale docstrings left over from incremental phased
  delivery (`integrations/checkov.py`'s module docstring still said
  Helm resolution "is a later phase"; `checkov_model.py`'s
  `CHECKOV_SEVERITY_ORDER` comment still said "a future
  `checkov_controller`"; `WorkspaceSpecModel.checkov_policy`'s field
  description only mentioned terraform/bicep/ansible;
  `CheckovPolicyModel.scope`'s field description never documented the
  Helm namespace-name form at all) — doc-only, no behavior change.
  **Found and fixed one real gap during the follow-up "anything to dig
  into further?" pass**: `_evaluate_helm()`'s only "nothing to
  evaluate" check was on *namespaces* matching `scope` — if those
  namespaces matched but **every** module inside them was disabled or
  a non-helm type, the function returned with zero diagnostics at all,
  not even an `on_missing_data` finding — precisely the "never scanned
  looks the same as scanned clean" risk this whole design exists to
  prevent (the provisioner-based path already guarded this correctly
  via its own `if not provisioners:` check; Helm's per-module filtering
  happened *inside* the loop with no equivalent whole-policy check
  after it). Fixed by counting modules actually reached (enabled and
  helm-typed) and reporting policy-level `on_missing_data` when that
  count is zero. Two existing tests whose assertions had encoded the
  bug as "correct" (`..._skips_a_disabled_module_reference_entirely`,
  `..._skips_a_non_helm_module_type`) were corrected; 4 tests now cover
  this (block/warn/skip for the all-disabled case, plus the all-non-helm
  case). Also checked, with real evidence, whether the known
  "`scope: staged`/`all` shadow a same-named step/namespace" edge case
  (noted as a non-blocking limitation during the review) has any real
  instances in `haven`/`cfg-int-deployment` — zero matches, confirmed
  via direct grep, left as a documented limitation only. Also attempted
  to smoke-test `CheckovIntegration.scan()` against a real `checkov`
  binary to verify the assumed CLI flags/JSON schema (never previously
  exercised against real output, only mocked in every test) — the one
  local `checkov` install found is a broken pipx-style launcher
  (missing Windows `.py` file association), so this remains
  **unverified against a real binary**, same unverified-CLI-assumption
  risk `cve-scanner-integration.md`'s own Trivy/Grype integration
  already carries by the same precedent. Full check suite clean
  (`mypy`, `ruff check`, `ruff format`, `lint-imports`, `pytest -q` —
  2304 passed).
