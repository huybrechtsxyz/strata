# Guided onboarding and cold-start experience

- Status: accepted
- Date: 2026-06-24

## Summary

| #   | Item                                                                | Phase | Status |
| --- | ------------------------------------------------------------------- | ----- | ------ |
| 1   | `strata sln init --list` — discover available templates             | 1     | done   |
| 2   | `strata new --list` shows bundles + descriptions                    | 1     | done   |
| 3   | Fix template bundles (`type:` → `provisioner:`)                     | 1     | done   |
| 4   | CI template validation test                                         | 1     | done   |
| 5   | Formalize `config/` as reference example workspace                  | 1     | done   |
| 6   | `strata console` — interactive workspace session (prompt_toolkit)   | 2     | done   |
| 7   | REPL commands: status, check, next, do, new, validate, flow, tools  | 2     | done   |
| 8   | `GuideController` extraction from `GuideCommand`                    | 2     | done   |
| 9   | Rich rendering (panels, tables, progress bar)                       | 2     | done   |
| 10  | `init` wizard inside the guide REPL                                 | 3     | todo   |
| 11  | `flow` command — Mermaid dependency graph (`strata validate graph`) | 3     | done   |
| 12  | `strata validate --path "**"` batch validation                      | 3     | done   |
| 13  | `strata validate --explain` — plain-English file summary            | 3     | done   |
| 14  | Validation error fix suggestions                                    | 3     | done   |
| 15  | Interactive `strata new` inside the guide REPL                      | 3     | future |
| 16  | Session progress persistence (`.strata/guide-progress.json`)        | 3     | done   |
| 17  | `strata env doctor` — non-interactive health check                  | 3     | future |
| 18  | Standalone LLM skill file (`strata-onboarding`)                     | 4     | done   |
| 19  | Progressive dependency scaffolding                                  | 5     | future |
| 20  | Auto-refresh mode (`strata guide --auto`)                           | 5     | future |
| 21  | Template marketplace / community templates                          | 5     | future |

---

## Context and Problem Statement

After `strata sln init`, a new user has a workspace directory structure (`.strata/`, VS Code config, CI scaffolding, `GETTING_STARTED.md`) but **no configuration files that actually describe their platform**. They face a "cold start" problem:

1. They must produce a **dependency chain** of YAML files in the correct order: configuration → environments → workspaces → resources/namespaces/modules → deployments.
2. Each file references others (`@repo/path`, provisioner names, store keys). A deployment can't validate without an environment; an environment can't resolve `@repo/` paths without remotes in the configuration.
3. The user must understand the full schema graph before writing the first file.
4. `strata new <template>` helps scaffold individual files, but doesn't solve the sequencing and wiring problem — you get a deployment template that references a configuration you haven't created yet.

The two example bundles (AKS, Compose) scaffold a connected set of files, but they're buried behind `strata sln init --template`, and a user has to know they exist. There's no progressive path from "I have nothing" to "I have a validating workspace."

**The result:** onboarding requires reading docs, understanding the schema, and manually assembling files. This is the single biggest barrier to adoption.

## What exists today

| Capability                       | What it does                                                            | Gap                                                                           |
| -------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `strata sln init`                | Creates `.strata/`, VS Code config, CI workflows, `GETTING_STARTED.md`  | No platform config files — empty workspace                                    |
| `strata sln init --template aks` | Scaffolds a connected AKS workspace (config + env + deployment + stack) | User must know templates exist; no discovery; templates may have schema drift |
| `strata new <template>`          | Scaffolds one YAML file from a template                                 | No sequencing; files reference things that don't exist yet                    |
| `strata new --list`              | Lists available single-file templates                                   | Doesn't show example bundles; no descriptions                                 |
| `strata validate`                | Validates a YAML file structurally                                      | Post-hoc — catches mistakes after the user has already guessed at the schema  |
| `strata tools status`            | Checks external tool availability                                       | Right idea, wrong moment — useful but not part of onboarding flow             |
| `strata env doctor`              | Environment health checks                                               | Requires a working environment to already exist                               |
| `strata help <topic>`            | Shows help topics (quickstart, workspace, etc.)                         | Text-only; no interactive guidance                                            |
| `GETTING_STARTED.md`             | Onboarding guide generated at init                                      | Static; can't adapt to the user's stack or intent                             |
| `.github/prompts/`               | Copilot prompts for new-module, new-deployment, diagnose                | AI-assisted but disconnected from CLI flow                                    |

