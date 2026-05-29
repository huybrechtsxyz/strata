# Reuben — History

## Core Context

Docs / Technical Writer for xyz-platform. Sphinx docs, Markdown guides, CLI reference.
User: Vincent Huybrechts. Stack: Sphinx, reStructuredText, Markdown.
Key paths: `docs/`, `docs/conf.py`, `docs/index.rst`, `docs/cli-preferences.md`, `docs/SQUAD.md`.

## Learnings

### 2026-05-19 — README pitch rewrite + Getting Started guide

- **`README.md`** — Replaced the opening paragraph (generic feature description) and the Quick Install + Quick Start sections with: (1) a one-paragraph pain statement pitch answering "why not just Terraform?"; (2) a minimal 4-command Quick Start block; (3) a link to the new Getting Started guide. Automation and License sections left untouched.
- **`docs/platform/getting-started.md`** — Created new file. Target reader: DevOps engineer, first contact with the tool. Structure: Prerequisites → Install (pipx + dev) → Init → File structure → Repo registration → Profiles → Validate → Deploy → Troubleshooting (audit, --verbose, JSON output) → Persist preferences → Next steps. Kept to ~150 lines (well under 200 limit).
- **Audience notes:** Operators scan — used tables, short paragraphs, and code blocks throughout. Avoided marketing language in the guide itself; saved the pitch for README only.
- **Decisions:** Chose to fold the old Quick Install and Quick Start into a single simplified Quick Start rather than maintaining two separate sections. Wrote to decisions/inbox for Danny's review.

### 2026-05-18 — Devcontainer scaffolding added to `xyz init`

- **`docs/platform/commands.md`** — Rewrote the `## init` section description and added a "Files created" table listing all five scaffolded paths (`.platform/project.json`, `.platform/cli.yaml`, `.platform/logging.yaml`, `.devcontainer/devcontainer.json`, `.devcontainer/post-create.sh`). Added a "Dev container" callout about "Reopen in Container".
- **`docs/platform/workflow.md`** — Extended the "Creates:" bullet list in Phase 1 to include both `.devcontainer/` files and a note about VS Code / Codespaces.
- **`docs/README.md`** — Added a one-line inline comment in the Quick Start `xyz init` step pointing users to "Reopen in Container".
- Only touched the three files directly affected; no new files created.

### 2026-05-19 — sln group docs and instructions update

- **`docs/platform/getting-started.md`** — Updated all `xyz init` references to `xyz sln init`. Added a `xyz sln export` section documenting the workflow for saving a workspace as a scaffold template.
- **`.github/copilot-instructions.md`** — Added `sln` to the registered CLI command groups list. Canonical list now includes: `sln init`, `sln clean`, `sln status`, `sln export` under the `sln` group. Flat `init`, `clean`, `status` are no longer registered directly.
- **Key convention:** `xyz sln init` is the canonical entry point for workspace creation in all documentation. Any doc referencing the old flat `xyz init` must be updated.

### 2026-05-29 — Document `github` as a valid secret store

- **`docs/config/configuration.md`** — Added a new "## Secret Stores" section (before Notes) with: a full store-type reference table (`constant`, `environment`, `github`, `azure-keyvault`, `bitwarden`, `vault`, `infisical`); a dedicated `### github — GitHub Actions secrets` subsection with YAML example, uppercase normalization note, local development workaround, `version` not-supported callout, and `allowed_secret_stores` production policy snippet.
- **`docs/platform/integrations.md`** — Updated the `secrets` row in the Capability Protocols table to note that `github` and `environment` are built-in resolvers, not integrations. Added a blockquote callout below the table pointing to the configuration.md reference.
- **Key fact to preserve:** `store: github` is NOT an integration — it is a built-in resolver in `ValueController` that reads `os.environ.get(value.upper())`. GitHub Actions injects secrets as env vars before each step. `GITHUB_ACTIONS != "true"` triggers a warning; missing env var returns an error.
- `version` field raises a validation error for `store: github` (enforced in `SecretStoreModel` via `model_validator`).
