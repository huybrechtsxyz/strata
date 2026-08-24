# Changelog — Strata VS Code Extension

All notable changes to the Strata VS Code extension are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- **Diagram preview pane (ADR-0034, Phase 1 + 2)** — `Strata: Show Dependency Graph`, `Strata: Show Infrastructure Topology`, and `Strata: Preview Diagram` (for any `kind: diagram` YAML, including custom definitions under `.strata/diagrams/`) render `strata diagram show` output in a webview panel, live-reloading on save. Clicking a node resolves its `strata://` URI via `strata diagram resolve` and jumps to the exact file and line — the CLI is the single source of truth for both the graph and the click target, replacing the old `dependencyGraphProvider.ts`, which re-implemented `@repo/path` scanning in TypeScript. Diagram colors are re-themed to the active VS Code theme (`--vscode-charts-*` variables) instead of the CLI's fixed light-theme hex, matching every status/severity/taxonomy token in `design_tokens.py`. Hovering a node shows its status/kind classDef name; moving the cursor in an open YAML file highlights the corresponding node in the diagram (resolved once per render via `strata diagram resolve`, capped at 150 nodes).
- **Diagrams sidebar + `/diagram` chat command (ADR-0034, Phase 3)** — a new "Diagrams" tree view (`strataDiagrams`) lists `strata diagram list` output (built-ins + workspace definitions), grouped by source, with a text filter (`strata.filterDiagrams`) and live refresh when `.strata/diagrams/*.yaml` changes; selecting an entry opens it in the preview pane. `@strata /diagram` (or `/diagram list`) browses the same catalog in chat with buttons per entry; `/diagram show <name>` opens one directly.
- **Diagram Builder + AI generation (ADR-0034, Phase 4)** — `Strata: New Diagram (Builder)` and per-entry "Edit in Diagram Builder" open a webview form (sources, layout, style) that live-previews through the existing preview pane and validates before saving to `.strata/diagrams/`; a hand-written-template diagram is detected and declined with a pointer to `--print-template`. `@strata /diagram create <description>` generates a definition from natural language (validated with a one-retry repair loop, never a silently-broken diagram) and opens it in the Builder. "Copy Mermaid" exports the rendered source to the clipboard; SVG/PNG export is not yet available (needs mermaid-cli — tracked in ADR-0034 Phase 1).

- **Help pane** (`strataHelp` tree view) — context-aware sidebar that detects the `kind:` of the active YAML file and shows suggested help topics, quick-action buttons (Validate, Schema, Guide), and a full A–Z topic list
  - Workspace override support: custom help files placed in `.strata/help/*.md` are discovered automatically and shown in a "Workspace" section with override indicators; file system watcher keeps the list live
  - 55 help topics bundled in the extension (`resources/help/`) so content is available without requiring the system CLI
  - All 12 platform kinds mapped to relevant suggested topics

### Fixed

- SIEM help topics (`sentinel`, `elk`, `otel`, `splunk`) now resolve from bundled extension resources; previously the file lookup always failed due to a `siem_` filename prefix mismatch and fell through to CLI fallback
- `deployment` kind now suggests its own help topic alongside related topics in the Help pane
- **Pending Work panel** (`strataWorkItems` tree view) — shows pending work items with type icons, deployment name, inline ✅ Approve / ❌ Reject buttons, and a badge count on the activity bar when items are waiting; auto-polls every 60 seconds with configurable `strata.workItemPollIntervalSeconds` setting
- **`@strata /approvals` chat command** — lists pending work items with plan summary, cost delta, CVE count, and AI risk; inline approve/reject buttons; follow-up suggestions
- **`strata.approveWorkItem`** / **`strata.rejectWorkItem`** commands — prompt for note/reason, call CLI, refresh panel
- **`strata.showWorkItem`** command — opens Output Channel with full work-item detail (type, status, deployment, commit, context)
- **`strata.workItemPollIntervalSeconds`** setting (default: 60, min: 10)

---

## [1.0.0] — 2026-07-08

### Added

