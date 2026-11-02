# VS Code Integrations and Policies views - Work

- Status: done
- Last updated: 2026-10-27

## Overview

The extension (`src/vscode`) shows the solution's reference graph and validate diagnostics. The
security-gates and integration-readiness work added CLI commands whose answers belong next to the
code: which integrations a deployment uses and whether this machine can run them
(`strata integration list|check`), and which policies a deployment has and whether they pass
(`strata policy list|check|findings`). This adds two sidebar views on top of those commands.

## Current Design / Progress

Verified before deciding (2026-10-27):

- The extension renders CLI output only (its own rule, `graphViewProvider.ts`): every call is
  `strata <command> --output json`, parsed from the envelope `{ok, command, version, context,
  summary, diagnostics, data}`; exit 2/3 still print the envelope.
- `integration list [--deployment D] -o json` returns `data.integrations[]` with `name`, `type`,
  `declared`, `enabled`, `required`, `ok`, `problems[]`, `installed_version`, `required_version`,
  `connection_check`, `used_for[]`, but **no file path**.
- `policy list D -o json` returns `data.policies[]` (name, phase, enforcement, backing, check,
  findings, last_result); `policy check` and `policy findings` exist for every policy but the
  release gate's findings.
- **No command lists deployments.** `strata graph` names deployment documents but cannot tell a
  partial (non-deployable) one from a real one.
- The extension has no tests (deliberate when scaffolded).

Decisions:

- **V1 - CLI first, two small additions.** `strata deploy list` (name, workspace, environments,
  partial, source; resolved through `extends` like `build run`, partials listed and marked) and a
  `source` field on `integration list` rows (`null` for type defaults). Paths are absolute, as
  `strata path` reports them. `strata path list deployment` already lists the files but cannot mark
  partials or show a workspace inherited through `extends`. Both additions are useful outside the
  extension (scripts, CI) and keep the extension a renderer.
- **V2 - One selected deployment, shared.** A status-bar item and `Strata: Select Deployment`
  (quick pick of non-partial deployments); stored in `workspaceState`; both views refresh on change.
  With none selected the Integrations view lists everything (no `--deployment`) and the Policies
  view says "Select a deployment".
- **V3 - Integrations view.** One row per integration: icon from `ok` / `enabled` (pass, error,
  disabled), description `type` and version; children are `used_for` entries and problems.
  Clicking a row opens its document. Inline action **Check connection** runs
  `integration check NAME [--deployment D]` and shows the result.
- **V4 - Policies view.** One row per configured policy: icon from `last_result` (passed, failed,
  not run), description `phase, enforcement`, tooltip `backing`. Actions: **Check** (one policy, or
  all from the view title) and **Show findings** (only where `findings` is true).
- **V5 - Findings as tree rows**, not a webview: the policy row expands into one child per finding
  (CVE, Checkov check, or image issue), with `[allowlisted]` / `[unrated]` markers and the link
  when there is one; a 10-row limit per target with a "+N more" row, as the console does.
- **V6 - Long commands run with progress.** Checks and findings can take 10+ seconds (Checkov,
  release gate polling): `window.withProgress` in the notification area; results go to the Strata
  output channel, failures to an error message naming the first error.
- **V7 - Tests stay in Python.** The CLI additions get pytest coverage; the extension keeps
  `tsc --strict` and a manual check against `config/` (`.v2-haven`), as for the Graph view.
- **V8 - `deploy list` as the deployment matrix.** Scripts and CI need more than the extension: each row
  also carries `extends` (the declared parent), `tenant` and `version` (both resolved through `extends`),
  and `ring`, `order`, `wave` from the version document's `spec.promotion` (all `null` when there is no
  version or it is untagged). Filters `--workspace`, `--tenant`, `--version`, `--ring` and `--deployable`
  (drops partials) combine with AND and compare resolved values. A filter naming something that does not
  exist (workspace, tenant, version, or a ring no version declares) exits 2 instead of returning an empty
  list, so a typo cannot pass a CI matrix with nothing in it. Still schema-only: a dangling `version`
  shows its name with a null ring, and `strata validate` reports it. The extension keeps its own
  non-partial filter; the new fields are additive.

## Implementation Plan

- [x] **Phase 1 - CLI.** `strata deploy list` (command, agent-skill row, tests); `source` on
      `integration list` rows (test).
- [x] **Phase 2 - Deployment selector and Integrations view** (V2, V3).
- [x] **Phase 3 - Policies view with Check and Show findings** (V4-V6).

## Remaining Work / Open Questions

1. Release gate findings: `policy findings` does not support it (no itemised API); the row shows
   only its last result.
2. Not yet tried in a real Extension Development Host with policies configured: `config/`
   has none, so Check / Show findings were exercised only through the stubbed run below.

## Changelog

- 2026-10-27: Created; design V1-V7.
- 2026-10-27: Phase 1 - `strata deployment list` (`commands/deployment_command.py`,
  `list_deployments()` in `deployment_resolution.py`), `source` on `integration list` rows;
  5 new tests.
- 2026-10-27: Phases 2-3 - `providers/deploymentSelector.ts`, `integrationsViewProvider.ts`,
  `policiesViewProvider.ts`, client methods, `package.json` views/commands/menus, README and
  extension CHANGELOG. `tsc --strict` clean. Verified by loading the compiled providers with a
  stubbed `vscode` module against `config/`: deployments (partials marked), Integrations with and
  without `prd-deployment` (`used_for` children appear), Policies ("Select a deployment" / "No
  policies configured"), and `findingGroups()` on all three findings shapes (cap, markers, link).
- 2026-10-30: `strata deployment list` moved to `strata deploy list` (`commands/deploy_list_command.py`);
  the `deployment` group is gone. The entries above keep the original name.
- 2026-10-30: V8 - `deploy list` gains `extends`, `tenant`, `version`, `ring`/`order`/`wave`, the five
  filters and the TENANT/VERSION/RING console columns (`list_deployments()` in
  `deployment_resolution.py`, `commands/deploy_list_command.py`); `DeploymentRow` in the extension
  mirrors the new fields.
