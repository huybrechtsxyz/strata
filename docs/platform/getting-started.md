# Getting Started

> **Audience:** DevOps engineer seeing `strata` (soon: `ruck`) for the first time. Goal: install the tool, initialize a config workspace, and validate a config file — in under 10 minutes.

---

## Prerequisites

| Tool                                                                       | Version | Why                                           |
| -------------------------------------------------------------------------- | ------- | --------------------------------------------- |
| Python                                                                     | 3.13+   | Runtime                                       |
| [pipx](https://pipx.pypa.io/) or [uv](https://docs.astral.sh/uv/)          | latest  | Package install                               |
| [Terraform](https://developer.hashicorp.com/terraform/install)             | 1.6+    | Infrastructure provisioning                   |
| [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) | latest  | Azure authentication                          |
| kubectl                                                                    | latest  | AKS cluster management (AKS deployments only) |

Not sure which tools you have installed? After initializing a workspace, run:

```bash
strata tools status --missing
```

This lists every external tool strata expects (Terraform, Azure CLI, kubectl, Helm, etc.) that is
not currently on your `PATH` — the fastest way to find out what's missing on day one.

---

## Install

**Recommended (isolated install):**

```bash
pipx install xyz-strata
strata --version
```

**Dev install (from source):**

```bash
git clone git@github.com:org/strata.git
cd strata
uv sync
uv run strata --version
```

**Dev Container / no PyPI access:**

If your repo already uses a Dev Container and you cannot reach PyPI, inject strata directly from the published container image — no Python install needed:

```dockerfile
# .devcontainer/Dockerfile
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

# Copy the pre-built strata venv from the GHCR image
COPY --from=ghcr.io/huybrechtsxyz/strata:latest /app/.venv /opt/strata

ENV PATH="/opt/strata/bin:$PATH"
```

```json
// .devcontainer/devcontainer.json
{
  "name": "my-repo",
  "build": { "dockerfile": "Dockerfile" },
  "remoteUser": "vscode"
}
```

Open the repo in VS Code → **Reopen in Container** → `strata --help` works immediately.

**Air-gapped / private registry:**

If you also cannot reach GHCR, mirror the image to your own registry first:

```bash
docker pull ghcr.io/huybrechtsxyz/strata:latest
docker tag  ghcr.io/huybrechtsxyz/strata:latest myregistry.example.com/strata:latest
docker push myregistry.example.com/strata:latest
```

Then reference `myregistry.example.com/strata:latest` in the `COPY --from` line above. No outbound internet required at container build time.

---

## Initialize a Workspace

Run `strata sln init` inside your config repository (the repo that holds your environment YAML files):

```bash
cd my-config-repo
strata sln init --name my-workspace
```

What gets created:

```
my-config-repo/
├── .strata/
│   ├── solution.json      ← workspace registry (solution model)
│   ├── cli.yaml           ← your persisted CLI preferences
│   └── logging.yaml       ← log level and output config
└── .devcontainer/
    ├── devcontainer.json  ← VS Code Dev Container definition
    └── post-create.sh     ← post-create setup script
```

> **VS Code users:** After init, use **Reopen in Container** to get a fully configured dev environment with all tools pre-installed.

### Scaffold with a template

Add `--template <name>` to get a ready-to-edit config folder alongside the workspace state:

```bash
strata sln init --name my-aks --template aks
```

This copies a working set of example config files into the repo root, with `my-aks` substituted wherever the template uses `{{ solution_name }}`:

```
my-config-repo/
├── config/
│   └── my-aks-config.yaml     ← platform configuration (providers, integrations)
├── stack/
│   ├── ws-platform.yaml       ← workspace definition
│   ├── ns-base.yaml           ← base namespace
│   └── mod-traefik.yaml       ← Traefik Helm module
├── deploy/
│   └── deploy-prd.yaml        ← production deployment descriptor
├── envs/
│   └── env-prd.yaml           ← environment variables (fill in secrets)
└── .strata/                   ← workspace state (as above)
```

The output tells you exactly what to do next:

```
✅  Solution 'my-aks' initialised
    • Work path    : /path/to/my-config-repo
    • Solution ID  : abc-123
    • Template     : aks
    • Files created: 6

Next steps:
    1. Register your repo:   strata repo add my-aks <git-url> --clone
    2. Add a profile:        strata profile add prd --activate
    3. Validate:             strata validate --file deploy/deploy-prd.yaml
    4. Deploy:               strata deploy run --file deploy/deploy-prd.yaml
```

**Built-in templates:**

| Name      | Description                                         |
| --------- | --------------------------------------------------- |
| `aks`     | Azure Kubernetes Service — Terraform + Helm starter |
| `compose` | Docker Compose / Swarm services starter             |

**Using a local template folder:**

```bash
strata sln init --name my-ws --template ./my-corporate-template/
```

The folder must contain a `scaffold/` subdirectory with the files to copy. An optional `template.yaml` declares the template name, description, and substitution variables.

### Save your workspace as a template

Once you have a working workspace, capture it as a reusable scaffold for the next project:

```bash
strata sln export --name my-corp-base
```

What it does:
- Copies all workspace config files into `.strata/templates/my-corp-base/scaffold/`
- Replaces the current solution name with `{{ solution_name }}` throughout — ready to substitute on next init
- Shows every substitution so you can spot false positives

Output:
```
✅  Template 'my-corp-base' exported
    • Output       : .strata/templates/my-corp-base/
    • Files saved  : 6
    • Substitutions: 14 occurrences across 6 files

Use it:
    strata sln init --name new-project --template .strata/templates/my-corp-base/
```

Use `--dry-run` to preview without writing. Use `--force` to overwrite an existing template.

### After upgrading strata

When you upgrade the `strata` package, run `sln update` to refresh package-owned files
(schemas, templates, devcontainer config) in your workspace:

```bash
strata sln update
```

User-owned files (`.strata/cli.yaml`, `.strata/logging.yaml`, your `README.md`,
`.vscode/` settings) are never overwritten. See `strata sln update` in the
[commands reference](commands.md#sln-update) for the full list of affected files.

---

## File Structure

Config files follow a Kubernetes-style format (`apiVersion`, `kind`, `meta`, `spec`):

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: my-environment
  annotations:
    description: Production AKS cluster — EU West
spec:
  profile: prd
  workspace: "@xyz-config/config/xyz-config.yaml"
  modules:
    - "@xyz-config/stack/ns-base.yaml"
    - "@xyz-config/stack/mod-traefik.yaml"
```

Cross-repository file references use `@repo-name/path/to/file.yaml` notation. The `@` prefix tells `strata` to resolve the path through the registered repo map.

Typical workspace layout:

```
my-config-repo/
├── stack/          ← reusable module and namespace configs
├── deploy/         ← environment deployment files (one per env)
├── envs/           ← environment-specific variable overrides
└── config/         ← global configuration (workspace-level)
```

---

## Register Repositories

If your config references files from other repos (Terraform, service configs), register them:

```bash
strata repo add xyz-config git@github.com:org/xyz-config.git --branch main --clone
strata repo add xyz-infrastructure git@github.com:org/xyz-infrastructure.git --branch main --clone
```

---

## Set an Active Profile

Profiles group environment-specific overrides (think: `dev`, `staging`, `prd`):

```bash
strata profile add prd --activate
strata profile list
```

---

## Validate

Before deploying anything, validate your config file:

```bash
strata validate --file stack/my-environment.yaml
```

A clean run exits `0` and prints a summary. A validation failure exits `3` and tells you:

- which file failed
- which field is invalid
- what the valid options are

Run against all files to catch cross-reference errors early:

```bash
strata validate --file deploy/my-environment.yaml
```

---

## Preview Changes

Before deploying, review what would change:

```bash
strata build plan -f deploy/my-environment.yaml
```

This is read-only — nothing is modified. It builds artifacts to a temp directory, diffs against the current build, and runs `terraform plan` against remote state. Review the output, then deploy when satisfied.

---

## Deploy

Once validation passes:

```bash
strata deploy run --file deploy/my-environment.yaml
```

This orchestrates the full lifecycle: resolves `@`-references, runs Terraform, applies Helm charts, and executes any pre/post scripts. Terraform output streams directly to your terminal.

---

## If Something Goes Wrong

**Check the execution log** — every command execution is logged:

```bash
strata log list
strata log list --last
```

To configure logging behaviour (levels, output format), use `strata config log`.

**Run with verbose output** to see every subprocess call and argument:

```bash
strata validate --file stack/my-environment.yaml --verbose
strata deploy run --file deploy/my-environment.yaml --verbose
```

**Structured output for automation or debugging:**

```bash
strata validate --file stack/my-environment.yaml --output json
```

**Diagnose value resolution** — see exactly where each variable, secret, and feature flag comes from and whether it resolved:

```bash
strata values resolve -f deploy/my-environment.yaml
strata values resolve -f deploy/my-environment.yaml -k MY_SECRET  # single key
strata values resolve -f deploy/my-environment.yaml --probe       # also test backend reachability
```

Exit code `3` means one or more values failed to resolve.

---

## Persist Your Preferences

Stop passing the same flags on every command:

```bash
strata config set output json      # always use JSON output
strata config set verbose true     # always show verbose logs
strata config list                 # check what's set
```

---

## Shell Completion

Enable Tab completion for all commands, subcommands, and options:

**Bash** — add to `~/.bashrc`:

```bash
eval "$(_STRATA_COMPLETE=bash_source strata)"
```

**Zsh** — add to `~/.zshrc`:

```bash
eval "$(_STRATA_COMPLETE=zsh_source strata)"
```

**Fish** — add to `~/.config/fish/completions/strata.fish`:

```fish
_STRATA_COMPLETE=fish_source strata | source
```

**Faster startup** (generate a static script instead of eval on every shell open):

```bash
_STRATA_COMPLETE=bash_source strata > ~/.strata-complete.bash
echo ". ~/.strata-complete.bash" >> ~/.bashrc
```

After reloading your shell, `strata <TAB>` completes commands and `--<TAB>` completes options.

---

## Next Steps

- [Workflow Guide](workflow.md) — full lifecycle walkthrough with real repo examples
- [CLI Reference](commands.md) — every command and flag documented
- [Configuration Format](../config/configuration.md) — YAML schema reference

> **Tip:** Use `strata new --list` to see all built-in config file templates, then
> `strata new namespace my-ns` (or any other kind) to scaffold a new YAML file with
> `meta.name` pre-filled and all spec fields as editable placeholders.
