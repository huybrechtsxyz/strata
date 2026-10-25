# v1 -> v2 VS Code Extension Coverage Gaps

- Status: living - update in place as items are ported, deliberately deferred, or decided against
- Last updated: 2026-10-24

## Overview

Tracks v1's `src/vscode` extension features against what v2's `src/vscode` extension has today.
v1 (`e:\SourcesXYZ\strata\src\vscode`) is a from-scratch-documented, ~11.5k LOC extension with a
`README.md` Features/Commands/Views section, a chat participant, and ~30 tree/view providers. v2's
extension (`src/vscode`) was deliberately scaffolded small (see its own CHANGELOG `[0.0.1]` entry)
and only wraps CLI surface that existed at the time (`validate`, `graph`, `version`). This file is
the durable record of what's left, so each gap can be closed, explicitly deferred, or decided
against on its own, instead of relying on conversation memory. Not every gap needs to close -
some v1 features may never be rebuilt in v2, and that's an acceptable outcome, not an oversight -
mark those `Decision: won't port` with a one-line reason rather than leaving them open forever.

Unlike `docs/decisions/` (immutable ADRs) and `docs/design/` (finished, nothing pending), this is
a flat working list, same shape as `gap_fit_v1.md` but scoped to the extension instead of the YAML
schema.

## Status legend

- `Ported` - exists in v2's extension today.
- `CLI ready, not wired` - the v2 CLI command this needs already exists (`strata.commands.cli`);
  only the extension-side provider/command is missing.
- `No v2 backend yet` - the underlying v2 CLI capability doesn't exist yet; wiring the extension
  isn't possible until that lands.
- `Decision: won't port` - deliberately not planned, with a reason.
- `Undecided` - known gap, no call made yet either way.

## Gap list

### 1. Per-file / all-files validate commands

- **v1:** `strata.validateCurrentFile` / `strata.validateAll`, CodeLens "Validate" action above a
  YAML document (`codeLensProvider.ts`).
- **v2 today:** only whole-solution `strata.validate` (`diagnosticsProvider.ts`) - matches v2's CLI
  (`validate` has no `-f FILE`, by design, see `validate_command`'s own docstring).
- **Status:** `Decision: won't port` as a literal per-file re-validate - v2's `validate` command
  itself has no file-scoped mode, so there's nothing narrower to call. CodeLens quick-actions above
  a document (triggering the existing whole-solution validate) could still be added cheaply; that
  part is `Undecided`.

### 2. ~~Schema export & wiring (`strata.exportSchemas`)~~ — RESOLVED (2026-10-24)

- **v1:** one-click JSON Schema export + wiring into VS Code's YAML schema association, enabling
  autocomplete/inline validation in the editor itself (separate from `strata validate`).
- **v2 today:** the backend already does this — and does it automatically, not as a separate
  manual step. `ScaffoldController._export_schemas()` (`scaffold_controller.py`, added 2026-10-02)
  writes one JSON Schema per registered kind plus an umbrella `kind:`-dispatching schema to
  `.strata/schemas/`, every time `strata sln init`/`strata sln update` runs (`.strata/` is
  package-owned, always regenerated — confirmed real, tested:
  `test_init_exports_one_schema_file_per_registered_kind`,
  `test_update_regenerates_schemas_even_if_hand_edited`). The shipped `dot.vscode/settings.json`
  scaffold template is pre-wired with `"yaml.schemas": {".strata/schemas/strata.json": ["**/*.yaml",
  "**/*.yml"]}` (`test_init_wires_vscode_yaml_schemas_setting`) — a freshly-scaffolded repo gets
  real editor autocomplete/inline validation with zero extra steps, no separate export command
  needed at all. (`docs/design/solution-scaffolding.md` had a stale "Deliberately out of scope"
  note claiming neither existed — corrected 2026-10-18; this doc's own status was equally stale
  until the same correction.)
- **Status:** `CLI ready, not wired` for the one genuine remaining gap — the **extension itself**
  has no command that invokes `sln init`/`sln update` at all (confirmed: `sln` is listed as
  "not yet wired into the extension" in `extension.ts`'s own header comment). A user gets the
  schema/`yaml.schemas` benefit automatically once they've run `sln init`/`sln update` from a
  terminal, but there's no one-click `strata.exportSchemas`-equivalent command inside the editor —
  wiring one would just shell out to `sln update` (reusing the existing CLI, not a new backend
  capability) and could double as the extension's first `sln`-group command generally (see gap #4's
  "readiness/guide view" note, which has the same underlying "no `sln` wiring yet" root cause).
- **Design (2026-10-24):** one command, `strata.updateSolution` ("Strata: Update Solution Files"),
  calling `sln update --output json` through `StrataClient`. Verified against the real CLI first:
  `sln update` is not schema-only. It always rewrites package-owned `.strata/` (README, schemas) and
  also *creates* any missing user-owned scaffold file (`.vscode/settings.json`, `.vscode/
  extensions.json`, `.github/` templates); an existing user-owned file is never overwritten
  (`ScaffoldController._scaffold()`). So the command asks for a modal confirmation saying exactly
  that, then reports the envelope's `Created:`/`Updated:` info messages (count in a notification,
  full list in a "Strata" output channel). Failures (`ok: false`, CLI not found, crash) surface as an
  error notification with the first diagnostic. No new CLI surface.
