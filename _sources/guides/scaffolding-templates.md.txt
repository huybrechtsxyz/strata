# Scaffolding Templates

> **Audience:** Operators who need to scaffold new configuration files, zones, or tenants
> from a consistent template — whether for a single file or an entire multi-tenant directory tree.

---

## Overview

The `strata new` command creates pre-filled configuration files from templates. There are three
layers of templates, each more powerful than the last:

| Layer                    | Source                             | Best for                                             |
| ------------------------ | ---------------------------------- | ---------------------------------------------------- |
| **1 — Single file**      | Package or workspace `.yaml` file  | One-off file creation                                |
| **2 — Bundle directory** | `.strata/templates/<name>/` folder | Multi-file scaffolds (e.g. a full environment stack) |
| **3 — Solution bundle**  | `solution.json spec.templates[]`   | Multi-path, multi-tenant fleet scaffolding           |

Templates use **Jinja2** syntax for variable substitution: `{{ variable_name }}`.

---

## Layer 1 — Single-file templates

The simplest case: scaffold one YAML file from a template.

```bash
# List all available templates
strata new --list

# Create a file from the built-in 'namespace' template
strata new --template namespace --name my-namespace

# Write to a specific directory
strata new --template namespace --name my-namespace --path config/namespaces/

# Supply variables inline (skip interactive prompt)
strata new --template environment --name dev --set region=westeurope
```

Template files live in:
- **Package built-ins:** shipped with strata (`strata new --list` shows them)
- **Workspace overrides:** `.strata/templates/<name>.yaml` — takes precedence over built-ins

Variables in the template file (`{{ name }}`, `{{ region }}`, etc.) are filled from:
1. `--set KEY=VALUE` flags (highest priority)
2. `spec.context` in `solution.json` (team-shared defaults)
3. Interactive prompt for anything still missing (Ctrl+C to cancel)

---

## Layer 2 — Bundle directory templates

A bundle directory contains multiple files that are all copied and rendered together.
This is useful when a logical unit requires more than one file (e.g. a stack with a
workspace, deployment, and environment file).

**Structure:**

```
.strata/templates/
└── mystack/
    ├── template.yaml          # optional: name, description, variables
    └── scaffold/
        ├── config/
        │   └── {{ name }}-config.yaml
        ├── deploy/
        │   └── deploy-{{ name }}.yaml
        └── environments/
            └── env-{{ name }}.yaml
```

`template.yaml` (optional manifest):

```yaml
name: mystack
description: Full stack — config, deployment, and environment
variables:
  - name: name
    description: Short name for this stack
```

**Usage:**

```bash
strata new --template mystack --name acme
```

This copies every file under `scaffold/`, rendering `{{ name }}` in both path segments
and file content. Output goes to the current directory (or `--path` if given).

Variable names can also appear in **directory and file names** — the entire relative path
is rendered before writing.

---

## Layer 3 — Solution bundles

Solution bundles are defined in `solution.json` and describe **where** to write templates
rather than what to copy. This enables a single `strata new` command to scaffold
files into multiple locations at once — the foundation for fleet and multi-tenant workflows.

### Defining solution bundles

Edit `.strata/solution.json` and add a `spec.templates` section:

```json
{
  "spec": {
    "templates": [
      {
        "name": "customer",
        "bundle": [
          {
            "name": "customer",
            "path": "customers/{{ shortname }}"
          },
          {
            "name": "bootstrap",
            "path": "zones/{{ zonecode }}/customers/{{ shortname }}"
          },
          {
            "name": "environment",
            "path": "zones/{{ zonecode }}/customers/{{ shortname }}/{{ environment }}"
          }
        ]
      },
      {
        "name": "zones",
        "bundle": [
          {
            "name": "zone",
            "path": "zones/{{ zonecode }}"
          }
        ]
      }
    ]
  }
}
```

Each entry in `bundle` has:
- **`name`** — the template source to look up via the standard resolution chain (layers 1–2)
- **`path`** — Jinja2 expression for the **destination directory**, relative to the work path

