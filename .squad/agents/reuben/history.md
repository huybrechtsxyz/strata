# Reuben — History

## Core Context

Docs / Technical Writer for xyz-platform. Sphinx docs, Markdown guides, CLI reference.
User: Vincent Huybrechts. Stack: Sphinx, reStructuredText, Markdown.
Key paths: `docs/`, `docs/conf.py`, `docs/index.rst`, `docs/cli-preferences.md`, `docs/SQUAD.md`.

## Learnings

### 2026-05-18 — Devcontainer scaffolding added to `xyz init`

- **`docs/platform/commands.md`** — Rewrote the `## init` section description and added a "Files created" table listing all five scaffolded paths (`.platform/project.json`, `.platform/cli.yaml`, `.platform/logging.yaml`, `.devcontainer/devcontainer.json`, `.devcontainer/post-create.sh`). Added a "Dev container" callout about "Reopen in Container".
- **`docs/platform/workflow.md`** — Extended the "Creates:" bullet list in Phase 1 to include both `.devcontainer/` files and a note about VS Code / Codespaces.
- **`docs/README.md`** — Added a one-line inline comment in the Quick Start `xyz init` step pointing users to "Reopen in Container".
- Only touched the three files directly affected; no new files created.
