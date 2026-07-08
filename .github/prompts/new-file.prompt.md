---
agent: agent
description: "Scaffold a new strata YAML file for any kind"
---

Help the user create a new strata YAML configuration file.

## Step 1: Ask which kind

Ask the user: **What kind of file do you want to create?**

Available kinds:
- `configuration` — Compose namespaces, modules, resources, providers
- `deployment` — Define deployment stages and orchestration
- `environment` — Environment-specific variable overrides
- `module` — Reusable infrastructure module
- `namespace` — Logical group of resources
- `provider` — Provider configuration (terraform, helm, etc.)
- `resource` — Single infrastructure resource
- `firewall` — Network firewall rules
- `network` — Network definition
- `dns` — DNS zone configuration
- `tenant` — Multi-tenant setup
- `workspace` — Workspace-wide configuration

Or if the user is not sure, suggest: *"Most people start with `configuration` to define their infrastructure, then `deployment` to orchestrate it."*

## Step 2: Get the schema

```bash
strata schema get <kind>
```

Use this to show the user what fields are available and required. Ask:
- **Name** — unique identifier for this file (e.g., `platform-base`, `dev-env`)
- **Description** — what this file is for
- Any kind-specific questions (e.g., for deployment: "what stages?" / for environment: "what variable overrides?")

## Step 3: Scaffold the file

```bash
strata new <kind> --name <name>
```

This creates a template YAML file with:
- `apiVersion: strata.huybrechts.xyz/v1`
- `kind: <kind>`
- `meta.name: <name>`
- `meta.annotations.description: <description>`
- Basic `spec:` structure with placeholder fields

Show the user the generated file path (e.g., `config/platform-base.yaml`).

## Step 4: Edit and customize

Ask the user if they want to edit the file now. If yes:
- Open the file in their editor
- Walk them through the required fields
- Help them understand what each field means (reference the schema)

## Step 5: Validate

```bash
strata validate <file>
```

If valid ✅: *"File is valid and ready to use."*
If errors: Show each error and help fix them.

## Step 6: Next steps

Ask: **What next?**
- **Register with profile** — `strata profile env add dev <file>`
- **Add to a deployment** — reference this file in `deploy/main.yaml`
- **Build and test** — `strata build run -f deploy/main.yaml --dry-run`
- **Create more files** — scaffold another configuration file
