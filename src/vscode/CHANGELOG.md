# Changelog — Strata VS Code Extension

All notable changes to this extension are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project follows the same versioning as
the `strata` CLI it wraps. For 1.x, see the
[v1 changelog](https://github.com/huybrechtsxyz/strata/blob/v1.11.2/src/vscode/CHANGELOG.md).

## [2.0.0] - Unreleased

The extension rebuilt for strata v2. It replaces 1.x on the same Marketplace listing.

### Changed (breaking)

- **Needs a strata v2 solution and CLI.** Activates on `strata.yaml` instead of
  `.strata/solution.json`, and runs the `strata` CLI 2.x.
- **1.x features not carried over:** the Workspace, Files, Repositories, Environments, Tools and
  Audit views; Build and Deploy commands; CodeLens; the `strata` task type; diagrams; the help
  pane; and the `@strata` chat participant. Build, deploy and audit run from the CLI; scaffolded
  agent skills replace the chat participant. To keep 1.x, install version 1.11.2 and turn off
  auto-update for it (see the README's "Upgrading from v1").

### Added

- **Get started walkthrough** (Help > Welcome): install the CLI, open a solution, validate,
  explore the graph, and turn on autocomplete and agent skills.
- **Update Solution Files command** (`strata.updateSolution`) — runs `strata sln update` after a
  modal confirmation, refreshing `.strata/` (including the JSON Schemas behind YAML autocomplete) and
  the strata agent skill (`.claude/skills/strata/`, unless its generated marker was removed), and
  creating any missing `.vscode/`/`.github/`/`.claude/` scaffold files. Other existing files are
  never overwritten. The changed files are listed in a new **Strata** output channel.
- **Workspace `.venv` auto-detection** — when `strata.cliPath` is left at its default (`"strata"`),
  the CLI wrapper now checks for `.venv`/`venv` (`Scripts/strata.exe` on Windows, `bin/strata`
  elsewhere) in the workspace folder before falling back to `PATH`. An explicit `strata.cliPath`
  override still always wins.
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
- **Deployment selector** — a status bar item and **Strata: Select Deployment**, backed by
  `strata deploy list`; the choice is kept per workspace and scopes the two views below.
- **Integrations view** — `strata integration list`: readiness icon, type and tool version, the
  problems and what uses each integration; an inline action runs `strata integration check`.
- **Policies view** — `strata policy list`: phase, enforcement and last result per policy;
  inline actions run `strata policy check` (one, or all from the view title) and
  `strata policy findings`, whose findings appear as child rows (`[allowlisted]`/`[unrated]`
  marked, issue links clickable, 10 per target).
- **`strata.cliPath`** / **`strata.validateOnSave`** settings.