## Decision Drivers

- A new user should go from `pip install strata` to `strata build plan` (validating dry-run) in under 10 minutes.
- The onboarding path should adapt to the user's stack (Terraform + AKS vs Compose vs Ansible + bare metal).
- Existing capabilities (`strata new`, templates, `validate`) should be composed, not replaced.
- Must work in both interactive terminals and CI/headless environments.
- Should not require reading docs to get started — docs are for deepening understanding, not for bootstrapping.

## Brainstorm — Candidate Approaches

### Approach A: Guided interactive init (`strata sln init --guided`)

Add an interactive wizard to `sln init` that asks a short series of questions and scaffolds a complete, connected workspace.

**Flow:**
```
$ strata sln init --guided

  Solution name: my-platform
  What are you deploying?
    [1] Kubernetes workloads (Terraform + Helm)
    [2] Docker Compose / Swarm services
    [3] Bare-metal / VM (Terraform + Ansible)
    [4] Minimal (configuration only, I'll add the rest)
  > 1

  Cloud provider?
    [1] Azure    [2] AWS    [3] GCP    [4] Other/skip
  > 1

  Environments? (comma-separated, or press Enter for dev,staging,prod)
  > dev,prod

  ✅ Created 8 files:
    config/my-platform-config.yaml    (configuration with Azure provider + AKS provisioner)
    envs/env-dev.yaml                 (development environment)
    envs/env-prod.yaml                (production environment)
    stack/ws-platform.yaml            (workspace)
    stack/res-aks.yaml                (AKS resource)
    stack/ns-app.yaml                 (application namespace)
    stack/mod-example.yaml            (example module)
    deploy/deploy-dev.yaml            (development deployment)

  Next: strata validate deploy/deploy-dev.yaml
```

**Pros:**
- Lowest friction — one command, a few questions, working workspace.
- Composes existing template bundles; no new file formats needed.
- `--guided` is opt-in; `sln init` stays lean for experienced users.
- Can fall back to `--template <name>` for non-interactive / CI use.

**Cons:**
- Wizard questions need maintenance as new provisioners/providers are added.
- Risk of "wizard lock-in" — users learn the wizard but not the underlying YAML.
- Interactive prompts don't work well in CI pipelines (mitigate: `--template` flag stays as the non-interactive equivalent).

### Approach B: `strata doctor` — workspace readiness checker

A command that inspects the current workspace and tells you what's missing or broken, with actionable fix suggestions.

**Flow:**
```
$ strata doctor

  Workspace: my-platform (.strata/ found)

  ❌ No configuration file found
     → Run: strata new configuration my-platform --path config/

  ❌ No environment files found
     → Run: strata new environment dev --path envs/

  ⚠️ No deployment files found
     → Run: strata new deployment my-platform-dev --path deploy/

  ✅ External tools:
     terraform 1.8.0    ✓
     helm 3.15.0        ✓
     docker 26.1.0      ✓

  1 error, 1 warning. Fix errors to proceed.
```

**Pros:**
- Non-prescriptive — tells you what's missing without forcing a specific structure.
- Works incrementally — run it after each step to see what's next.
- Teaches the schema by showing the dependency chain in situ.
- Useful beyond onboarding (CI validation, workspace audits).