- **Plan (one phase):** `StrataClient.slnUpdate()`; the command + confirmation + output channel in
  `extension.ts`; `package.json` contribution; README/CHANGELOG; compile + lint; exercise the client
  method against a scratch solution with a `vscode` stub.
- **Built 2026-10-24** as planned (`strata.updateSolution`). Verified by loading the compiled
  extension under a `vscode` stub against a scratch solution: declining the confirmation writes
  nothing; accepting writes `.strata/schemas/` and reports all 7 changes; a folder with no
  `strata.yaml` and a missing CLI both surface an error notification. Status: `Ported`.

### 3. Build / Deploy commands (dry-run + run, stage-based)

- **v1:** `strata.buildDryRun` / `strata.buildRun` / `strata.deployDryRun` / `strata.deployRun`,
  stage-based deploy (`/stage [file] <stage-name>` in chat too), `buildPlanProvider.ts`.
- **v2 today:** nothing wired into the extension.
- **Status:** `CLI ready, not wired` - `build run` and `deploy run` both exist in `cli.py`.
  Dry-run needs checking against each command's actual flag surface (`build run --help` /
  `deploy run --help`) before wiring - don't assume `--dry-run` exists just because v1 had it.

### 4. Workspace Explorer (readiness checklist, profile, 8-phase guide)

- **v1:** `workspaceViewProvider.ts` / `workspaceHealthProvider.ts` - active profile name, phase
  checklist (Init -> Configure -> Validate -> Build -> Deploy...), `strata.showGuide`.
- **v2 today:** nothing - no profile or `.strata/` state concept at all (ADR-0015 replaced
  `.strata/solution.json` with the `strata.yaml` manifest; no multi-profile model exists).
- **Status:** `Decision: won't port` for the profile/active-profile piece specifically - there is
  no v2 concept of a "profile" to switch between (confirmed: no `sln status`-equivalent, no
  profile model in `src/strata/models/`). A readiness/guide view without the profile angle is
  `Undecided` - would need its own v2-appropriate definition of "phases" first, not a direct port.

### 5. Files view (YAML files + validation badges)

- **v1:** `filesViewProvider.ts` / `fileDecorationProvider.ts` - explorer-wide per-file ✅/⚠️/❌
  badges.
- **v2 today:** nothing - only the Problems panel (per-diagnostic, not per-file badge).
- **Status:** `Undecided`. Could be built purely from the existing `strata validate` envelope
  (bucket `diagnostics[]` by file, same data `diagnosticsProvider.ts` already buckets) - no new CLI
  surface needed, so this is implementable today if wanted.

### 6. Repositories view (git tags: release / quality-gate)

- **v1:** `repositoriesViewProvider.ts` - per-repo latest `v*.*.*` release tag + quality-gate tag,
  path, origin, commit SHA.
- **v2 today:** nothing - v2 has no concept of multiple linked repos (`remotes` in
  `strata.yaml`/ADR-0015 exist, but no CLI command surfaces their git metadata).
- **Status:** `No v2 backend yet`.

### 7. Tools view (terraform / ansible / git / docker / uv availability)

