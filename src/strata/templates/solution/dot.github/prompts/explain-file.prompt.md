---
agent: agent
description: "Explain what a YAML file does and show its relationships"
---

Explain a strata YAML configuration file to the user.

Ask the user: **Which file do you want me to explain?** (provide a file path relative to the workspace root, e.g., `config/main.yaml` or `deploy/production.yaml`)

## Step 1: Validate the file

```bash
strata validate <file>
```

If there are errors, show them and ask if you should fix them first, or proceed with explanation anyway.

## Step 2: Get file details

```bash
strata guide show --file <file>
```

Parse the output to extract:
- Document kind (e.g., `deployment`, `configuration`, `environment`)
- Document name (from `meta.name`)
- Validation status
- Any cross-file references (files that this one depends on, or files that depend on this one)

## Step 3: Get the schema

```bash
strata schema get <kind>
```

Use the schema to explain:
- What this kind of document is for (from schema `description`)
- What the main `spec` fields mean
- Which fields are required vs optional

## Step 4: Explain the document

Create a summary like:

> **File:** `config/main.yaml`
> **Kind:** Configuration
> **Name:** `platform-base`
> **Purpose:** [from schema]
> **Status:** ✅ Valid
>
> **Key fields:**
> - `spec.namespaces` — [description]
> - `spec.modules` — [description]
> - `spec.resources` — [description]
>
> **References:**
> - This file is included in: `deploy/main.yaml`
> - This file depends on: `@infra/modules/api.yaml`, `@infra/modules/database.yaml`
>
> **Next steps:** [suggest what to do with this file — edit it, add it to a profile, build it, etc.]

## Step 5: Offer next actions

Ask: "What would you like to do with this file?"
- **Edit it** — open the file
- **Validate it** — run validation
- **Add to profile** — register with a profile
- **Build with it** — run build if it's a deployment
- **See relationships** — show what depends on it