**Cons:**
- Still requires the user to run individual `strata new` commands.
- Doesn't solve the wiring problem — the suggested files still need to reference each other correctly.
- More useful as a complement to Approach A than as a standalone.

### Approach C: Progressive template bundles with dependency resolution

Enhance `strata new` to understand file dependencies. When you scaffold a deployment, it checks whether the referenced configuration and environment exist and offers to scaffold those too.

**Flow:**
```
$ strata new deployment my-app-dev --path deploy/

  This deployment references:
    configuration: my-platform (not found)
    environment: dev (not found)

  Create missing files?
    [1] Yes, scaffold all dependencies
    [2] No, just create the deployment
  > 1

  Created:
    config/my-platform-config.yaml
    envs/env-dev.yaml
    deploy/deploy-my-app-dev.yaml
```

**Pros:**
- Meets the user where they are — they know they want a deployment, strata figures out the rest.
- No new commands; extends existing `strata new` behavior.
- Dependency graph is already known from the model relationships.

**Cons:**
- Complex implementation — need to reverse-engineer which config/env a deployment needs.
- Questions about defaults: which provisioner? which provider? The deployment template can't know.
- May produce files that need heavy editing, creating a false sense of completeness.

### Approach D: `strata init` quick-start presets (non-interactive)

Predefined preset strings that map to complete workspace scaffolds, usable in one command with no prompts.

**Flow:**
```
$ strata sln init my-platform --preset azure-aks
$ strata sln init my-services --preset compose
$ strata sln init my-infra --preset terraform-only
```

**Pros:**
- Zero interaction — works in CI, scripts, READMEs.
- Easy to document: "Run this one command."
- Presets are just named template bundles — minimal new code.

**Cons:**
- Rigid — you get exactly what the preset defines, no customization at init time.
- Preset proliferation: `azure-aks`, `azure-aks-multi-env`, `aws-eks`, `compose`, `ansible-bare-metal`...
- Essentially what `--template` already does — this is a naming/discovery problem, not a capability gap.

### Approach E: Post-init "next steps" engine

After any `strata` command, show contextual next-step suggestions based on workspace state.

**Flow:**
```
$ strata sln init my-platform
  ✅ Workspace initialized.

  📋 Next steps:
  1. strata sln init --template aks     (scaffold a Kubernetes workspace)
     OR
  2. strata new configuration my-platform --path config/   (start from scratch)
  3. strata tools status                (verify external tools)

$ strata new configuration my-platform --path config/
  ✅ Created config/my-platform-config.yaml

  📋 Next steps:
  1. Edit config/my-platform-config.yaml — add your provisioners and remotes
  2. strata new workspace my-platform --path stack/
  3. strata validate config/my-platform-config.yaml
```

**Pros:**
- Non-intrusive — always shows what to do next without forcing a flow.
- Teaches the dependency chain implicitly.
- Works with any approach above.

**Cons:**
- Suggestions need to be smart enough to not be annoying (don't repeat, don't suggest what already exists).
- Alone, doesn't reduce the number of steps — just makes them visible.

## Observations

1. **These are not mutually exclusive.** The strongest onboarding combines A (guided init for day 1) + B (doctor for ongoing validation) + E (next-steps for progressive guidance).

2. **The template bundles already exist** (AKS, Compose) but suffer from discoverability and schema drift (e.g., `type: infrastructure` in stage definitions that would fail Pydantic validation).

3. **The `.github/prompts/` directory** in the init scaffold (new-module, new-deployment, diagnose) is an interesting AI-assisted onboarding path that could complement the CLI flow — but can't be the primary path since it requires Copilot.

4. **`strata new --list` exists** but only shows single-file templates, not the example bundles. This is a quick win regardless of which approach we choose.

5. **The configuration file is the hardest to scaffold** because it's the hub — remotes, provisioners, policies, stores. A minimal valid configuration (name + one provisioner) should be the default template output, not a kitchen-sink example.

## Open Questions

