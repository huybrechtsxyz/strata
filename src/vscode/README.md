# Strata VS Code Extension

Editor integration for the Strata v2 CLI: validates a solution as you work and lets you explore
its cross-document reference graph without leaving VS Code.

This is a from-scratch v2 extension, not a port of [v1's `src/vscode`](https://github.com/huybrechtsxyz/strata/tree/main/src/vscode) extension — it wraps
only the CLI surface v2 actually has today. See [Scope](#scope) for what that does (and does not
yet) include.

## Features

- **Validate** — runs `strata validate` against the whole solution and reports findings in the
  editor's Problems panel, grouped by the file each one came from. Re-runs automatically on save.
- **Graph** — a "Graph" view (Strata icon in the Activity Bar) backed by `strata graph`, showing
  every cross-document reference: one entry per document, expanding to the references it
  declares. A reference that does not resolve is marked **unresolved**. Clicking a reference opens
  the declaring file at that field.
- **Status bar** — shows the `strata` CLI version, or why it isn't available.

## Requirements

- VS Code 1.90 or later.
- The `strata` CLI available to run — either on `PATH`, or reachable via
  [`strata.cliPath`](#configuration) (e.g. `"uv run strata"` for a `uv`-managed checkout — see
  [Get Started](../../.github/CONTRIBUTING.md#get-started) in this repository's own contributing
  guide).
- A solution: a `strata.yaml` at or above the open workspace folder (see
  [the Solution document](../../docs/config/solution.md) and
  [ADR-0015](../../docs/decisions/0015-solution-manifest-and-document-discovery.md)). The
  extension activates when one is present.

## Installation

Not yet published to the Marketplace. Run it from source:

```powershell
cd src/vscode
npm install
npm run compile
```

Then open this repository in VS Code and press **F5** (uses the root
[`.vscode/launch.json`](../../.vscode/launch.json) "Run VS Code Extension" configuration) to start
an Extension Development Host with it loaded. See [DEVELOPMENT.md](DEVELOPMENT.md) for the full
workflow, including packaging a `.vsix` for manual installation.

## Quick Start

1. Open a folder containing a `strata.yaml` (or a subdirectory of one) — the extension activates
   automatically.
2. Check the status bar: `$(cloud) strata <version>` means the CLI was found and ran. `$(error)
   strata` means it was not — see [Troubleshooting](#troubleshooting).
3. Save any `.yaml` file to validate the solution; problems appear in the **Problems** panel
   (`Ctrl+Shift+M`).
4. Open the **Strata** view in the Activity Bar to browse the reference graph.

## Commands

| Command                    | Title                           | Purpose                                                     |
| -------------------------- | ------------------------------- | ----------------------------------------------------------- |
| `strata.validate`          | Strata: Validate Solution       | Validate now and show a summary notification.               |
| `strata.refreshGraph`      | Strata: Refresh Graph           | Re-run `strata graph` and reload the Graph view.            |
| `strata.openGraphLocation` | Strata: Open Reference Location | Internal — bound to clicking a reference in the Graph view. |

## Views

### Graph

One top-level entry per document that declares at least one reference (`kind/name`, e.g.
`workspace/main`), expanding to its references (`location → target_kind/target_name`). An
unresolved reference shows a warning icon and an "unresolved" description — this view never fails
validation itself, it only shows the shape of the solution; `strata validate` is what decides
pass/fail.

Use the refresh icon in the view's title bar, or run **Strata: Refresh Graph**, after changes the
file watcher doesn't already know to re-check (the view does not watch the filesystem yet — see
[Scope](#scope)).

## Configuration

| Setting                 | Type    | Default    | Description                                                                |
| ----------------------- | ------- | ---------- | -------------------------------------------------------------------------- |
| `strata.cliPath`        | string  | `"strata"` | Path to the `strata` CLI. Use `"uv run strata"` for a uv-managed checkout. |
| `strata.validateOnSave` | boolean | `true`     | Re-validate the whole solution whenever a YAML file is saved.              |

## Troubleshooting

### Status bar shows `$(error) strata`

The CLI could not be run. Hover the status bar item for the exact error. Most commonly:

- `strata` is not on `PATH` — set `strata.cliPath`, e.g. `"uv run strata"` or an absolute path to
  the executable.
- The configured command exists but isn't the `strata` CLI — check `strata.cliPath` is spelled
  correctly.

### The extension never activates

It activates on `workspaceContains:strata.yaml` — confirm the open workspace folder contains one
at its root or a subdirectory (`strata` document discovery itself walks *upward* from a path to
find one, per ADR-0015, but VS Code's own activation check only looks *downward* from the
workspace root, so a `strata.yaml` in a parent directory of the opened folder will not trigger
activation).

### Graph view is empty

- Run **Strata: Refresh Graph** — it does not refresh automatically yet.
- Check the solution actually has cross-document references (a lone `provider.yaml` with nothing
  referencing it produces an empty graph; this is correct, not a bug).
- If `strata graph` itself fails, a warning notification explains why — the same CLI error the
  status bar hover shows.

### Validation looks stale

Saves are debounced by 300ms and run one solution-wide `strata validate` — if several files are
saved close together, only the last run's results apply (correct: it always reflects the whole
solution, not just the file you last touched). Run **Strata: Validate Solution** to force an
immediate re-check.

## Scope

This extension intentionally covers only what v2's CLI has today (`validate`, `graph`, `version`).
For context, [v1's extension](https://github.com/huybrechtsxyz/strata/tree/main/src/vscode) also
had a workspace explorer, build/deploy commands, drift detection, an audit trail view, a chat
participant, SBOM generation, and more — none of that exists here because the corresponding v2 CLI
commands (`build`, `deploy` wiring, `audit`, etc.) either don't exist yet or aren't wired into the
extension yet. Nothing here references those features; if you're looking for them, they aren't
silently broken — they simply haven't been built.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for setup, building, debugging, and project structure.
Changes are tracked in [CHANGELOG.md](CHANGELOG.md).