- **v1:** `toolsViewProvider.ts` - ✅/❌ + version per tool.
- **v2 today:** nothing - status bar only reports the `strata` CLI itself (`statusBarProvider.ts`),
  not its subprocess dependencies.
- **Status:** `No v2 backend yet` as a CLI-sourced check (no `strata tools status`-equivalent).
  Could in principle be done fully client-side (the extension already knows how to `execFile` a
  binary and check for ENOENT, see `strataClient.ts`'s own `StrataCLINotFoundError` handling) -
  `Undecided` whether that's worth doing without a CLI source of truth behind it.

### 8. Environments view (status + drift)

- **v1:** `envViewProvider.ts` - `strata.envStatus` / `strata.envDrift` / `strata.envDoctor`.
- **v2 today:** nothing.
- **Status:** `No v2 backend yet` for drift/doctor specifically (no `strata deploy status` or
  drift-detection command exists in `cli.py` today). `audit status`/`audit changes` exist and are
  related but aren't the same capability (audit = history of past changes, drift = live
  config-vs-deployed-state comparison).

### 9. Audit Trail view (history, resend, export)

- **v1:** `auditViewProvider.ts` - `strata.auditChanges` / `strata.auditResend` / `strata.auditExport`.
- **v2 today:** nothing wired; CLI has `audit status` / `audit changes`.
- **Status:** `CLI ready, not wired` for the "changes" (history) half. `auditResend` (resend to
  sinks) and `auditExport` have no obvious v2 CLI equivalent yet - check `audit_command.py`'s
  actual subcommands before assuming parity; may be a partial port only.

### 10. Dependency graph / topology diagram / diagram builder

- **v1:** `crossReferenceProvider.ts` (dependency graph), `diagramsViewProvider.ts` /
  `diagramBuilderProvider.ts` / `diagramPreviewProvider.ts` (topology diagrams, including
  AI-generated ones from a text description).
- **v2 today:** `graphViewProvider.ts` (`Ported`) - already a deliberate from-scratch redesign, not
  a port (see repo memory: v1's client-side `@repo/path` scanning was replaced by a CLI-sourced
  `strata graph`). Diagram rendering/building itself has no v2 equivalent.
- **Status:** dependency graph = `Ported` (different implementation, same user-facing capability).
  Topology diagram view/builder = `No v2 backend yet`, and the AI-generate-from-description part
  is a distinct chat/AI feature, see gap #13.

### 11. Work Items view (approval gates / cost / security review queue)

- **v1:** `workItemsViewProvider.ts` - `strata.approveWorkItem` / `rejectWorkItem` /
  `refreshWorkItems` / `showWorkItem`, chat `/approvals`.
- **v2 today:** nothing - no work-item/approval-gate model or command exists anywhere in
  `src/strata/`.
- **Status:** `No v2 backend yet`. Also worth flagging: `docs/work/access-management.md`-style
  features were explicitly evaluated and killed in a prior session (see
  `/memories/repo/v1-consumer-usage.md`, "Access management evidence - 4-kind proposal KILLED")
  for a different but adjacent reason (no real consumer evidence) - worth checking whether
  work-item/approval-gates has similar real-consumer evidence before investing here.

### 12. Promotions view (ring/promotion status, matrix, trigger)

