# Integration readiness - Work

- Status: in-progress (Phases 1-9 done; recorded as [ADR-0050](../decisions/0050-one-offline-integration-readiness-check.md) and [ADR-0051](../decisions/0051-local-tools-as-declarable-integration-types.md))
- Last updated: 2026-10-27

## Overview

Two separate questions can be asked of any Integration document, and strata currently answers
neither of them completely:

| Question                 | Checks                                                                      | Needs                                   |
| ------------------------ | --------------------------------------------------------------------------- | --------------------------------------- |
| Is it usable **here**?   | type is registered, `spec.enabled`, tool on PATH, version vs `spec.version` | nothing - offline, no credentials       |
| Is it reachable **now**? | credentials resolve and the platform accepts them                           | a deployment (for credentials), network |

v1 answered the first with `strata tools status|check`; v2's `strata integration check` (added for
security gates, [security-gates.md](../design/security-gates.md) D7) answers only the second.
This doc plans one readiness check that answers both, used by deploy preflight and by the commands.

## Current Design / Progress

What exists (verified 2026-10-26):

- `Integration` (`integrations/base.py`) has `is_available()` (command on PATH), `get_version()`
  (cli transport only; `None` for http/sdk) and `ensure_version(expected)` (PEP 440 specifier).
- **Bug: `ensure_version()` has no caller.** `docs/config/integration.md` and
  `IntegrationModel.spec.version`'s own description say the version is "checked at preflight", but
  deploy preflight (`deploy_controller.deploy_run()`) only calls `is_available()`. A
  `version: ">= 1.7"` (as in `config/integrations/terraform.yaml`) is never enforced.
- `strata integration check NAME [--deployment D]` runs `ConnectionCheckIntegration.check_connection()`;
  a type without it (terraform, helm, ...) exits 2 "has no connection check" instead of reporting
  local readiness.
- `strata validate` already checks declared-but-unknown types, disabled gate integrations, and
  capability/ABC mismatches (`semantic_checks.py`); it never touches PATH or versions (it must stay
  runnable anywhere).

v1 evidence (read from v1 source `commands/cli_tools.py`, `controllers/tools_controller.py`):

- `tools status [--file D] [--required|--optional|--available|--missing]`: every known integration
  type, available on PATH, installed version, capabilities; with a deployment, each marked
  required/optional from the deployment's stores, provisioners and declared integrations.
  `--missing` exits 3 when a required one is unavailable.
- `tools check NAME`: one type by name - available, version, setup info. No credentials, no network.
- `tools install NAME`: setup guidance, optionally AI-generated.
- Neither real consumer's CI calls any of them (haven, cfg-int): debugging aids, not critical path.

## Proposal

1. **Enforce `spec.version` at deploy preflight**, next to `is_available()`, before any step runs.
2. **One readiness function** in the controllers layer, per Integration document: registered type,
   enabled, available, installed vs required version, connection check supported. Deploy preflight
   and both commands call it, so they cannot drift apart.
3. **`strata integration check NAME [--deployment D]` answers both questions**: local checks
   always, then the connection check when the type has one. A type without one passes on local
   checks (exit 0, not 2). An undeclared NAME that is a registered type is checked with its
   defaults (v1 `tools check terraform`); an undeclared, unknown NAME says exactly that and lists
   the declared names and known types (exit 2).
4. **`strata integration list [--deployment D]`** (v1 `tools status`): one row per Integration
   document with the local columns. With `--deployment`: also what the deployment uses it for
   (provisioner steps, stores, remotes/assets, output store, gate policies, ...), and exit 3 when
   something it uses is unavailable or the wrong version - an offline CI preflight.
5. **Not ported:** `tools install` (setup guidance / AI guide) - no evidence of use.

## Phase 1 design - enforce `spec.version`

Verified before deciding (2026-10-26):

- Only deploy runs Integration-backed tools: `deploy_run()` (behind `deploy run|setup|check|plan|apply`)
  preflights every filtered step with `is_available()`. `build run` only renders; gates and stores
  are networked.
