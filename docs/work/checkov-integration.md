# Checkov Integration (`policies: checkov`) — v1 Capability Catalog and v2 Gap

- Status: not started — catalog only, sized against v1's real source. The
  one hard prerequisite this doc originally flagged (`build run`'s
  provisioner-path resolution) is now unblocked — see "v2: current state"
  below. **The `on_missing_data` granularity/configurability question is
  provisionally decided (2026-10-07) — see "v2 design decision" below.**
- Last updated: 2026-10-07
- Related: [policy-engine-architecture.md](policy-engine-architecture.md)
  (umbrella catalog — `checkov` is bucket C: needs a real external scanner,
  not derivable from strata's own document graph or a Terraform plan),
  [cve-scanner-integration.md](cve-scanner-integration.md) (sibling
  bucket-C concern and the framework doc for scanner-type integrations —
  `ScannerIntegration` capability, shared open questions all three face),
  [trivy-integration.md](trivy-integration.md)/
  [grype-integration.md](grype-integration.md) (the other two scanner
  integrations, both already implemented as `CveScannerIntegration`
  backends), [sbom-generation.md](sbom-generation.md) (sibling bucket-C
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
[cve-scanner-integration.md](cve-scanner-integration.md)'s own
`on_missing_data: skip|warn|block`, a real, documented ADR-0082 field).
v1's Checkov degradation is hardcoded to "always skip, always warn" —
never configurable to block. This is the real shape the decision below
has to reconcile against, not an assumption.

### v2 design decision (provisional — decided ahead of implementation, 2026-10-07)

Per [cve-scanner-integration.md](cve-scanner-integration.md)'s own
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
justification is the same one [cve-scanner-integration.md](cve-scanner-integration.md)'s
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

**The one hard prerequisite this doc originally flagged — resolved.** It
read "no `build run` command exists yet either... no
`get_provisioner_path()`-equivalent to resolve paths through." `build run`
now exists (`build_controller.build_run()`, shipped) and already resolves
exactly this: each execution step computes `source_path = sync_source(
context.root, build_path, provisioner.source, remotes)` (the real,
already-materialised on-disk directory for that provisioner, honouring
`source.target_path`/`source.source_path` the same way the matching
provisioner's own render step does), and every dependency-only provisioner
(reachable only via another provisioner's `depends_on`, never its own
execution step) is resolved the same way into a `materialised: dict[str,
Path]` map inside `materialise_provisioner_sources()`. A future Checkov
evaluator wired inline into `build_run()` (matching `evaluate_cve_policy()`'s
own precedent) would have direct access to both — no new path-resolution
machinery needs to be built, only assembling the two into one `dict[str,
Path]` the evaluator can look up by provisioner name. The `helm` framework's
own separate resolution (namespace/module enumeration) is unaffected by
this — still needs `NamespaceService` enumeration as originally scoped.

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
   longer blocked**, see "v2: current state" above.
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

## Open Questions

1. Real-usage evidence — is `checkov` actually declared in either real
   consumer's `policies:` block today? Not yet checked (same gap flagged
   in the umbrella doc).
2. `build run`'s real output shape (confirmed: each provisioner's
   `source_path` is wherever `sync_source()` materialised it, honouring
   `source.target_path`/`source.source_path` — not necessarily one flat
   directory per framework) — does this integration's path-resolution
   logic need any adjustment beyond "look up `source_path` by provisioner
   name," or is that the whole story? Worth a closer check once this is
   actually picked up, not assumed clean from this catalog pass alone.
3. **Resolved, not open — shared with [cve-scanner-integration.md](cve-scanner-integration.md)'s
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
  [cve-scanner-integration.md](cve-scanner-integration.md) "Framework"
  section). Also linked the two sibling scanner-integration docs split out
  this same pass, [trivy-integration.md](trivy-integration.md)/
  [grype-integration.md](grype-integration.md). No code written — catalog
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