1. Should `--guided` be the default for `sln init` (with `--no-guided` to skip), or opt-in?
2. How many presets/templates should ship built-in vs. being community-maintained?
3. Should `strata doctor` be a separate command or folded into `strata sln status`?
4. How do we handle the AI-assisted path (Copilot prompts) alongside the CLI path — parallel tracks or integrated?
5. Should the example bundles be validated in CI to prevent schema drift?
6. What's the minimum viable onboarding improvement — what single change gives the most lift?

## Implementation Plan

Ordered by impact and dependency. Each item is a shippable increment.

### Phase 1 — Quick wins (discovery + hygiene)

1. **`strata sln init --list`** — List available init templates (both example bundles and single-file templates) with descriptions. Users can't use what they can't find. Pull descriptions from each template's `template.yaml` manifest.

2. **`strata new --list` shows bundles** — Currently only shows single-file templates. Include the example bundles (aks, compose) with their descriptions so `strata new --list` is the one-stop discovery command.

3. **Fix existing template bundles** — The AKS and Compose scaffold templates use `type: infrastructure` / `type: platform` in deployment stage definitions, which fails Pydantic validation (`extra="forbid"`). Fix to use `provisioner: <name>`. A new user hitting validation errors on scaffolded files is a showstopper.

4. **CI template validation test** — Add a test that runs `strata sln init --template <name>` for each built-in template, then `strata validate` on every generated YAML file. Prevents schema drift from recurring.

5. **Formalize `config/` as a reference example workspace** — The `config/` directory already contains a complete, real workspace (`xyz-configuration` + `xyz-infrastructure` + `xyz-svc-traefik`) with all the file types: configuration, environments, providers, resources, namespaces, modules, deployments, Terraform backends, and a service repo. Formalize it:
   - Add README annotations explaining the purpose of each file and directory
   - Ensure all YAML passes `strata validate` (fix any schema drift — e.g., `customers/` → `tenants/`)
   - Reference it from docs, the skill file, and `strata guide` hints as "see `config/` for a working example"
   - Add a CI job that validates all YAML files in `config/` to prevent drift
   - This is the "show, don't tell" companion to the skill file — one explains the concepts, the other demonstrates them

### Phase 2 — Rework `strata guide` into an interactive REPL

The existing `strata guide` command already has the right bones: 8-phase workspace checklist, file-mode analysis, hints system with overrides, JSON output. But it's a single-shot command — run it, read the output, figure out what to do, run another command, come back. This loses state and context between invocations.