### How resolution works

When you run `strata new --template customer`, the CLI:

1. Finds `"customer"` in `solution.json spec.templates[]`
2. Collects all Jinja2 variables across every bundle path: `shortname`, `zonecode`, `environment`
3. Subtracts any already supplied via `--set` or `solution.json spec.context`
4. **Prompts interactively** for whatever remains (Ctrl+C cancels cleanly, nothing is written)
5. For each bundle entry:
   - Renders the `path` with all collected variables → destination directory
   - Resolves the `name` as a template source (`.strata/templates/<name>/` or package file)
   - Copies and renders all template files into the destination directory

### Usage

```bash
# Scaffold a new zone
strata new --template zones

  zonecode: eu-west

# → creates: zones/eu-west/

# Scaffold a new customer (prompts for all variables)
strata new --template customer

  shortname: acme
  zonecode: eu-west
  environment: production

# → creates:
#   customers/acme/
#   zones/eu-west/customers/acme/
#   zones/eu-west/customers/acme/production/

# Supply all variables inline — no prompts
strata new --template customer --set shortname=acme --set zonecode=eu-west --set environment=production
```

---

## Fleet and multi-tenant patterns

Solution bundles are the building block for operating strata at scale.

### Recommended directory layout

```
<workspace root>/
├── customers/
│   ├── acme/            ← customer definition files
│   └── contoso/
├── zones/
│   ├── eu-west/         ← zone bootstrap files
│   │   └── customers/
│   │       ├── acme/    ← customer bootstrap in this zone
│   │       │   └── production/  ← environment files
│   │       └── contoso/
│   └── us-east/
│       └── customers/
│           └── acme/
└── .strata/
    └── solution.json
```

### Onboarding a new customer

```bash
# 1. Add a new zone (if it doesn't exist yet)
strata new --template zones --set zonecode=us-east

# 2. Onboard the customer into that zone
strata new --template customer \
  --set shortname=contoso \
  --set zonecode=us-east \
  --set environment=production
```

### Onboarding a customer into multiple zones

Run `strata new --template customer` once per zone:

```bash
for zone in eu-west us-east ap-southeast; do
  strata new --template customer \
    --set shortname=acme \
    --set zonecode=$zone \
    --set environment=production
done
```

### Team-shared variable defaults

Variables that are the same for every operator on the team can be committed to
`solution.json spec.context` so they never need to be typed:

```json
{
  "spec": {
    "context": {
      "environment": "production"
    }
  }
}
```

With this set, `strata new --template customer --set shortname=acme --set zonecode=eu-west`
will not prompt for `environment` — it comes from the shared context.

---

## Template resolution order (full)

When a template name is given, the CLI searches in this order and uses the first match:

| Priority | Source                    | Location                                  |
| -------- | ------------------------- | ----------------------------------------- |
| 0        | **Solution bundle**       | `solution.json spec.templates[name]`      |
| 1        | **Workspace bundle dir**  | `.strata/templates/<name>/`               |
| 2        | **Workspace single file** | `.strata/templates/<name>.yaml`           |
| 3        | **Package bundle dir**    | Built-in bundle shipped with strata       |
| 4        | **Package single file**   | Built-in `.yaml` file shipped with strata |

Solution bundles (priority 0) define *routing* — where files land. The actual template
content always comes from priorities 1–4.

---

## Adding new template sources

### Add a workspace single-file template

Create `.strata/templates/<name>.yaml`. Use `{{ variable }}` for substitution.

### Add a workspace bundle directory

```
.strata/templates/
└── <name>/
    ├── template.yaml      # optional manifest
    └── scaffold/
        └── ...            # files and folders to copy
```

### Add a solution bundle

Edit `solution.json` directly — add an entry to `spec.templates[]`. No CLI command
exists yet for this; it is a hand-authored section of the solution state file.

Each bundle entry's `name` must match an existing template source (priorities 1–4 above).
