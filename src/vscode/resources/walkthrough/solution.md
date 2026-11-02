# Open a solution

A strata solution is a folder with a `strata.yaml` manifest and the YAML documents beside it:
providers, workspaces, environments, deployments, and the rest. Strata starts as soon as the folder
you open contains one.

No solution yet? Create one in a terminal:

```sh
strata sln init my-platform
```

That writes `strata.yaml` plus a few starter files:

| Path              | What it is                                                |
| ----------------- | --------------------------------------------------------- |
| `strata.yaml`     | The solution manifest                                     |
| `.strata/`        | Strata-owned: JSON Schemas and notes, refreshed on update |
| `.vscode/`        | Recommended extensions and YAML schema wiring             |
| `.github/`        | Pull request and change request templates                 |
| `.claude/skills/` | Agent skills for Claude and GitHub Copilot                |

Everything outside `.strata/` is yours to edit.
