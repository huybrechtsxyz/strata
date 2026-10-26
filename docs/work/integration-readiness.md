# Integration readiness - Work

- Status: in-progress (Phases 1-4 done; remaining open questions only)
- Last updated: 2026-10-26

## Overview

Two separate questions can be asked of any Integration document, and strata currently answers
neither of them completely:

| Question                 | Checks                                                                      | Needs                                   |
| ------------------------ | --------------------------------------------------------------------------- | --------------------------------------- |
| Is it usable **here**?   | type is registered, `spec.enabled`, tool on PATH, version vs `spec.version` | nothing - offline, no credentials       |
| Is it reachable **now**? | credentials resolve and the platform accepts them                           | a deployment (for credentials), network |

v1 answered the first with `strata tools status|check`; v2's `strata integration check` (added for
security gates, [security-gates-design.md](security-gates-design.md) D7) answers only the second.
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

## Remaining Work / Open Questions

1. ~~**Version for networked integrations.**~~ Resolved in Phase 1 design D4: a validate error.
2. **Build-time tools.** `build run` uses scanners (trivy/grype/checkov) that are not Integration
   documents today; should `integration list` show them, and should `build run` enforce versions?
   Same question for build-time module types (D16 left them out).
3. ~~**Usage map scope.**~~ Resolved in Phase 4 design D16 (deploy-time bindings only).
4. ~~**`--missing` filters.**~~ Resolved in D18: none; JSON plus `jq` covers them.
5. **Validate-time gaps** found on the way: a provisioner naming a disabled Integration document
   (caught only at deploy preflight since Phase 2), and v1 types not yet ported reported with the
   generic "unknown type" message by `integration check`.
6. **Graduation:** once 2 and 5 are decided, an ADR for the readiness model and this doc to
   `docs/design/`.

## Changelog

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
