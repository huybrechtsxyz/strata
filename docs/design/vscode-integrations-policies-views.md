# VS Code Integrations and Policies views

- Status: current — implemented (2026-10-27, `deploy list` extended 2026-10-30). The extension shows a
  deployment selector, an Integrations view and a Policies view, all rendering CLI output.

## Overview

The VS Code extension (`src/vscode`) shows the solution's reference graph and validate diagnostics. The
security-gates and integration-readiness work added CLI commands whose answers belong next to the code:
which integrations a deployment uses and whether this machine can run them (`strata integration
list|check`), and which policies a deployment has and whether they pass (`strata policy
list|check|findings`). Two sidebar views sit on top of those commands, and a deployment selector scopes
them.

The extension stays a renderer. It calls `strata <command> --output json`, parses the envelope
`{ok, command, version, context, summary, diagnostics, data}` (exit 2 and 3 still print the envelope),
and holds no rules of its own beyond display.

## Current Design

Facts the design rests on (checked against the CLI at 2026-10-27):

- `integration list [--deployment D] -o json` returns `data.integrations[]` with `name`, `type`,
  `declared`, `enabled`, `required`, `ok`, `problems[]`, `installed_version`, `required_version`,
  `connection_check`, `used_for[]`, and no file path.
- `policy list D -o json` returns `data.policies[]` (name, phase, enforcement, backing, check, findings,
  last_result). `policy check` and `policy findings` exist for every policy except the release gate's
  findings.
- No command listed deployments before `strata deploy list`, and `strata graph` could not tell a partial
  (non-deployable) document from a real one.
- The extension has no tests by design. Its CLI additions are covered by pytest.

### Decisions

- **V1 — CLI first, small additions.** `strata deploy list` gives name, workspace, environments, partial
  and source, resolved through `extends` as `build run` resolves them; partials are listed and marked.
  `integration list` rows carry a `source` field (`null` for type defaults). Paths are absolute, as
  `strata path` reports them. `strata path list deployment` lists the files but cannot mark partials or
  show a workspace inherited through `extends`. Both additions are useful to scripts and CI, not only to
  the extension.
- **V2 — One selected deployment, shared.** A status-bar item and `Strata: Select Deployment` (quick pick
  of non-partial deployments) store the choice in `workspaceState`. Both views refresh on change. With no
  selection the Integrations view lists everything (no `--deployment`) and the Policies view says "Select a
  deployment".
- **V3 — Integrations view.** One row per integration. The icon comes from `ok` and `enabled` (pass, error,
  disabled); the description is the `type` and version; children are the `used_for` entries and problems.
  Clicking a row opens its document. The inline action **Check connection** runs
  `integration check NAME [--deployment D]` and shows the result.
- **V4 — Policies view.** One row per configured policy. The icon comes from `last_result` (passed, failed,
  not run); the description is `phase, enforcement`; the tooltip is `backing`. Actions: **Check** (one
  policy, or all from the view title) and **Show findings** (only where `findings` is true).
- **V5 — Findings as tree rows, not a webview.** A policy row expands into one child per finding (CVE,
  Checkov check, or image issue), with `[allowlisted]` and `[unrated]` markers and the link when there is
  one. A target shows at most 10 rows and then a "+N more" row, as the console does.
- **V6 — Long commands run with progress.** Checks and findings can take ten seconds or more (Checkov,
  release gate polling). They run under `window.withProgress`; results go to the Strata output channel and
  failures to an error message naming the first error.
- **V7 — Tests stay in Python.** The CLI additions get pytest coverage. The extension keeps `tsc --strict`
  and a manual check against `config/`, as the Graph view does.
- **V8 — `deploy list` as the deployment matrix.** Scripts and CI need more than the extension, so each row
  also carries `extends` (the declared parent), `tenant` and `version` (both resolved through `extends`),
  and `ring`, `order` and `wave` from the version document's `spec.promotion` (all `null` when there is no
  version or it is untagged). The filters `--workspace`, `--tenant`, `--version`, `--ring` and
  `--deployable` (drops partials) combine with AND and compare resolved values. A filter that names
  something which does not exist exits 2 instead of returning an empty list, so a typo cannot pass a CI
  matrix with nothing in it. A dangling `version` shows its name with a null ring; `strata validate`
  reports it. The extension keeps its own non-partial filter; the new fields are additive.

## Known Limits

- Release gate findings are not itemised: `policy findings` has no API for the release gate, so its row
  shows only the last result.
- The views have not been exercised in a live Extension Development Host with policies configured. `config/`
  declares none, so Check and Show findings were verified against a stubbed `vscode` module only.

## History

- Views read the CLI's JSON envelope rather than re-deriving anything. Keeping the extension a renderer
  means every rule (partials, `extends`, policy results) is tested once, in Python.
- A deployment listing was added to the CLI rather than to the extension, because scripts and CI need it
  too. The first name was `strata deployment list`; the command group was dropped and the verb became
  `strata deploy list` (2026-10-30).
- The matrix fields (V8) were added after the views shipped, for the same reason: CI needs the same answer
  the extension gives.
