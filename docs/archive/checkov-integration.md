# Checkov Integration (`policies: checkov`) — Shipped Design & Reference

- Status: **Shipped end to end** — models (`checkov_model.py`),
  `CheckovIntegration`, the `build_controller.py` step-loop prerequisite,
  `WorkspaceSpecModel.checkov_policy`, and `checkov_controller.py`'s
  inline evaluator (terraform/bicep/ansible, plus Helm's own
  namespace/module resolution path) are all built and tested, wired into
  `build run` end to end, and code-reviewed. The full incremental
  design/implementation history (3 phases + code review) has been
  condensed into the settled summary below (available via git history if
  ever needed).
- Last updated: 2026-10-08
- Related: [policy-engine-architecture.md](../work/policy-engine-architecture.md)
  (umbrella catalog — `checkov` is bucket C: needs a real external
  scanner, not derivable from strata's own document graph or a Terraform
  plan), [cve-scanner-integration.md](cve-scanner-integration.md) (sibling
  bucket-C concern and the framework doc for scanner-type integrations —
  `ScannerIntegration` capability, shared open questions both face; this
  doc's own "v2 design decision" extends that doc's `on_missing_data`
  reasoning to a genuinely per-unit (not whole-policy) shape),
  [sbom-generation.md](../work/sbom-generation.md) (sibling bucket-C
  concern, same "real external tool, not a strata-internal check" shape)

## Overview

