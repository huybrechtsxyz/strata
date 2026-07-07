# Changelog — Strata VS Code Extension

All notable changes to the Strata VS Code extension are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- New `@strata` chat participant commands: `status`, `validate`, `guide`, `build`, `deploy`, `repos`
- Enhanced **Environments** view with drift detection and status monitoring
- Enhanced **Audit Trail** view with SIEM resend functionality
- New `strata.autoExportSchemas` setting for automatic schema wiring on workspace open
- Support for per-stage deployment (deploy a single stage at a time)
- Deployment freshness check: warns if build artifacts are stale

### Changed

- Improved error diagnostics with fix suggestions
- Better handling of circular dependencies
- More granular control over validation timing (on-save, on-type)

### Fixed

- Schema wiring now works correctly for all 15 model types
- Cross-reference resolution (@repo/path) now works in deeply nested files
- Fixed issue where environment-specific secrets were not shown in values lists

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
