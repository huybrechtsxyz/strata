# strata — Contributing

Thanks for your interest in contributing! This document covers the workflow, conventions, and architecture rules you need to contribute effectively to strata.

---

## Get Started

1. Fork the repo and create a branch from `main`: `git checkout -b my-feature`
2. Keep changes focused — one logical change per PR.
3. Run tests and linters locally before opening a PR.
4. Update `CHANGELOG.md` under `[Unreleased]` for any user-visible change.

---

## Report Bugs & Request Features

- For usage questions, open a GitHub Discussion.
- For bugs, include: steps to reproduce, expected vs actual behaviour, `strata --version`, and relevant logs (`strata log list --last`).
- For features, describe the operator problem you're solving, not the implementation.

---

## Architecture Overview

strata uses a strict layered architecture. **Lower layers never import higher ones.**

```
commands/     ← Click CLI entry points — thin wrappers, call command classes
controllers/  ← Orchestrate services + integrations for a single operation
services/     ← Load, validate, and expose a single YAML model type
integrations/ ← Subprocess wrappers for external tools (git, terraform, etc.)
models/       ← Pydantic v2 models for YAML documents
utils/        ← Pure utilities (no business logic, no service imports)
```

### Adding a new CLI command

1. Create `src/strata/commands/<group>/<name>_command.py` extending `BaseCommand`.
2. Implement `get_required_integrations()` and `_execute()`. Do **not** override `execute()` — it is the concrete lifecycle orchestrator on the base class.
3. For commands that work without an initialized workspace (no `solution.json` required), override `_initialize()` to call `self._initialize_session()` and return its result.
4. Wire it up in `src/strata/commands/cli_<group>.py` with Click decorators.
5. Register the group in `src/strata/cli.py` if it's new.
6. Use `@click_work_path`, `@click_output_format`, `@click_output_verbose`, `@click_output_quiet` from `cli_common.py`.
7. Use `@click_file` from `cli_common.py` for `--file` options (includes `STRATA_FILE` env var automatically).
8. Never use `sys.exit()` — raise `click.exceptions.Exit(code)`.

### Adding a new integration

1. Create `src/strata/integrations/<name>.py` extending `BaseIntegration`.
2. Set `COMMAND = "<binary>"` for availability detection.
3. Implement `_check_version_command()` to return the version command args.
4. Register in `integrations/registry.py` and `integrations/factory.py`.
5. Add `CAPABILITIES` list from `integrations/capabilities.py` as appropriate.
6. Never call subprocess directly — use `self._run_integration(args, cwd, timeout)`.

### Adding a new model

1. Create `src/strata/models/<name>_model.py` extending `pydantic.BaseModel`.
2. Use `PlatformName` for `name` fields, `PlatformKind` for `kind`, `PlatformVersion` for `apiVersion`.
3. Use `model_validator(mode="after")` for cross-field validation.
4. Never call `Path.exists()` inside validators — models must load without a filesystem.
5. Add a corresponding service in `src/strata/services/`.

### Introducing a new convention

Before adding any new string syntax, field shape, naming rule, or other repeated
pattern (not just a one-off field): write an ADR, check whether an existing strata
mechanism or an industry standard already does the job before inventing one, and if
the same logic is needed in more than one place put it in exactly one shared
function — never a second hand-rolled copy. See
[docs/decisions/README.md#introducing-a-new-convention](../docs/decisions/README.md#introducing-a-new-convention).

---

## Exit Codes

| Code | Meaning                                         |
| ---- | ----------------------------------------------- |
| `0`  | Success                                         |
| `1`  | System / execution failure                      |
| `2`  | Usage error (Click default for bad arguments)   |
| `3`  | Validation failure — file processed but invalid |

Always use `handle_command_exit(command, success)` from `cli_common.py` to map to exit codes.

---

## Pull Requests

- Open against `main` with a clear title and description.
- Link related issues.
- PRs must pass CI (lint + type check + pytest) before review.
- Be responsive to review feedback.
- Update `CHANGELOG.md` under `[Unreleased]`.

---

## Review & Merging

- Maintainers will review PRs and may request changes.
- Merge is performed when CI passes and reviewers approve; maintainers may squash or rebase as needed.

---

## Security

- Do not include secrets, credentials, or private data in PRs or issues.
- For security vulnerabilities, follow SECURITY.md (do not open a public issue).

---

## Contributing Example Workspaces

The `config/` directory contains complete, validated reference workspaces that demonstrate strata for different cloud providers and deployment strategies. These serve as living documentation and are validated in CI.

**To add a new example workspace:**

1. Create a folder under `config/` named after the pattern: `<cloud>-<orchestrator>` (e.g., `aws-eks`, `hetzner-compose`, `azure-aks`).
2. Include the full dependency chain:
   - `config/` — configuration file (provisioners, providers, remotes)
   - `environments/` — at least one environment (e.g., `env-dev.yaml`)
   - `stack/` — workspace + resources + namespaces + modules
   - `deployments/` — at least one deployment tying it together
3. Every YAML file must pass `strata validate`. CI runs validation on all files in `config/`.
4. Add a `README.md` to your folder explaining: what it demonstrates, prerequisites (e.g., Terraform, Helm), and how to `strata build plan` against it.
5. Use realistic but non-sensitive values (fake subscription IDs, placeholder domains).

**Guidelines:**
- Keep examples minimal but complete — enough to validate end-to-end, not a production-ready setup.
- Use `provisioner:` on stages, never `type:`.
- Use `secret:` refs for credentials, never plain values.
- Reference the example from `docs/skills/strata-onboarding.md` if it demonstrates a new pattern.

---

## Questions & Support

- See [SUPPORT](./support.md) for where to get help and expected response times.

Thank you for contributing!

---

## Developer Setup

### Install dependencies (with dev extras)

**Linux / macOS:**
```bash
uv sync --group dev
source .venv/bin/activate
```

**Windows:**
```powershell
uv sync --group dev
.venv\Scripts\Activate.ps1
```

### Linting and type checks

| Tool                                           | Purpose             | Command                                                |
| ---------------------------------------------- | ------------------- | ------------------------------------------------------ |
| [`ruff`](https://docs.astral.sh/ruff/)         | Linter + formatter  | `ruff check .` / `ruff check . --fix && ruff format .` |
| [`mypy`](https://mypy-lang.org/)               | Static type checker | `mypy .`                                               |
| [`yamllint`](https://yamllint.readthedocs.io/) | YAML linter         | `yamllint .`                                           |

### Tests

```bash
# Run all tests
uv run pytest tests/ --no-cov -q

# Run with coverage
uv run pytest tests/ --cov=strata --cov-report=term-missing
```

### Nox (all checks in one command)

[`nox`](https://nox.thea.codes/) runs all tools in an isolated Python 3.13 environment.

```bash
nox              # run everything (lint + tests + build)
nox -s lint      # ruff + yamllint + mypy only
nox -s tests     # pytest only
nox -s build     # package build only
nox -s lock_check  # verify uv.lock is up to date
```

To auto-fix style issues via nox:
```bash
FIX=1 nox -s lint
```

### Lockfile

`uv.lock` pins all transitive dependencies to exact versions. After pulling new commits or updating a dependency, run:
```bash
uv sync
```

### Build documentation (Sphinx)

```bash
make          # generates HTML docs in html_docs/
make clean    # remove generated docs
```

Open `html_docs/index.html` in a browser to preview. In VS Code, the
[Open HTML In Browser](https://marketplace.visualstudio.com/items?itemName=peakchen90.open-html-in-browser)
extension lets you right-click `index.html` → "Open in Default Browser".