`checkov` is one of v1's 19 real built-in policy types (`policies: type:
checkov`) — it wraps the real [Checkov](https://www.checkov.io/) CLI to
scan rendered IaC artifacts (Terraform, Bicep, Ansible, Helm charts) for
security misconfigurations, gated by a configurable severity threshold.
Ported from v1's real `integrations/checkov.py` +
`validators/policies/checkov_policy.py` (both read directly, not from
memory).

**The one real porting trap, found in v1's own source and worth stating
up front**: v1's policy resolves each scanned provisioner's artifact
directory through `get_provisioner_path()` — the same single source of
truth the matching builder/deployer already use — after an earlier
revision guessed at flat candidate directories and silently reported
`passed=True` for any workspace whose provisioner used a nested path. v2's
own equivalent (`build_run()`'s per-step `source_path`, now in the shared
`materialised` dict) is what the evaluator resolves through here too —
never re-derived independently.

## Design decisions (settled)

- **Native Python evaluator, not OPA/a generic engine** — matches every
  other bucket-C policy shipped so far ([policy-engine-architecture.md](../work/policy-engine-architecture.md)'s
  own Open Question 1: bespoke, indefinitely, no generic `PolicyModel`/
  dispatcher planned).
- **A dedicated `checkov_model.py` module**, not folded into
  `sbom_model.py` the way CVE's models were — Checkov never touches an
  SBOM, it scans rendered IaC directly, so the SBOM-specific grouping
  that justified CVE's placement doesn't apply.
- **`WorkspaceSpecModel.checkov_policy`**, same placement reasoning as
  `cve_policy` — a build-phase concern, not a per-deployment one.
- **`on_missing_data: skip|warn|block`, per-provisioner — a deliberate
  improvement over v1, not a straight port.** v1's own real Checkov
  config has no such field at all; its degradation is hardcoded to
  "always skip, always warn," never configurable to block. Two
  genuinely separable decisions were made here:
  1. **Granularity stays per-provisioner, not normalized to a single
     whole-policy switch** (unlike CVE, which only ever has one
     `sbom.json`). Checkov's `scope` can legitimately name N independent
     provisioners/namespaces in one evaluation; collapsing to one
     whole-policy switch would mean one unscannable provisioner (e.g. a
     registry-pulled Helm chart with no local source) blocks reporting
     on every *other*, perfectly scannable provisioner in the same
     policy — a real regression from v1's own "skip that one
     provisioner only" behavior.
  2. **Configurability is added** (`skip`/`warn`/`block`, `block`
     default) — the same "a security guardrail shouldn't let 'never
     scanned' look like 'scanned clean'" reasoning
     [cve-scanner-integration.md](cve-scanner-integration.md) already
     established for CVE, extended here since Checkov is equally a
     security guardrail.

  Net effect: one shared operator-facing vocabulary
  (`on_missing_data: skip|warn|block`, same field name/values/default)
  across every scanner-type policy, while each policy's own internal
  evaluation granularity stays whatever its real data shape demands.
- **`scope` is build-time-native, not v1's deployment-stage concept.**
  `build_run()` never consults `DeploymentModel.spec.stages` (a
  deploy-time-only runtime-knobs concept). `scope` means: `"staged"`
  (default — provisioners/namespaces with their own execution step) |
  `"all"` (every declared provisioner of the matching framework) | a
  bare execution-step or namespace name. For `framework: helm`
  specifically, `"staged"`/`"all"` are deliberately equivalent — a
  workspace's namespaces have no "has its own execution step"
  distinction the way provisioners do; only a bare namespace name
  genuinely narrows the set.

## Implementation (shipped)

- **Models** (`src/strata/models/checkov_model.py`): `CheckovFindingModel`/
  `CheckovScanResultModel` (field-for-field port of v1's real
  `CheckovFinding`/`CheckovScanResult`, including `findings_at_or_above()`
  as a real method) and `CheckovPolicyModel` (`framework`, `severity_gate`,
  `scope`, `skip_checks`/`include_checks`, `custom_checks_dir`, `timeout`,
  `on_missing_data`, `enforcement: deny | warn` — the last one a
  mid-implementation correction: v1's own YAML always paired
  `enforcement:` alongside `configuration:`, missed in the first model
  draft).
- **`CheckovIntegration`** (`src/strata/integrations/checkov.py`): built-in
  only (never `IntegrationModel`-configured, matching
  `CveScannerIntegration`'s precedent). Wraps `checkov --directory <path>
  --output json [--framework ...] [--check ...] [--skip-check ...]
  [--compact] --soft-fail` (`--soft-fail` a deliberate addition beyond
  v1's documented flags, mirroring Trivy's own "never let the subprocess's
  exit code drive control flow" precedent). `ensure_available()` checks
  `shutil.which("checkov")`.
- **`build_controller.py` step-loop prerequisite**: the main step loop's
  own `source_path` (previously a local variable, discarded before any
  evaluator could read it) now writes into the same `materialised` dict
  `materialise_provisioner_sources()` already populates for
  dependency-only provisioners — one unified provisioner→path mapping
  regardless of how a provisioner was reached, rather than a second,
  parallel dict.
- **`evaluate_checkov_policy()`** (`src/strata/controllers/
  checkov_controller.py`): for each provisioner matching
  `checkov_policy.framework` (via `_FRAMEWORK_TOOLS`, reusing
  `TERRAFORM_COMPATIBLE_TYPES` so `opentofu` matches `framework:
  terraform` too) selected by `scope`, scans its real materialised path
  and applies the severity-gate check. A breach on any one provisioner
  denies the whole policy (AND semantics, matching v1), but every
  provisioner is still scanned and reported independently —
  `on_missing_data` governs degradation per-provisioner only, never the
  whole policy at once. `_checkov_policy_result()` mirrors
  `_cve_policy_result()` exactly for `policy_results.json`; both CVE's
  and Checkov's results are collected and written in one
  `write_policy_results()` call (a real pre-existing fragility fixed
  along the way — that function overwrites the whole file per call, so
  two independent calls would have silently discarded one policy's
  result the moment both were ever configured at once).
- **Helm's separate resolution path** (`_evaluate_helm()`): enumerates
  `NamespaceSpecModel.modules` per namespace in scope (reusing
  `workload_controller.resolve_module()` as-is), skipping disabled/
  non-helm entries silently (not a missing-data condition) and degrading
  a registry-pulled chart (`chart_name` set, no local source) via
  `on_missing_data`. A local chart's real path
  (`build_path/<namespace>/<module reference name>`) is recomputed
  independently rather than threaded through from `workload_controller.py`
  — the provisioner loop and the workload pipeline are two structurally
  disconnected input shapes (ADR-0022 D5–D7), so reusing a stable,
  already-documented path convention was the smaller, less
  coupling-prone choice. **Code review fix**: the only "nothing to
  evaluate" check was on namespaces matching `scope` — if those matched
  but every module inside them was disabled or non-helm, zero
  diagnostics were emitted at all (not even an `on_missing_data`
  finding), the exact "never scanned looks like scanned clean" risk this
  whole design exists to prevent. Fixed by counting modules actually
  reached and reporting policy-level `on_missing_data` when that count
  is zero.
- **Tests**: `test_models_checkov.py`, `test_integrations_checkov.py`,
  `test_checkov_controller.py` (terraform/bicep/ansible + Helm, severity
  gate, `enforcement`, every `on_missing_data` value, `scope` variants,
  the one-unscannable-provisioner-does-not-block-a-sibling regression
  case), plus `test_build_controller.py` additions (the shared
  `materialised` dict covers every execution-step provisioner too,
  including the double-sync case; both policies land in one
  `policy_results.json`; end-to-end terraform and Helm breach/pass
  cases).
- **Honest, carried-over limitation**: never verified against a real
  `checkov` binary — every test stubs `run_command`. The one local
  install found is a broken pipx-style launcher (missing Windows `.py`
  file association) — same unverified-CLI-assumption risk
  [cve-scanner-integration.md](cve-scanner-integration.md)'s own Trivy/
  Grype integration already carries, by the same precedent.

## Resolved Open Questions

- **Real-usage evidence**: checked directly against both real consumer
  repos. `haven` has no policy infrastructure at all; `cfg-int-deployment`
  has `checkov` present only as a commented-out example in
  `config/policies.yaml` (identical pattern to every other inactive
  example policy there) and is never invoked in any real Azure Pipelines
  YAML. Zero evidence of current need — doesn't block the build, confirms
  no urgency either.
- **The generic-vs-bespoke policy engine question** is shared with, and
  resolved by, [policy-engine-architecture.md](../work/policy-engine-architecture.md)'s
  own Open Question 1 (bespoke, indefinitely) — not re-litigated here.
- **`tool` is an open string in v2**, not v1's closed 4-value `framework`
  — `framework: terraform` matches both `tool: terraform` and `tool:
  opentofu` (treated identically wherever terraform-specific validation
  applies elsewhere in this codebase too).
- **Helm is reachable two structurally different ways in v2** — as a
  provisioner (`tool: helm`, new, v1 never had this) or as a namespace
  module (v1's only shape, and the only one with any real current
  usage — confirmed via direct search, zero config/test anywhere
  declares `tool: helm`). The provisioner-shaped case was deferred
  without real cost; the namespace-module path is the one actually built.

## Changelog

- 2026-10-06: Created, catalog-only — v1's real `integrations/checkov.py`
  + `validators/policies/checkov_policy.py` read directly and sized
  against v2's then-empty state. Flagged the `get_provisioner_path()`
  porting trap v1 itself hit and fixed as the one thing a v2 port must
  not repeat.
- 2026-10-07: Re-checked the "hard prerequisite" claim against v2's
  real, newly-shipped `build_controller.build_run()` and found the
  picture more involved than first assumed — no unified provisioner→path
  mapping existed yet; `tool` is an open string, not v1's closed
  `framework`; Helm is reachable two structurally different ways in v2.
  Decided the `on_missing_data` granularity/configurability question
  provisionally (per-provisioner granularity kept, new configurable
  field added) ahead of implementation, then confirmed real-usage
  evidence in both consumer repos. Design only, nothing built yet.
- 2026-10-07: **Implemented all 3 phases and code-reviewed, in one
  continuous build-out**: models + `CheckovIntegration` + the
  `build_controller.py` step-loop fix (Phase 1); the config field +
  inline evaluator for terraform/bicep/ansible, re-interpreting `scope`
  as build-time-native since v1's deployment-stage concept has no v2
  equivalent, plus fixing the `policy_results.json` overwrite fragility
  found while wiring Checkov in alongside CVE (Phase 2); Helm's separate
  namespace/module resolution path, re-deriving the same `scope` finding
  for namespaces (Phase 3). Code review then found and fixed one real
  gap — Helm's all-modules-disabled/non-helm case silently reported
  nothing instead of a missing-data finding — and 4 stale docstrings left
  over from incremental delivery. Full check suite clean throughout;
  2304 passing after the code review pass.
- 2026-10-08: Archived — fully shipped, all open questions resolved,
  nothing left pending. Moved from `docs/work/` (condensing the
  phase-by-phase changelog into this summary) alongside its sibling
  [cve-scanner-integration.md](cve-scanner-integration.md), matching that
  doc's own precedent. No new ADR written for the `on_missing_data`
  decision above — consistent with CVE's own equivalent decision, which
  also has no dedicated ADR; these are implementation-doc-level config
  decisions, not standalone architectural ones.