- **v1:** `promotionsViewProvider.ts` - chat `/promote`.
- **v2 today:** nothing wired; CLI has `promote status` / `promote apply` / `promote view`.
- **Status:** `Undecided` — deferred 2026-10-24. The CLI is ready, but no real data exercises it:
  zero `kind: version` documents with `spec.promotion` in `config/`, `.v2-cfg`, `.v2-haven` or
  cfg-int's `origin/v2`, and `promote view`/`status` return an empty rollout for `config/`. A view
  would render an empty tree everywhere and could only be shaped against synthetic data. Revisit when
  a consumer (cfg-int v3's rings are the expected first) tags its version documents.

### 13. Chat participant (`@strata`) and AI-assisted commands

- **v1:** full chat participant with `/status /validate /guide /build /deploy /stage /values
  /drift /repos /promote /versions /review /diagnose /sbom /approvals /diagram`. Of these,
  `/review` (AI terraform-plan risk analysis), `/diagnose` (AI failure diagnosis), `/sbom` (AI
  supply-chain analysis), and `/diagram create <description>` are LLM-backed, not plain CLI
  wrappers (`aiPromptBuilder.ts`).
- **v2 today:** no chat participant, by decision (below).
- **Status:** `Decided 2026-10-24 - no chat participant, MCP server or language-model tools`.
  v1's MCP server exposed 13 tools and no consumer (haven, cfg-int) used any of them; the agents
  people actually use (Claude Code, VS Code Copilot agent mode) already run the CLI in a terminal
  and read `--output json`. What they lack is strata knowledge, so v2 scaffolds
  [Agent Skills](https://agentskills.io) instead: `sln init`/`sln update` write a strata-generated
  `.claude/skills/strata/SKILL.md` (commands, ground rules, known pitfalls) and a team-owned
  `.claude/skills/project-gotchas/SKILL.md`. `.claude/skills/` is read by both Claude and Copilot.
  See [ADR-0046](../decisions/0046-strata-agent-skills-with-generated-marker.md) and
  [solution-scaffolding.md](../design/solution-scaffolding.md). Other agents (`AGENTS.md`,
  `.agents/skills/`) only when a consumer needs them.

### 14. Secret management, deployment locking, multi-environment composition, SBOM generation

- **v1:** Azure Key Vault / HashiCorp Vault / Bitwarden integration with generate-on-first-build;
  configurable lock backends (local/Azure/Terraform Cloud/Consul); environment merge with override
  tracking; 12+ built-in policies; 27+ lifecycle hook phases; CycloneDX SBOM generation across 8
  language ecosystems.
- **v2 today:** `values` command covers secret store resolution/generation already (`values
  generate`/`set`/`rotate`/`status`, see `/memories/repo/v1-consumer-usage.md`'s
  values-secrets-command.md phase history) - this slice is largely `Ported` at the CLI level,
  just not the extension's own secret-focused view. Locking, policy engine, lifecycle hooks, and
  SBOM are **not** extension features at all in v1 (they're CLI/backend capabilities v1's UI merely
  surfaces) - tracking their v2 status belongs in `docs/work/policy-engine-architecture.md` /
  `docs/work/sbom-generation.md` / `docs/work/output-store-lock-audit.md`, not here. Listed only
  so a reader doesn't think this file is the place to track them.
- **Status:** out of scope for this file beyond this note - see the docs/work/ files named above.

### 15. Profile switcher (`strata.switchProfile`)

- **v1:** switch the active environment profile.
- **v2 today:** no profile concept (same root cause as gap #4).
- **Status:** `Decision: won't port` - no v2 model to switch between; revisit only if a v2
  "profile" concept is ever introduced for an unrelated reason.

### 16. Interactive console launcher (`strata.openConsole`)

- **v1:** launches an interactive Strata console in the integrated terminal.
- **v2 today:** nothing, but trivial - v2's CLI has no separate interactive/REPL mode that this
  would launch into (unlike v1, which had `init`/`repo`/`profile`/`config` interactive-bootstrap
  commands - see `/memories/repo/v1-consumer-usage.md` Tier 2 list). Nothing to wire to.
- **Status:** `No v2 backend yet` (and low priority - v1's own Tier 2 list already marks the
  commands this would front as not-proven-used-in-real-CI).

### 17. State service status bar + log tailing, model cache refresh

- **v1:** `stateServiceStatusBarProvider.ts` / `stateServiceTailProvider.ts` (a long-running v1
  background state service this extension could tail), `cacheWarmerProvider.ts` /
  `strata.refreshCache`.
- **v2 today:** nothing - v2 has no equivalent background state service or model cache concept at
  all (every v2 CLI call is a one-shot `execFile`, see `strataClient.ts`).
- **Status:** `Decision: won't port` unless v2 ever grows a long-running service process - nothing
  to tail or warm today.

## How gaps get added/closed here

Same convention as `gap_fit_v1.md`: when wiring a new v2 extension feature or deciding against one,
update its entry in place (don't just note it in session/repo memory) - strike the heading with
`~~...~~ RESOLVED` or `~~...~~ WON'T PORT` and explain why, matching how `gap_fit_v1.md` closes
its own entries. Add a new numbered entry for anything found here that isn't already covered.
