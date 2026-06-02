# Squad Team

> strata

## Coordinator

| Name  | Role        | Notes                                              |
| ----- | ----------- | -------------------------------------------------- |
| Squad | Coordinator | Routes work, enforces handoffs and reviewer gates. |

## Members

| Name       | Role                      | Charter                             | Status |
| ---------- | ------------------------- | ----------------------------------- | ------ |
| Danny      | 🏗️ Lead / Architect        | .squad/agents/danny/charter.md      | active |
| Linus      | 🔧 Python / CLI Dev        | .squad/agents/linus/charter.md      | active |
| Basher     | ⚙️ DevOps Integrations     | .squad/agents/basher/charter.md     | active |
| Livingston | 🧪 Tester / QA             | .squad/agents/livingston/charter.md | active |
| Reuben     | 📝 Docs / Technical Writer | .squad/agents/reuben/charter.md     | active |
| Scribe     | 📋 Scribe                  | .squad/agents/scribe/charter.md     | active |
| Ralph      | 🔄 Work Monitor            | —                                   | active |

## Project Context

- **Project:** strata
- **User:** Vincent Huybrechts
- **Universe:** Ocean's Eleven
- **Created:** 2026-04-22
- **Stack:** Python 3.13, Click, Pydantic v2, uv, structlog, pytest
- **Purpose:** DevOps profile management CLI — manages multiple repos, merges terraform/ansible/config files across repos, builds unified deployment artifacts, executes deployments in order
- **CLI flow:** `xyz init` → `xyz repo add <repo>` → `xyz build` → `xyz deploy`
- **Workspace state:** `.strata/` folder in the workspace root
- **Work-path resolution:** `--work-path` flag > `STRATA_WORK_PATH` env var > walk up from CWD