**Rework it into a persistent REPL session** (same pattern as sterling's `watch` command):

5. **`strata guide` enters a REPL by default** — Interactive session with `prompt_toolkit` (history, completion). The existing single-shot behavior moves to `strata guide --once` (or `--no-interactive`). The REPL keeps workspace state in memory across commands.

6. **REPL commands** — Each maps to existing or new capability:

   | Command                 | Alias | What it does                                                         |
   | ----------------------- | ----- | -------------------------------------------------------------------- |
   | `status`                | `s`   | Run the 8-phase workspace checklist (existing `_evaluate_checklist`) |
   | `check <file>`          | `c`   | Run file-mode analysis (existing `_run_file_mode`)                   |
   | `next`                  | `n`   | Show the next step hint (existing `_find_next_step`)                 |
   | `do`                    | `d`   | Execute the suggested next-step command directly (shell out)         |
   | `new <template> [name]` |       | Scaffold a file via `strata new` inline                              |
   | `validate [file]`       | `v`   | Run `strata validate` on a file or all found YAML                    |
   | `templates`             | `t`   | List available templates (single-file + bundles)                     |
   | `tools`                 |       | Run `strata tools status` inline                                     |
   | `open <file>`           | `o`   | Open a file in the default editor                                    |
   | `help`                  | `?`   | Show command table                                                   |
   | `quit`                  | `q`   | Exit the REPL                                                        |

7. **`GuideController`** — Extract the stateful logic from `GuideCommand` into a controller (per architecture rules: controllers orchestrate, commands are thin wrappers). The controller holds:
   - Current workspace state (solution, checklist, detected files)
   - Session history (what was scaffolded, what was validated)
   - Hints configuration
   - The "what's next" engine

8. **Rich rendering** — Replace `click.echo` checklist rendering with Rich panels and tables (like sterling's `_render_header` / `_render_status`). The REPL header shows workspace name + progress bar. Status shows the checklist with color.

### Phase 3 — Guided init and workspace graph

9. **`init` command in the REPL** — When the workspace is uninitialized (phase 1 pending), the REPL offers `init` which runs the guided wizard inline: stack type → provider → environments → scaffold. This replaces the standalone `--guided` flag idea — the REPL IS the guided experience.

10. **Post-scaffold auto-refresh** — After any `new` or `init` command, the REPL re-evaluates the checklist automatically so the user sees immediate progress.

11. **`strata guide flow` / `flow` REPL command** — Workspace dependency graph as a Mermaid diagram (same pattern as sterling's `flow` command). Nodes are YAML files; edges are cross-file references (`@repo/path`, `spec.workspace.file`, provisioner names). Highlights current state:
    - ✅ Green: file exists and validates
    - ⚠️ Orange: file exists but has validation errors
    - ❌ Red: referenced but missing

    Output modes:
    - Terminal: text tree representation
    - `--save` / `flow --save`: writes `flow.md` with fenced Mermaid block + live editor link
    - REPL `flow` command: inline rendering with Rich

    This makes the wiring visible. A new user sees "my deployment references an environment that doesn't exist yet" as a red node in the graph — immediately actionable.

12. **`strata validate --path "**"`** — Batch validation of all YAML files matching a glob. Already have `--path` for overlap validation; extend to accept `**` (all workspace YAML). Output: summary table (file, kind, status, error count). Supports `--output json` for CI. Feeds into the flow command (determines node colors). No new flag needed — just ensure `--path "**"` works as "validate everything."

13. **`strata validate --explain`** — After validation, emit a plain-English explanation of what the file does: "This deployment targets environment 'prd', runs 2 stages: Terraform infra then Compose services, references workspace xyz-ws-platform." TBD: scope and implementation approach — could be a separate flag or folded into verbose output. Needs further design.

14. **Validation error fix suggestions** — When Pydantic rejects a field (e.g., `extra inputs not permitted: 'type'`), include an actionable hint: "Did you mean `provisioner:`?" The model knows valid fields — surface them in the error message. TBD: implementation approach (post-processor on Pydantic errors vs custom error formatter).

15. **Interactive `strata new` in the guide REPL** — When running `new module myapp` inside the REPL, ask follow-up questions if template has optional sections: "Services? (y/n)", "Compose or Helm?", "Health check?" Keeps the single-shot `strata new` non-interactive (CI-safe) but the REPL version can be conversational.

16. **Session progress persistence** — Save guide REPL progress to `.strata/guide-progress.json`. Tracks: which phases are complete, what was scaffolded, what was validated. When you restart the REPL, it picks up where you left off: "Welcome back — you were at phase 4 (profile creation). Continue?"

17. **`strata env doctor`** — Workspace health check lives in the `strata env` command group (not a new top-level command). Non-interactive, one-shot, CI-friendly. Exit code 0/3. Checks: `.strata/` exists, configuration found, environments present, deployments present, tools available, files validate. GitHub issues exist for tracking this work.

### Phase 4 — LLM skill file

14. **Standalone `strata-onboarding` skill** — A single portable markdown file that users drop into their LLM/Copilot configuration (`.github/instructions/`, Squad `.squad/skills/`, or personal prompts folder) to get strata-aware assistance *before* they have a workspace. One file, not split — avoids discovery overhead for new users.

    Contents:
    - What strata is (30-second orientation)
    - The dependency chain: configuration → environments → workspaces → deployments (the key mental model)
    - YAML schema essentials (envelope, kinds, `meta.name` rules, `@repo/` references)
    - Onboarding command sequence (`sln init` → `new` → `validate` → `guide` → `build plan`)
    - Common patterns (secrets/variables/features, cross-repo refs, stages with provisioners)
    - Anti-patterns to avoid (plain-text secrets, `type:` on stages, missing `apiVersion`)

    Ships in the strata repo at `docs/skills/strata-onboarding.md` and also bundled into the init scaffold at `.github/instructions/strata-onboarding.instructions.md`. The scaffold version replaces the existing `strata-yaml.instructions.md` (which is incomplete — still has `type: terraform` in stage examples).

### Phase 5 — Future consideration

18. **Progressive dependency scaffolding** — `new deployment` in the REPL detects missing config/environment and offers to scaffold them (the REPL has state to track what exists).

19. **Auto mode** — `strata guide --auto 30` polls workspace state and re-renders on file changes (file watcher). Useful during active editing — save a YAML file, see the checklist update.

20. **Template marketplace / community templates** — Allow `strata sln init --template https://...` or a registry. Deferred until the built-in templates are solid.

### Reference: sterling `watch` + `flow` patterns

The sterling CLI (`E:\Sources\app-int-agentic-workflow`) implements two relevant patterns:

**`cli_watch.py` + `watch_controller.py` — REPL session:**
- **`prompt_toolkit.PromptSession`** with `InMemoryHistory` for readline-style input
- **`WatchController`** holds stateful `WatchState` dataclass across commands
- **Rich rendering** for panels, tables, syntax-highlighted traces
- **`_AutoRefresher`** background thread for polling with optional auto-run on change
- **`match/case` dispatch** for REPL commands with aliases
- **Clean shutdown** via `try/finally` stopping the auto-refresher

**`cli_flow.py` + `flow_command.py` — dependency graph visualization:**
- Generates a **Mermaid `graph LR`** diagram from workflow rule configuration
- Nodes = rules; edges = outcome-based transitions (which rule fires next based on state)
- With `--id`: fetches live item state and **highlights position** (green = done, orange = current)
- With `--save`: writes `flow.md` per squad with fenced Mermaid + summary table
- Includes a **Mermaid Live URL** (base64-encoded) for one-click browser preview
- `_find_targets()` resolves edges by matching outcome tags/state to downstream rule filters

**Mapping to strata:**

| Sterling concept           | Strata equivalent                                                              |
| -------------------------- | ------------------------------------------------------------------------------ |
| Rules                      | YAML files (configuration, environment, deployment, etc.)                      |
| Outcome edges              | Cross-file references (`@repo/path`, `spec.workspace.file`, provisioner names) |
| Item position highlighting | File validation status (exists + valid / exists + invalid / missing)           |
| Per-squad output           | Per-deployment or whole-workspace graph                                        |
| Mermaid Live URL           | Same — include in `graph.md` output                                            |

Key differences:
- Sterling watches a single work item (external state). Strata guides through local workspace setup (filesystem state).
- Sterling's pipeline is fetch → plan → execute → apply. Strata's pipeline is checklist → next-step → scaffold → validate.
- Sterling needs `--id` to start. Strata's REPL starts from workspace context (auto-detected).
- Sterling's graph is rule-to-rule (dynamic state machine). Strata's graph is file-to-file (static dependency tree with live validation status).

## Decision Outcome

**Chosen approach:** Rework `strata guide` from single-shot to interactive REPL, folding guided init, doctor, and next-steps into a single stateful session. Phase 1 is pure hygiene (discovery + template fixes). Phase 2 is the REPL rework. Phase 3 adds guided init inside the REPL.

Status: **accepted** — proceeding with Phase 1, then Phase 2.
