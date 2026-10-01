# Changelog — Strata VS Code Extension

All notable changes to this extension are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project follows the same versioning as
the `strata` CLI it wraps.

## [0.0.1] — 2026-10-01

Initial scaffold. Wraps v2's CLI surface as it exists today — `validate`, `graph`, `version` — not
a port of [v1's extension](https://github.com/huybrechtsxyz/strata/tree/main/src/vscode); see
[README.md § Scope](README.md#scope) for what that leaves out.

### Added

- **Validation diagnostics** — `strata validate` runs on save (debounced) and on
  **Strata: Validate Solution**; findings are grouped by source file and shown in the Problems
  panel, with the reported field path resolved to a line via a dotted-path heuristic
  (`fieldLocation.ts`).
- **Graph view** — a "Graph" tree view (Strata Activity Bar container) backed by `strata graph`:
  one entry per document, expanding to the references it declares; unresolved references are
  flagged. **Strata: Refresh Graph** re-runs it; clicking a reference opens its declaring file at
  that field via **Strata: Open Reference Location**.
- **Status bar** — shows the CLI version once resolved, or the reason it couldn't be (CLI not
  found, or any other failure), from a single `strata version` call.
- **`strata.cliPath`** / **`strata.validateOnSave`** settings.