- `ensure_version()` builds a `packaging` `SpecifierSet` directly, so Terraform-style `~> 1.9` -
  the example in `IntegrationModel.spec.version`'s own description - raises `InvalidSpecifier`, an
  uncaught non-`StrataError`. A plain `>= 1.7` (with a space) parses fine.
- `packaging` accepts `v3.14.0` and prerelease versions such as `1.10.0-beta1` for `>= 1.7`; an
  unparseable version never satisfies anything.
- Real `spec.version` values in use: `config/` terraform `>= 1.7`; `.v2-haven` terraform `>= 1.6.0`
  and `git >= 2.30.0` (`git` is not a registered type, so skipped).

Decisions:

- **D1 - Where.** Deploy preflight, next to `is_available()`, for each distinct integration of the
  filtered steps (one `--version` call per integration, not per step). Failure is an error
  (`integration_version_mismatch`) naming provisioner, step, installed and required version; no
  step runs.
- **D2 - `--smoke-test` skips it.** Its contract is zero tool contact, and reading a version runs the
  tool; it reports "would check <type> version '<constraint>'" instead. `--dry-run` checks.
- **D3 - Syntax.** PEP 440 specifiers, plus Terraform's `~>` read as its exact PEP 440 equivalent
  `~=` (`~> 1.9` = `>= 1.9, < 2.0` = `~= 1.9`). One parser in `utils/version_constraints.py`, used
  by the model and by `ensure_version()`. An unparseable constraint is a load-time error, so
  `strata validate` reports it.
- **D4 - Networked-only integrations (open question 1, resolved).** `spec.version` on a type with no
  `cli` transport, or with `spec.transport` set to something other than `cli`, is a validate error
  (`integration_version_unenforceable`): strata cannot read a remote service's version, and a
  silently ignored assertion is worse than none. Unregistered types are skipped (reported elsewhere).
  A multi-transport class that falls back to a networked transport at runtime still fails loudly at
  preflight ("could not determine the installed version").

## Phase 2 design - one readiness check

Verified before deciding (2026-10-26):

- Deploy preflight is the only caller today (`deploy_run()`); it gets each step's integration from
  `resolve_integration()`, which also calls `bind(provisioner)`. `ScriptIntegration`'s command
  depends on the provisioner, so two steps can share an integration *name* but run different
  commands: Phase 1's once-per-name version cache was keyed too coarsely.
- An enabled check exists only for auto-binding (`bind_integration_config()` ignores disabled
  documents) and for remotes/gates in `semantic_checks.py`. A provisioner that **names** a disabled
  Integration document passes validate and runs at deploy.
- Phase 1 called `ensure_version()`, which reads the version itself; a readiness record that also
  shows the installed version would read it twice.

Decisions:

- **D5 - Shape.** `controllers/integration_readiness.py`: `assess_integration(integration, *,
  read_version=True) -> IntegrationReadiness`, a frozen record (name, type, declared, enabled, local
  tool or not, command, available, required and installed version, connection check supported) with
  `problems: tuple[ReadinessProblem(code, message)]` and `ok`. Offline: never resolves credentials,
  never calls `check_connection()` (that stays the command's job, Phase 3).
- **D6 - Problems and codes.** `integration_disabled`, `integration_unavailable` (message unchanged:
  `'<command>' is not available on PATH.`), `integration_version_mismatch` (also for "could not
  determine"), `integration_version_unenforceable` (networked transport with `spec.version`),
  `integration_configuration_invalid` (an unsupported `spec.transport`). The version is not read
  when the tool is unavailable or `read_version=False`.
- **D7 - One comparison rule.** `version_satisfies(installed, constraint)` in
  `utils/version_constraints.py`; `ensure_version()` and the readiness check both use it, and the
  check reads the version once.
- **D8 - Deploy preflight on top of it.** Assess once per `(integration name, command)`; report each
  problem once, prefixed `Provisioner '<p>' (step '<s>'):` on the first step that uses it; under
  `--smoke-test` `read_version=False` and the same "would check" line. Deliberate changes from
  today: a provisioner naming a disabled Integration now fails preflight; an unavailable tool is
  reported once instead of once per step; `integration_unavailable` gains a code.

## Phase 3 design - `strata integration check`

Verified before deciding (2026-10-26):

- Today the command constructs the named document's class and requires `ConnectionCheckIntegration`
  (terraform: exit 2 "has no connection check"); `enabled: false` exits 2; an undeclared name exits 2
  listing only declared names.
- Deploy finds an integration by **type** through `bind_integration_config()` (the single enabled
  document of that type, else none; several is a `UsageError`). v1's `tools check NAME` took a type.
- The registry has no public list of known types (`_KNOWN` plus entry points).

Decisions:

- **D9 - Lookup.** NAME is a document name first; otherwise, if it is a registered type, the
  integration deploy would bind for that type (`bind_integration_config()`, defaults when no enabled
  document); otherwise exit 2: "'x' is not declared and is not a known integration type", listing
  declared names and `registry.known_types()` (new). Several enabled documents of the type: exit 2
  (the existing auto-bind message). A class that cannot be constructed (unregistered type on a
  document, bad configuration): exit 3.
- **D10 - Order.** Local readiness (`assess_integration()`) always runs first and is reported in
  full; any readiness problem exits 3 and skips the connection check. Then, only for a type with a
  connection check: `--deployment` needed (exit 2), credentials (exit 3), `check_connection()`
  (exit 1 unreachable, 3 rejected). A type without one reports "local checks only" and exits 0.
- **D11 - Exit codes.** 0 ready; 1 platform unreachable; 2 NAME neither declared nor a known type,
  several documents of the type, `--deployment` needed, bad arguments; 3 not ready locally
  (disabled - previously 2, now consistent with deploy preflight - not on PATH, version mismatch,
  misconfigured) or credentials missing/rejected/unresolved.
- **D12 - Output.** One info line describing local readiness (`integration_ready`), each problem as
  an error with its readiness code, connection lines as before (`integration_check_ok`) or
  `integration_no_connection_check`. JSON `data`: `integration`, `type`, `declared`, `readiness`
  (every field plus `problems`), `checks`.
- **D13 - Lookup lives in the controller.** `resolve_for_check(index, name)` in
  `integration_readiness.py`, so Phase 4 can reuse it; the command stays a thin layer.

## Phase 4 design - `strata integration list`

Verified before deciding (2026-10-26), resolving open question 3 from real call sites:

| Binding                         | Where it is bound                                                         | When       | Has a class? |
| ------------------------------- | ------------------------------------------------------------------------- | ---------- | ------------ |
| Execution step provisioners     | `resolve_integration()` (named, else by `tool`)                           | deploy     | yes          |
| Value stores (non-direct)       | `value_controller._Resolvers` (`bind_integration_config()` by store type) | deploy     | yes          |
| Output store                    | `resolve_output_store_integration()`                                      | deploy     | yes          |
| Gate policies                   | `security_gate_controller` (by name)                                      | deploy     | yes          |
| Workload module types           | `resolve_module_integration()` in `workload_controller`                   | build only | yes          |
| Remotes, assets (`sources`)     | `remote_credentials.py` reads `spec.authentication` only                  | build only | **no**       |
| Audit sinks (`integration` arm) | not dispatched yet (info finding only)                                    | -          | -            |

Finding: a `sources`-only document (`type: git`, as in `config/integrations/git-customers.yaml`) is a
**credential holder**, never constructed through the registry. Phase 3's `integration check
git-customers` therefore reports a false "type not registered" (exit 3).

Decisions:

- **D14 - Rows.** One row per declared Integration document, plus one per type the deployment binds
  without a document (defaults). Script integrations stay one row per `(name, command)`, as in
  deploy preflight. Reading versions runs each local tool once (as v1 `tools status` did).
- **D15 - Registered or credential holder.** Readiness gains `registered`. A document whose type has
  no class is fine when its capabilities are only `sources` (and `x-` extensions) - it is reported
  as a credential holder; otherwise `integration_type_unregistered`. `integration check` uses the
  same assessment, fixing the false exit 3 above.
- **D16 - `--deployment` usage map.** "Used" means what `deploy run` binds for that deployment:
  execution step provisioners, non-direct value stores of the reachable environments, the output
  store, and the two gate policies. Build-time-only bindings (module types, remotes, assets) are
  out of scope here: `build run` already fails on those, and remote credentials are checked by
  `strata validate`.
- **D17 - Exit codes.** 0 listed. With `--deployment`: 3 when an integration the deployment uses is
  not ready (each problem an error); problems of unused rows are warnings. Without: every problem is
  a warning, exit 0 (v1 `tools status` likewise only failed with `--missing`). 2 for an unknown
  deployment or an ambiguous auto-bind (as deploy).
- **D18 - No filter flags yet** (open question 4): `--output json` plus `jq` covers them.

## Review fixes design (2026-10-27)

Code review of Phases 1-4 (findings R1-R11). Verified before deciding: `git` is invoked as a CLI by
`git_checkout.py`, `remote_resolution.py`, `git_push.py`, `audit_push.py` and the gitops integration
(`COMMAND = "git"`), yet `type: git` has no registered class; `CAPABILITY_ABCS` has no entry for
`sources`, so a `sources`-only class needs no ABC; `ensure_version()` has no production caller since
Phase 2 (deploy uses `assess_integration()`).

- **R1 - Register `git` as a local tool.** A minimal `GitIntegration` (`sources`, `cli`, command
  `git`, version from `git --version`); `git` leaves the "v1 type not ported" list. A `type: git`
  document is then checked like any CLI tool (PATH, `spec.version`) by `integration check|list`
  and by the Phase 1 validate rule. Enforcing it during `build run` belongs to the build-time
  preflight (open question 2). Unregistered `sources`-only documents stay credential holders.
- **R2 - Remove `ensure_version()`.** One comparison path (`assess_integration()` +
  `version_satisfies()`); its tests move to the readiness and constraint tests.
- **R3 - `registered` means "type is registered".** A known type whose class rejects its
  configuration is `registered: true` with `integration_configuration_invalid`.
- **R4 - Disabled and unused is not a warning.** `integration list` reports nothing for a disabled
  document no deployment uses; the STATUS column still says `disabled`.
- **R5 - Networked type without a connection check** says "nothing to verify without a connection
  check" instead of "local checks only".
- **R6 - Connection failures become diagnostics.** Credentials unresolved/missing/rejected -> error,
  exit 3; unreachable -> error, exit 1; the readiness line and JSON `data` survive. `--deployment`
  needed and an unknown deployment stay usage errors (exit 2).
- **R7 - One JSON shape** for a readiness record in `check` and `list` (`ok`, `credential_holder`,
  `problems` included in both).
- **R8 - Ambiguous type in `check`:** "several enabled '<type>' documents (a, b); check one by name".
- **R9 - Name clash in `list`:** a declared document is only hidden by a row for the same
  *declared* document; a type-default row shows as `<type> (type default)`.
- **R10 - Module docstring** covers both commands.
- **R11 - Tests:** two script provisioners with different interpreters (deploy preflight), `list
  --pin`, controller-level tests for `assess_document`/`assess_named`/`list_integrations`, and a
  disabled credential holder.

## Phase 5 design - integration preflight for build and deploy

Proposed rule (2026-10-27): check every enabled integration; ready -> ok; not ready -> error when
`spec.required`, else warning. Verified before deciding:

- **`spec.required` is never read.** The field ("required for platform operation") is set in real
  documents - `config/` terraform `true`, `.v2-cfg` keyvault and terraform `true`, `.v2-haven` git
  `true` - and v1's `tools status --missing` used it, but no v2 code consults it: the same class of
  "documented, silently ignored" gap as `spec.version` was before Phase 1.
- `build run` checks nothing up front; each tool fails where it is first used (a clone, a scan).
  Deploy preflight checks only the filtered steps' tools.
- `integration list` already assesses every document; only its severity rule differs (used ->
  error, unused -> warning).

Decisions:

- **D19 - Generic, one rule, three callers.** `integration_preflight(index, *, exclude, read_version)`
  in `integration_readiness.py`: every **enabled** Integration document, assessed as in D5;
  problems are errors when `spec.required`, warnings otherwise. Disabled documents are skipped.
  The rule lives in one function, `readiness_diagnostics(readiness, used=...)` (error when required
  *or* used by the run, warning otherwise, nothing for a disabled unused document), which
  `integration list` uses too.
- **D20 - The command is `strata integration list`** (no new command): without `--deployment` it
  now exits 3 when a *required* integration is not ready; non-required problems stay warnings.
  It is the stand-alone "is this machine ready?" check v1's `tools status --missing` was.
- **D21 - `build run` runs it first**, before cleaning or rendering: a required integration that is
  not ready stops the build (exit 3); other problems are warnings in its report. `--dry-run` runs
  it too (read-only).
- **D22 - Deploy runs it after the step preflight**, excluding the integrations the filtered steps
  already checked (those stay errors, D8), so nothing is reported twice. `--smoke-test` passes
  `read_version=False`.
- **D23 - `strata validate` does not run it** - validate must work on any machine; readiness is a
  property of the machine, not of the documents.
- **D24 - `readiness.required`** joins the record (and the JSON shape).

## Phases 6-8 design - Trivy, Grype and Checkov as declarable integrations

Verified before deciding (2026-10-27):

- `CveScannerIntegration` (`integrations/cve_scanner.py`, type `cve_scanner`) picks Trivy or Grype
  from PATH at construction (Trivy preferred) and is built only by `sbom_controller.
  evaluate_cve_policy()` as `CveScannerIntegration()`; version args differ (`trivy --version`,
  `grype version`). `CheckovIntegration` (type `checkov`, `COMMAND = "checkov"`) is built only by
  `checkov_controller` as `CheckovIntegration()`, twice. Neither is in the registry, so no document
  can declare them; `checkov` is even listed as "a v1 type not yet ported".
- Both already declare `Capability.SCANNER` and implement `ScannerIntegration`; nothing else
  depends on their constructors except tests that monkeypatch the class names with no-argument
  lambdas.

Shared decisions:

- **D25 - Register three types:** `trivy`, `grype`, `checkov`, each a `ScannerIntegration` with the
  `scanner` capability and the `cli` transport. A document then gets everything Phases 1-5 give any
  local tool: `spec.version` (validated and enforced), `spec.required` (build/deploy preflight),
  `spec.command` (a non-default binary), `integration check|list`.
- **D26 - Declared wins, else today's behaviour.** The CVE gate uses the enabled `trivy` document if
  there is one, else the enabled `grype` document, else the existing PATH auto-detection
  (`CveScannerIntegration()`). The Checkov gate uses the enabled `checkov` document, else
  `CheckovIntegration()`. A declared scanner that is missing is not silently swapped for the other
  one: the gate's `on_missing_data` applies, and the preflight already reported it.
- **D27 - Binding by type** through `bind_integration_config()`, as stores and module types bind:
  several enabled documents of one type is the same usage error.
- **D28 - No new capability or command.** Scanners stay out of `integration list --deployment`'s
  usage map (build-time, D16); the preflight covers them.

Per phase:

- **Phase 6 - `trivy`.** `CveScannerIntegration` takes an optional `config` and a fixed `backend`;
  `command` honours `spec.command`; `ensure_available()` checks the resolved command.
  `TrivyIntegration` fixes the backend to `trivy`. `sbom_controller` selects per D26.
- **Phase 7 - `grype`.** `GrypeIntegration`, the same with `grype` (version via `grype version`);
  added to the D26 selection after Trivy.
- **Phase 8 - `checkov`.** Register `CheckovIntegration` (out of the v1 list); `checkov_controller`
  binds an enabled `checkov` document for both its provisioner and Helm paths.

## Phase 9 design - validate-time gaps

Two contradictions in the documents that `strata validate` passes today (Remaining Work 5).
Verified before deciding (2026-10-27):

- **A provisioner naming a disabled Integration.** `build run` does not notice:
  `resolve_integration()` takes a named document whether or not it is enabled. Deploy's step
  preflight then fails with `integration_disabled`, but only after a full build. Deploy resolves
  integrations only for provisioners named by an execution step; a `depends_on`-only provisioner
  never has its integration resolved (`test_build_run_never_resolves_integration_for_a_dependency_only_provisioner`).
- **`required: true` with `enabled: false`.** The preflight skips disabled documents (D19), so
  `required` has no effect on them. Nothing reports the contradiction.
- Precedents: a gate policy naming a disabled Integration is a validate error
  (`gate_integration_capability_missing`), and so is a remote naming one
  (`remote_integration_disabled`). `semantic_checks.py` is document-only: no PATH, no
  construction (D23).

Decisions:

- **D29 - A step's provisioner naming a disabled Integration is a validate error**,
  `provisioner_integration_disabled`, at `spec.provisioners.<i>.integration` of the workspace. It
  only applies to provisioners named by an execution step, the same set deploy resolves. A
  `depends_on`-only or unused provisioner is not reported, because nothing would ever use the
  binding (the remote rule: "a remote nothing deploys from never fails the run"). An unknown name
  is left to the generic reference walk.
- **D30 - `required: true` with `enabled: false` is a validate warning**,
  `integration_required_disabled`, at `spec.required`. It is a warning, not an error: nothing
  breaks, the flag is only inert, and disabling an integration for a while is legitimate. The
  message says why it matters ("a disabled integration is never checked, so `required` has no
  effect") and what to do (enable it or drop `required`).
- **D31 - No change to `build run` or deploy.** Validate runs before both, so the earlier error is
  enough. The deploy preflight keeps its own check for callers that skip validate.

**Tests**: D29 - a step provisioner bound to a disabled document errors at the right location; the
same document enabled passes; a `depends_on`-only provisioner bound to a disabled document passes;
an unknown name is not double-reported. D30 - warning with the location; `required: true` with
`enabled: true`, and `enabled: false` without `required`, are both silent; `strata validate` still
exits 0 with only the warning. `config/` still validates.

## Implementation Plan

- [x] **Phase 1 - version enforcement (bug fix).** As designed above: `parse_version_constraint()`,
      `IntegrationSpecModel.version` validator, `ensure_version()` on the shared parser, deploy
      preflight (deduplicated, skipped under `--smoke-test`), validate check for networked-only
      types, tests at each layer, `docs/config/integration.md`. Done 2026-10-26; also checked
      against a real Terraform 1.12.2 (`>= 1.7` and `~> 1.9` pass, `>= 9.0` fails).
- [x] **Phase 2 - readiness function.** `controllers/integration_readiness.py`:
      `assess_integration()` and the frozen `IntegrationReadiness` (D5-D6); `version_satisfies()`
      shared with `ensure_version()` (D7); deploy preflight on top of it (D8). Looking a document up
      by name or type moves to Phase 3, its first caller. Done 2026-10-26.
- [x] **Phase 3 - `integration check`** on top of Phase 2 (local + connection; undeclared
      handling); update its exit-code table, tests, how-to and `docs/config/integration.md`.
      Done 2026-10-26 (D9-D13); `registry.known_types()` added; dogfooded on `config/` against the
      real Terraform 1.12.2.
- [x] **Phase 4 - `integration list`** with the `--deployment` usage map and exit 3; agent skill row
      (drift test), docs. Done 2026-10-26 (D14-D18).
- [x] **Phase 5 - integration preflight** for `build run` and deploy, honouring `spec.required`
      (D19-D24); `find_provisioner()` moved to `integration_resolution.py` to avoid an import cycle.
      Done 2026-10-27.
- [x] **Phase 6 - `trivy`** declarable (`TrivyIntegration`); `CveScannerIntegration` takes a
      config and a fixed backend; the CVE gate uses a declared Trivy (D25-D27). Done 2026-10-27.
- [x] **Phase 7 - `grype`** declarable (`GrypeIntegration`, version via `grype version`); chosen
      after a declared Trivy. Done 2026-10-27.
- [x] **Phase 8 - `checkov`** registered (out of the v1 list); the Checkov gate binds a declared
      document on both its provisioner and Helm paths. Done 2026-10-27.
- [x] **Phase 9 - validate-time gaps** (D29-D31): `provisioner_integration_disabled` (error) and
      `integration_required_disabled` (warning) in `semantic_checks.py`. Done 2026-10-27.

## Remaining Work / Open Questions

1. ~~**Version for networked integrations.**~~ Resolved in Phase 1 design D4: a validate error.
2. ~~**Build-time tools without documents.**~~ Resolved in Phases 6-8: `git`, `trivy`, `grype` and
   `checkov` can all be declared, required and version-pinned; undeclared, they keep today's PATH
   behaviour.
3. ~~**Usage map scope.**~~ Resolved in Phase 4 design D16 (deploy-time bindings only).
4. ~~**`--missing` filters.**~~ Resolved in D18: none; JSON plus `jq` covers them.
5. **Validate-time gaps** found on the way: ~~a provisioner naming a disabled Integration document~~
   and ~~`required: true` with `enabled: false`~~ (both Phase 9); still open: v1 types not yet
   ported reported with the generic "unknown type" message by `integration check`.
6. **Graduation:** ~~an ADR for the readiness model~~ (ADR-0050, ADR-0051, 2026-10-27); this doc to
   `docs/design/` once 5 is decided.

## Changelog

- 2026-10-27: Phase 9 built: `strata validate` reports a step's provisioner bound to a disabled
  Integration (`provisioner_integration_disabled`, error; `depends_on`-only provisioners are not
  reported, as deploy never resolves them) and `required: true` on a disabled one
  (`integration_required_disabled`, warning). No new findings on `config/`, `.v2-haven` or
  `.v2-cfg`; three mutants, all killed.

- 2026-10-27: Phases 6-8 built: `trivy`, `grype` and `checkov` are registered `scanner` types. A
  declared, enabled document is what the CVE gate (Trivy first, then Grype) and the Checkov gate
  use - never silently swapped for another tool - and it gets `spec.required`/`spec.version`/
  `spec.command` like any local tool; undeclared, the gates keep their PATH auto-detection.
- 2026-10-27: Phase 5 built: `integration_preflight()` runs first in `build run` and in deploy
  (after the step preflight, without repeating it); `spec.required` finally means something - a
  required integration that is not ready is an error, any other a warning; `integration list` uses
  the same rule (exit 3 for a required one even without `--deployment`).
- 2026-10-27: Review fixes R1-R11 built: `git` registered as a local tool (`integrations/git_cli.py`),
  `ensure_version()` removed, `registered` made truthful, no warning for disabled unused documents,
  networked types without a connection check say "nothing to verify", connection failures keep the
  readiness report (exit 1/3 via diagnostics), one JSON shape, a clear ambiguous-type message, no
  more hidden rows on a name clash, and the missing tests (a mutation check caught all four
  injected faults).
- 2026-10-26: Phase 4 built: `strata integration list [--deployment D]` (D14-D18) and
  `list_integrations()`; readiness gained `registered`/`credential_holder`, so `integration check
  git-customers` now exits 0 (credential holder) instead of a false 3. Dogfooded on `config/`.
- 2026-10-26: Phase 3 built: `strata integration check` runs local readiness, then the connection
  check where the type has one; NAME may be a type; an unknown name lists declared names and known
  types. Exit-code changes: a type without a connection check exits 0 (was 2), a disabled integration
  exits 3 (was 2).
- 2026-10-26: Phase 2 built: `assess_integration()` is the one offline readiness check; deploy
  preflight uses it. Deliberate changes: a provisioner naming a disabled Integration now fails
  preflight (`integration_disabled`), an unavailable tool is reported once rather than per step
  (`integration_unavailable`), and the cache is keyed by name and command (script integrations).
  Full suite green (3401 passed).
- 2026-10-26: Phase 1 built: `spec.version` is enforced at deploy preflight (once per integration,
  code `integration_version_mismatch`), Terraform's `~>` accepted, an unparseable constraint fails
  at load, and `spec.version` on a networked-only integration is a validate error
  (`integration_version_unenforceable`). Full suite green (3390 passed).
- 2026-10-26: Created from the security-gates code review (finding 4, "undeclared integration") and
  the comparison with v1's `tools status|check`; records the unenforced `spec.version` bug.
