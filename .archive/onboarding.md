# Onboarding Gap Analysis
_Date: 2026-06-09_

---

## 🔴 Critical — Would break a new user

### 1. `project.json` vs `solution.json` naming mismatch
Both `docs/platform/getting-started.md` and `docs/platform/workflow.md` tell users the workspace registry is `.strata/project.json`. The actual code writes `.strata/solution.json`. A new user following the docs would look for a file that doesn't exist.

### 2. Built-in `strata help` content uses old `xyz` CLI name
`src/strata/data/help/quickstart.md` and `src/strata/data/help/troubleshooting.md` still say `xyz init`, `xyz repo add`, `xyz audit list`, etc. Anyone running `strata help --topic quickstart` gets outdated instructions.

---

## 🟡 High — Missing key commands or flows

### 3. `strata sln update` is completely undocumented
Exists in `cli_sln.py` with a real implementation (refreshes schemas, templates, devcontainer after a package upgrade). Not in getting-started, workflow, or commands.md.

### 4. `strata tools` is absent from the getting-started flow
`strata tools status --missing` is the most direct answer to "what's not installed?" — the #1 question on day one. Described in commands.md but never mentioned in the guided flow.

### 5. `compose` template is undiscoverable
`src/strata/templates/examples/` contains both `aks/` and `compose/`, but only `aks` appears in any doc. Users don't know the compose option exists.

### 6. `strata new` has no usage examples in any guide
Listed in the commands table but no examples showing `strata new namespace my-ns` or `strata new --list`. Day-to-day use involves this command heavily.

### 7. `strata profile add --activate` flag missing from command reference tables
Template README and getting-started.md snippets both use it, but it's not listed in the profile command table in `docs/platform/workflow.md` or `docs/platform/commands.md`.

---

## 🟢 Medium — Incomplete coverage

### 8. `strata repo add --type local` undocumented
The template `GETTING_STARTED.md` (copied into new workspaces) uses it; nothing in the guides explains it.

### 9. `strata values resolve` missing from troubleshooting section
The most useful debugging command for resolving secrets/vars isn't in getting-started.md.

### 10. `strata ref data` type unexplained
`env`, `config`, and `secret` ref types are covered; `data` refs are never described anywhere.

### 11. `strata doctor` listed as "Coming soon"
Sitting in the troubleshooting section of getting-started.md as a false promise with no replacement path.

### 12. `strata sln export` missing from workflow.md command tables
Covered in getting-started.md but absent from the comprehensive reference tables at the bottom of workflow.md.

---

## Files to update (by gap)

| Gap                          | Files                                                                                                            |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 1 — solution.json rename     | `docs/platform/getting-started.md`, `docs/platform/workflow.md`                                                  |
| 2 — xyz → strata in help     | `src/strata/data/help/quickstart.md`, `src/strata/data/help/troubleshooting.md`, all `src/strata/data/help/*.md` |
| 3 — sln update               | `docs/platform/commands.md`, `docs/platform/workflow.md` (Phase 9), `docs/platform/getting-started.md`           |
| 4 — tools in getting-started | `docs/platform/getting-started.md` (Prerequisites section)                                                       |
| 5 — compose template         | `docs/platform/getting-started.md`, `docs/platform/commands.md`                                                  |
| 6 — strata new examples      | `docs/platform/getting-started.md`, `docs/platform/workflow.md`                                                  |
| 7 — profile add --activate   | `docs/platform/commands.md`, `docs/platform/workflow.md`                                                         |
| 8 — repo add --type local    | `docs/platform/workflow.md`, `docs/platform/commands.md`                                                         |
| 9 — values resolve           | `docs/platform/getting-started.md` (troubleshooting section)                                                     |
| 10 — ref data type           | `docs/platform/workflow.md` (Phase 4), `docs/platform/commands.md`                                               |
| 11 — strata doctor           | `docs/platform/getting-started.md`                                                                               |
| 12 — sln export in workflow  | `docs/platform/workflow.md` (command reference tables)                                                           |