- **Values Inspector** (`strataValues` tree view) — new sidebar panel showing all resolved deployment values with secret masking (`***`), source tracking, resolved/unresolved indicators, and copy-key-to-clipboard
- **Lock Status & Release** — `strata.lockStatus` command shows live lock holder, TTL, and backend; `strata.releaseLock` force-releases with confirmation; 🔒 badge on locked deployments in Environments panel
- **Drift Detection** — `strata.envDrift` runs drift analysis; ⚠ drift badge shown on deployment items after detection; `/drift` chat command with action button
- **SBOM Generation** — `strata.buildSbom` generates CycloneDX SBOM with progress notification and offers to open `sbom.json`; SBOM task added to auto-discovered VS Code tasks
- **Stage-targeted Deploy** — `strata.deployStage` deploys a single named stage with optional dry-run; right-click context menu on stage items in Environments panel; `/stage` chat command
- **Repository Write Operations** — sync repo (spinner during operation), remove repo (confirmation dialog), add repo (name + path input); right-click context menus on repository items
- **Audit Filter & Limit** — `strata.auditFilter` cycles all / success-only / failures-only; `strata.auditSetLimit` configures entry count (5–200, default 20); summary header shows current filter state
- **Workspace Panel fully implemented** — `strataWorkspace` tree view now shows: active profile with indicator, all repositories with clone status, document paths (click to open), tool availability with pass/fail icons (was entirely stubbed with TODO comments)
- **Chat Participant (`@strata`) action buttons** — `/build` and `/deploy` now render clickable ▶ Dry Run / ⚡ Full Build / 🚀 Full Deploy buttons that execute the corresponding commands; `/stage [file] <name>` for stage-targeted deploys; `/values` displays resolved values table inline; `/drift` renders drift detection button
- **Editor context menus** — Show Values, Generate SBOM, Lock Status actions on `.yaml` files
- **13 new commands**: `strata.deployStage`, `strata.lockStatus`, `strata.releaseLock`, `strata.showValues`, `strata.copyValueKey`, `strata.buildSbom`, `strata.syncRepo`, `strata.addRepo`, `strata.removeRepo`, `strata.auditFilter`, `strata.auditSetLimit`
- **`StrataClient`** — 9 new CLI wrapper methods: `syncRepo`, `addRepo`, `removeRepo`, `getLockStatus`, `releaseLock`, `runDrift`, `getValues`, `generateSbom`; `getAuditChanges` enhanced with optional `stage` filter

### Changed

- Environments panel — deployment items show lock (🔒) and drift (⚠ drift) badges; stage items carry `filePath` and `stageName` for context menu targeting
- Repositories panel — items now have sync/remove context menus; spinner badge during sync; open-folder command on local path
- Audit panel — entries limited by configurable count; filter state shown in summary header item
- `yaml.schemas` workspace setting now uses a single `strata.json` umbrella schema (kind-dispatched) instead of 12 folder-path-based entries

---

## [0.16.1] — 2026-01-15

### Added

- Tree view decorations (badges) for files showing validation status (✅ ⚠️ ❌)
- **Files View** now shows all YAML files with validation status badges
- Right-click context menu on files: "Open", "Validate", "Show Status"
- **Code Lens** for YAML documents: inline "Validate", "Build", "Deploy" actions
- Configuration option `strata.showCodeLens` (default: true)
- Configuration option `strata.showFileDecorations` (default: true)
- New task type: `strata` for CI/CD pipeline integration

### Changed

- Extension now activates for *any* workspace (not just those with `.strata/`)
- Clearer error messages when CLI is not found
- Status bar now shows profile name when available

### Fixed

- Schema export now includes all 15 model types (was missing some)
- Dependency graph now correctly handles circular references
- Fixed workspace view refresh not updating on file changes

---

## [0.16.0] — 2025-12-20

### Added

- **Chat Participant**: New `@strata` participant for asking questions about your workspace
- **Dependency Graph**: Visualize YAML file references and dependencies
- **Environments View**: Track deployments and their status
- **Audit Trail View**: View deployment history with changeset details
- New commands:
  - `strata.showDependencyGraph` — visualize dependencies
  - `strata.envStatus` — check deployment status
  - `strata.envDrift` — detect infrastructure drift
  - `strata.envDoctor` — run health diagnostics
  - `strata.auditChanges` — show recent changes
  - `strata.auditExport` — export audit trail
  - `strata.auditResend` — resend entries to audit sinks

### Changed

- **Workspace View** refactored: now shows readiness phases (8-phase checklist)
- **Tools View** enhanced: now shows terraform, ansible, git, docker, uv versions
- Build and deploy commands now support `--stage` flag for per-stage operations
- Improved status bar: displays active profile name and workspace health

### Fixed

