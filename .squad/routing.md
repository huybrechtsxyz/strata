# Work Routing

How to decide who handles what.

## Routing Table

| Work Type                                 | Route To   | Examples                                                 |
| ----------------------------------------- | ---------- | -------------------------------------------------------- |
| Architecture, design decisions, scope     | Danny      | CLI structure, module design, cross-cutting concerns     |
| Python CLI, Click commands, decorators    | Linus      | New commands, options, `ctx.obj` wiring, exit codes      |
| Models, services, controllers             | Linus      | Pydantic models, service logic, controller orchestration |
| Work-path resolution, `xyz set` config    | Linus      | `resolve_work_path()`, `default_map` loading             |
| Git integration, repo cloning/sync        | Basher     | `xyz project add`, `integrations/git.py`                 |
| Terraform file merging + execution        | Basher     | Build pipeline, `integrations/terraform.py`, `tf.exe`    |
| Ansible merging + execution               | Basher     | Deploy pipeline, playbook merging                        |
| Other integrations (Docker, Vault, Azure) | Basher     | `integrations/*.py`                                      |
| Build pipeline (`xyz build`)              | Basher     | Multi-repo config parsing, artifact generation           |
| Deploy pipeline (`xyz deploy`)            | Basher     | Ordered tool execution, `deployment_model.py`            |
| Tests, pytest, CliRunner                  | Livingston | New test files, fixtures, edge cases, exit code tests    |
| Pydantic model validation tests           | Livingston | Valid/invalid YAML, model edge cases                     |
| Docs, user guides, CLI reference          | Reuben     | `docs/` Sphinx pages, Markdown guides                    |
| Changelog, release notes                  | Reuben     | `docs/CHANGELOG.md`                                      |
| Code review                               | Danny      | Review PRs, approve or reject, enforce patterns          |
| Issue triage (`squad` label)              | Danny      | Analyze issue, assign `squad:{member}` label             |
| Session logging                           | Scribe     | Automatic — never needs routing                          |

## Issue Routing

| Label              | Action                                               | Who        |
| ------------------ | ---------------------------------------------------- | ---------- |
| `squad`            | Triage: analyze issue, assign `squad:{member}` label | Danny      |
| `squad:danny`      | Architecture, design, review work                    | Danny      |
| `squad:linus`      | CLI commands, models, services                       | Linus      |
| `squad:basher`     | Integrations, build, deploy pipeline                 | Basher     |
| `squad:livingston` | Tests, QA                                            | Livingston |
| `squad:reuben`     | Docs, guides, changelog                              | Reuben     |

## Rules

1. **Eager by default** — spawn all agents who could usefully start work, including anticipatory downstream work.
2. **Scribe always runs** after substantial work, always as `mode: "background"`. Never blocks.
3. **Quick facts → coordinator answers directly.** Don't spawn an agent for status questions.
4. **When two agents could handle it**, pick the one whose domain is the primary concern.
5. **"Team, ..." → fan-out.** Spawn all relevant agents in parallel as `mode: "background"`.
6. **Anticipate downstream work.** If a feature is being built, spawn Livingston to write test cases simultaneously.
7. **Issue-labeled work** — when a `squad:{member}` label is applied to an issue, route to that member. Danny handles all `squad` (base label) triage.
