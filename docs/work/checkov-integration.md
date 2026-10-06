# Checkov Integration (`policies: checkov`) — v1 Capability Catalog and v2 Gap

- Status: not started — catalog only, sized against v1's real source
- Last updated: 2026-10-06
- Related: [policy-engine-architecture.md](policy-engine-architecture.md)
  (umbrella catalog — `checkov` is bucket C: needs a real external scanner,
  not derivable from strata's own document graph or a Terraform plan),
  [sbom-generation.md](sbom-generation.md) (sibling bucket-C concern, same
  "real external tool, not a strata-internal check" shape)

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

## v2: current state

Nothing — confirmed via `list_dir` on `src/strata/{models,integrations}`:
no `PolicyModel`, no `validators/policies/` package, no
`integrations/checkov.py`. No `build run` command exists yet either
(`v1-consumer-usage.md`'s rebuild order has it as "next" after `validate`),
so there is no `get_provisioner_path()`-equivalent to resolve paths
through yet — this is a hard prerequisite, not just a nice-to-have, given
the porting trap flagged above.

## Sizing

Smaller than `tenant_zone`'s full plan-time tier (no Terraform-plan
parsing, no resource-level location heuristic) but has its own real
complexity, concentrated in path resolution rather than evaluation logic:

1. **`integrations/checkov.py` port** — subprocess wrapper + JSON-output
   parsing into `CheckovFinding`/`CheckovScanResult`. Mechanical, no v1
   design decisions to revisit.
2. **A `CheckovPolicy` (or v2-equivalent model)** — the framework→
   provisioner-type mapping, `scope` resolution (`staged`/`all`/
   `<stage-name>`), severity-gate comparison. Needs `build run`'s
   provisioner-path resolution to exist first (see above) — this is the
   actual blocker, not the Checkov wrapper itself.
3. **Helm's separate resolution path** — namespace/module enumeration via
   `NamespaceService`, local-vs-registry chart distinction. Can be deferred
   to a second phase (terraform/bicep/ansible first, matching
   `sbom-generation.md`'s own "ship a small real slice first" precedent).
4. **Decide the `PolicyModel` question** — same open question as
   [policy-engine-architecture.md](policy-engine-architecture.md) #1: does
   `checkov` get a dedicated v2 model (matching `path_convention`'s
   precedent), or does v2 build a real generic `PolicyModel` once there are
   enough policy types that one-model-per-type stops scaling? Worth
   deciding once, not per policy type.

## Open Questions

1. Real-usage evidence — is `checkov` actually declared in either real
   consumer's `policies:` block today? Not yet checked (same gap flagged
   in the umbrella doc).
2. Does v2's eventual `build run` even produce a flat per-provisioner
   directory shape compatible with `get_provisioner_path()`'s v1 contract,
   or will v2's build-command design (not started) end up organizing
   output differently, requiring this integration's path-resolution logic
   to be redesigned rather than ported as-is?

## Changelog

- 2026-10-06: Created — catalogs v1's real `integrations/checkov.py` +
  `validators/policies/checkov_policy.py` (both read directly), sized
  against v2's current zero-equivalent state. Flagged the real porting
  trap v1 itself hit and fixed (guessing flat build-path directories
  instead of routing through `get_provisioner_path()`) as the one thing a
  v2 port must not repeat. Split out from
  [policy-engine-architecture.md](policy-engine-architecture.md)'s bucket-C
  catalog into its own tracked doc per direct request.
