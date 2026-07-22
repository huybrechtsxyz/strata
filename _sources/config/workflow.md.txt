# Workflow Configuration

`.strata/workflow.yaml` defines the onboarding sequence that drives `strata guide`. Each step maps to a named check function and an optional strata command. The guide uses this file to power the checklist, next-step hints, and (when implemented) the `--next` and `--do` flags.

The file is created automatically by `strata sln init`. Customize it to match your team's workflow — add steps, reorder them, or change the suggested commands.

---

## Schema

```yaml
steps:
  - id: <unique_step_id>           # required — referenced in depends_on
    name: <human_label>            # required — shown in the checklist
    check: <check_function_name>   # required — see Built-in Checks below
    command: <strata command>      # optional — run by `do`; null for dynamic steps
    depends_on: [<step_id>, ...]   # optional — step IDs that must be ok first
    hint: <guidance text>          # optional — shown by `next`
    see_also: <reference>          # optional — doc link or help topic
    skippable: false               # optional — reserved for future use
```

### Field Reference

| Field        | Type             | Required | Description                                                                                                |
| ------------ | ---------------- | -------- | ---------------------------------------------------------------------------------------------------------- |
| `id`         | string           | ✅        | Unique identifier for this step. Used in `depends_on` references and cached checklist lookups.             |
| `name`       | string           | ✅        | Human-readable label shown in the console checklist (e.g. `Repositories registered`).                      |
| `check`      | string           | ✅        | Name of a built-in check function (see below). Determines the step's status: `ok`, `warn`, or `pending`.   |
| `command`    | string or `null` | —        | The strata command that `do` will execute for this step. Use `null` for steps with dynamic commands.       |
| `depends_on` | list of IDs      | —        | Step IDs that must have status `ok` before this step becomes active. Unmet dependencies show as `pending`. |
| `hint`       | string           | —        | Guidance text shown by `next`. Multi-line strings are supported (use YAML `\|` or `\n`).                   |
| `see_also`   | string           | —        | Reference link or help topic appended to the `next` output.                                                |
| `skippable`  | bool             | —        | Reserved. Has no effect in the current release.                                                            |

---

## Built-in Check Functions

The `check` field must be one of these names. Each function inspects the workspace state and returns `ok`, `warn`, or `pending`.

| Check name         | Returns `ok` when…                                                            |
| ------------------ | ----------------------------------------------------------------------------- |
| `solution_exists`  | `.strata/solution.json` exists (workspace has been initialized)               |
| `repos_registered` | At least one repository is registered in `solution.json`                      |
| `repos_cloned`     | All registered repositories are present on disk at their configured paths     |
| `profile_exists`   | At least one profile exists in `solution.json`                                |
| `profile_active`   | A profile is currently set as active                                          |
| `files_registered` | At least one configuration file reference is registered in the active profile |
| `build_exists`     | A build artifact exists (`.strata/build/` is non-empty)                       |
| `sbom_exists`      | An SBOM has been generated (`.strata/sbom/` is non-empty)                     |

Custom check functions are not supported in the current release.

---

## Default Workflow

The built-in default matches the 8-phase `strata guide` readiness checklist:

```yaml
steps:
  - id: workspace_init
    name: Workspace initialized
    check: solution_exists
    command: "strata sln init {name}"
    hint: "Initialize the workspace to create .strata/ and solution.json"
    see_also: "strata help --topic quickstart"

  - id: repos_registered
    name: Repositories registered
    check: repos_registered
    depends_on: [workspace_init]
    command: "strata repo add {name} {url}"
    hint: "Register at least one configuration repository"
    see_also: "strata help --topic repos"

  - id: repos_on_disk
    name: Repositories on disk
    check: repos_cloned
    depends_on: [repos_registered]
    hint: "Clone registered repositories to their configured paths"
    see_also: "strata help --topic repos"

  - id: profile_created
    name: Profile created
    check: profile_exists
    depends_on: [workspace_init]
    command: "strata profile add {name} --activate"
    hint: "Create a profile to organize file references"
    see_also: "strata help --topic profiles"

  - id: profile_activated
    name: Profile activated
    check: profile_active
    depends_on: [profile_created]
    command: "strata profile activate {name}"
    hint: "Activate a profile to set the working context"
    see_also: "strata help --topic profiles"

  - id: files_registered
    name: File references registered
    check: files_registered
    depends_on: [profile_activated]
    command: "strata ref config add {name} @{repo}/path/to/config.yaml --profile {active}"
    hint: "Register configuration files against the active profile"
    see_also: "strata help --topic environments"

  - id: build_exists
    name: Build artifact exists
    check: build_exists
    command: "strata build run"
    hint: "Run a build to generate deployment artifacts"
    see_also: "strata help --topic build"

  - id: inventory_generated
    name: Platform inventory generated
    check: sbom_exists
    depends_on: [build_exists]
    command: "strata build sbom -f {file}"
    hint: "Generate the platform inventory"
    see_also: "docs/platform/builders.md"
```

---

## Resolution Order

When the console loads, it looks for a workflow file in this order:

1. `.strata/workflow.yaml` in the current workspace — use this for project-specific customization
2. The built-in default shipped with strata (identical to the file above)

`strata sln init` writes the default workflow to `.strata/workflow.yaml` so it's immediately editable.

---

## Customization Examples

### Reorder steps

Put build before file registration if your workflow requires it:

```yaml
steps:
  - id: workspace_init
    name: Workspace initialized
    check: solution_exists
    command: "strata sln init {name}"

  - id: build_exists
    name: Build artifact exists
    check: build_exists
    depends_on: [workspace_init]
    command: "strata build run"
    hint: "Run a build to generate deployment artifacts"

  - id: files_registered
    name: File references registered
    check: files_registered
    depends_on: [workspace_init]
    command: "strata ref config add {name} @{repo}/path/to/config.yaml"
    hint: "Register configuration files against the active profile"
```

### Add a project-specific step

Reuse an existing check for a domain-specific action:

```yaml
  - id: terraform_docs
    name: Terraform docs generated
    check: build_exists          # reuse an existing check as a proxy
    depends_on: [build_exists]
    command: "terraform-docs markdown . > README.md"
    hint: "Generate Terraform module documentation"
    skippable: true
```

### Simplify for a compose-only project

Remove Terraform-specific phases and keep only what you need:

```yaml
steps:
  - id: workspace_init
    name: Workspace initialized
    check: solution_exists
    command: "strata sln init my-project"

  - id: profile_created
    name: Profile activated
    check: profile_active
    depends_on: [workspace_init]
    command: "strata profile add prd --activate"

  - id: build_exists
    name: Compose file built
    check: build_exists
    depends_on: [profile_created]
    command: "strata build run"
    hint: "Generate the docker-compose.yml artifact"
```

---

## Dynamic Steps

When `command` is `null` (or omitted), `do` cannot execute the step automatically. Instead, the console shows the `hint` text as instructions. The `repos_cloned` step uses this pattern — the hint is generated dynamically to show the exact `git clone` command for each missing repository.

```yaml
  - id: repos_on_disk
    name: Repositories on disk
    check: repos_cloned
    depends_on: [repos_registered]
    # no command — console shows git clone lines per missing repo
    hint: "Clone registered repositories to their configured paths"
```
