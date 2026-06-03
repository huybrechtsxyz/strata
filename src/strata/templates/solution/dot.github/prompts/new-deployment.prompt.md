---
agent: agent
description: "Scaffold a new strata Deployment YAML file"
---

Create a new `kind: Deployment` YAML file.

Ask the user for:
1. **Deployment name** — the identifier for this deployment (e.g. `deploy-prd`, `deploy-stg`)
2. **Workspace file** — path to the workspace YAML (e.g. `@my-repo/workspace.yaml`)
3. **Namespaces** — list of namespace names and their file paths to include
4. **Stages** — ordered deployment stages with type (`terraform`, `compose`, `helm`) and any dependencies

Then generate the file at `deploy/<name>.yaml` with:
- Each namespace as a `spec.namespaces` entry with `name` and `file`
- Each stage as a `spec.stages` entry with `name`, `type`, and optional `depends_on`

After writing the file, validate it:
```bash
strata validate deploy/<name>.yaml
```

Then show the user what a dry-run build would produce:
```bash
strata build run -f deploy/<name>.yaml --dry-run
```

If either step fails, show the errors and fix them before finishing.