- Validation diagnostics now update correctly on file save
- Fixed issue where validation errors were not cleared between runs
- Schema export is now more robust (handles edge cases)

---

## [0.15.2] — 2025-11-10

### Added

- Getting-started walkthrough with 3 steps
- Walkthrough markdown resources in `resources/walkthrough/`
- New command: `strata.exportSchemas` — export and wire JSON schemas

### Changed

- Package.json now declares walkthroughs (auto-shown on first install)
- Improved walkthrough UX with links to commands

### Fixed

- Schema wiring now persists correctly across sessions

---

## [0.15.1] — 2025-10-30

### Added

- Configuration option `strata.defaultProfile` to auto-activate a profile on workspace open
- Configuration option `strata.validateOnType` (debounced, 1.5s delay)
- Snippet provider: auto-complete YAML scaffolding for Strata kinds

### Changed

- Validation debounce now configurable (internal, not user-facing)
- Improved TypeScript compilation with stricter tsconfig

### Fixed

- Fixed cross-reference provider not working with Windows paths

---

## [0.15.0] — 2025-10-15

### Added

- **Repositories View**: Browse remote repositories with git metadata
  - Shows latest release tag (v*.*.* pattern)
  - Shows latest quality-gate tag
  - Displays repository path, git origin, and tag metadata
- New commands:
  - `strata.switchProfile` — activate a different profile
  - `strata.openConsole` — launch interactive console in terminal
- Configuration option `strata.cliPath` to specify custom CLI path

### Changed

- TreeView icons now use VS Code's built-in icon library
- Improved performance: cached CLI responses, reduced network calls
- Status bar layout redesigned for clarity

### Fixed

- Repository status now updates correctly when remotes change

---

## [0.14.0] — 2025-09-20

### Added

- **Tools View**: Check availability of external tools (terraform, ansible, git, docker)
- Real-time status indicator for tool availability
- New commands:
  - `strata.buildDryRun` — preview changes without applying
  - `strata.deployDryRun` — preview deployment without applying
  - `strata.buildRun` — build artifacts
  - `strata.deployRun` — apply deployment
- Configuration options:
  - `strata.validateOnSave` (default: true)
  - `strata.showStatusBar` (default: true)

### Fixed

- File path resolution now works correctly on Windows
- CLI invocation now properly escapes paths with spaces

---

## [0.13.0] — 2025-08-30

### Added

- **Files View**: List all YAML files in workspace
- **Workspace View**: Show workspace health and readiness
- Diagnostics provider: inline YAML validation errors
- Code lens provider: quick actions above YAML documents
- Real-time validation: errors update as you type (with debounce)
- Cross-reference resolution: detect @repo/path references
- Status bar integration: show workspace status and active profile

### Changed

- Initial extension structure with proper separation of concerns
- Improved error handling and user-facing messages

### Fixed

- Initial release: all baseline features working

---

## Versioning

Extension versions align with the Strata CLI version (e.g., v0.16.1 extension works with v0.16.1 CLI).

The extension is backward-compatible with older CLI versions where possible, but features may be limited based on CLI capabilities.

---

## Future Roadmap

### Planned for v0.17

- [ ] Progressive secret scaffolding during initialization
- [ ] Advanced SBOM analysis and dependency scanning UI
- [ ] Deployment history timeline visualization
- [ ] Multi-workspace support improvements

### Deferred (Post-v1.0)

- [ ] CEF syslog format for SIEM integration (ADR-0022 Phase 2)
- [ ] Template marketplace / community templates (ADR-0014 Phase 5)
- [ ] Advanced policy simulation UI (ADR-0006 Phase 2)
- [ ] Promotion strategies UI (ADR-0011)
- [ ] Infrastructure drift detection UI (ADR-0008)

---

## Archive

### v0.13–v0.15

These versions are archived. See git tags for historical details.

---

## Contributing

To contribute to the Strata VS Code extension:

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes and add tests
4. Run linting and tests: `npm run lint && npm run test`
5. Commit with a descriptive message following [Conventional Commits](https://www.conventionalcommits.org/)
6. Push to your fork and create a Pull Request

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for full guidelines.

---

## Support

- **Issues**: [GitHub Issues](https://github.com/huybrechtsxyz/strata/issues)
- **Discussions**: [GitHub Discussions](https://github.com/huybrechtsxyz/strata/discussions)
- **Documentation**: [https://huybrechtsxyz.github.io/strata](https://huybrechtsxyz.github.io/strata)
