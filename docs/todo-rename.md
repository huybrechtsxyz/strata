# Rename: xyz-platform → ruck

## Context

- **Audience:** Internal company team managing Azure native + AKS environments
- **Goal:** Clean name that's fast to type, easy to remember, credible when pitching internally
- **Approach:** Clean cut in one commit — no deprecation period, no dual-support fallback

## Naming Convention

| Layer                   | Current              | New                                                              |
| ----------------------- | -------------------- | ---------------------------------------------------------------- |
| CLI command             | `xyz-platform`       | `ruck`                                                           |
| PyPI / internal package | `xyz-platform`       | `ruck` (internal feed) / `xyz-ruck` (if published to PyPI later) |
| Python import           | `xyz_platform`       | `ruck`                                                           |
| GitHub repo             | `xyz-platform`       | `ruck`                                                           |
| Env var prefix          | `XYZ_`               | `RUCK_`                                                          |
| Workspace marker        | `.platform/`         | `.ruck/`                                                         |
| Config file             | `.platform/cli.yaml` | `.ruck/cli.yaml`                                                 |

## Files to Rename/Update

### Package Structure
- `src/xyz_platform/` → `src/ruck/`
- `tests/xyz_platform/` → `tests/ruck/`
- `src/xyz_platform.egg-info/` → rebuild (auto-generated)
- `pyproject.toml` — name, scripts entry point, package discovery
- `package.json` — name field (if relevant)

### Entry Point
- `pyproject.toml` `[project.scripts]`: `xyz-platform = "xyz_platform.cli:main"` → `ruck = "ruck.cli:main"`

### Internal References
- All `from xyz_platform` / `import xyz_platform` → `from ruck` / `import ruck`
- Logger names: `xyz_platform.*` → `ruck.*`
- Exception hierarchy module paths
- `PlatformError` → `RuckError`
- `PlatformName` → `RuckName`
- `PlatformKind` → `RuckKind`
- `PlatformVersion` → `RuckVersion`
- `PlatformFileNotFoundError` → `RuckFileNotFoundError`
- `BaseService`, `BaseController`, `BaseCommand`, `BaseIntegration` — keep (not prefixed)

### Environment Variables
- `XYZ_WORK_PATH` → `RUCK_WORK_PATH`
- `XYZ_OUTPUT` → `RUCK_OUTPUT`
- `XYZ_<OPTION>` pattern → `RUCK_<OPTION>`

### Workspace State
- `.platform/` directory → `.ruck/`
- `solution.json`, `cli.yaml`, `platform.json` inside it
- `PlatformArtifactModel` → `RuckArtifactModel`

### Documentation (full rewrite needed)

#### `docs/platform/` — CLI & Architecture docs
- `docs/platform/architecture.md` — update all references to "xyz-platform", module paths
- `docs/platform/commands.md` — all CLI examples (`xyz validate` → `ruck validate`, etc.)
- `docs/platform/cli-preferences.md` — env var names, config file paths
- `docs/platform/configuration.md` — workspace marker references
- `docs/platform/exceptions.md` — class names (PlatformError → RuckError)
- `docs/platform/exit-codes.md` — CLI name in examples
- `docs/platform/integrations.md` — references to the tool name
- `docs/platform/lifecycles.md` — CLI command references
- `docs/platform/logging.md` — logger name prefix
- `docs/platform/models.md` — PlatformName/Kind/Version references
- `docs/platform/services.md` — module paths
- `docs/platform/builders.md` — CLI examples
- `docs/platform/deployers.md` — CLI examples
- `docs/platform/validators.md` — CLI examples
- `docs/platform/workflow.md` — end-to-end CLI examples
- `docs/platform/utilities.md` — module paths
- `docs/platform/readme.md` — intro/overview

#### `docs/config/` — YAML config schema docs
- `docs/config/workspace.md` — `.platform/` → `.ruck/` references
- `docs/config/configuration.md` — apiVersion references
- `docs/config/deployment.md` — CLI command examples
- `docs/config/environment.md` — CLI command examples
- `docs/config/readme.md` — overview
- All other config docs — scan for `xyz-platform`, `xyz`, `.platform/` references

#### Other docs
- `docs/conf.py` — Sphinx project name
- `docs/index.rst` — title, intro
- `docs/README.md` — overview
- `docs/todo-agent.md` — CLI name references

#### Repo-root docs
- `README.md` — full rewrite of intro, install instructions, examples
- `.github/copilot-instructions.md` — all xyz-platform references, module paths, env vars
- `Dockerfile.cli` — pip install name, entry point
- `Dockerfile.docs` — package name
- `.github/` workflows — package name references

### YAML Documents
- `apiVersion: platform.huybrechts.xyz/v1` → `apiVersion: ruck.huybrechts.xyz/v1`
- Update all YAML files in `config/` and `xyz-configuration` repo

### Configuration Repo (xyz-configuration)
- Update any CLI command references in scripts or docs
- Update `apiVersion` in all YAML files
- No structural rename needed — it remains a consumer

## Migration Strategy

1. Do the rename in one commit (clean cut)
2. No deprecation — only user is us, coordinate with the team
3. Find-and-replace pass with verification: `grep -r "xyz_platform\|xyz-platform\|XYZ_\|\.platform/" src/ tests/ docs/`
4. Run full test suite after rename
5. Update CI/CD pipelines
6. Tag as v1.0.0 (fresh start)

## Open Questions

- [ ] Rename `docs/platform/` directory itself to `docs/cli/` or `docs/ruck/`?
- [ ] Keep `apiVersion` as `platform.huybrechts.xyz/v1` for backward compat with existing configs, or clean-cut to `ruck.huybrechts.xyz/v1`?
- [ ] Rename the `docs/config/` folder or keep as-is (it describes config file format, not the tool)?
