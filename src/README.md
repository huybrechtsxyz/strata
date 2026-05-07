# src/

Python source tree for the `xyz-platform` package.

The installable package lives in `src/xyz_platform/` and is managed with `uv`. The `xyz.code-workspace` and `pyproject.toml` at the repo root both point here.

## Package layout

```
src/xyz_platform/
├── cli.py              # Click entry point — registers all command groups
├── commands/           # Click wiring (thin wrappers that call BaseCommand subclasses)
├── controllers/        # Orchestration — one controller per operation domain
├── services/           # Load, validate, and expose a single YAML model type each
├── integrations/       # Subprocess / SDK wrappers for external tools
│   ├── git.py
│   ├── terraform.py
│   ├── docker.py
│   ├── bitwarden.py
│   ├── hashicorp_vault.py
│   ├── hashicorp_consul.py
│   ├── azure_keyvault.py
│   └── azure_appconfig.py
├── models/             # Pydantic v2 models for all YAML document types
├── builders/           # Build-phase logic (platform artifact generation)
├── deployers/          # Deploy-phase logic
├── validators/         # Cross-file and cross-repo validation rules
├── exceptions/         # PlatformError hierarchy
├── logger/             # structlog configuration and context helpers
├── utils/              # Pure utilities (no business logic, no service imports)
├── data/               # Bundled static assets (help topics, etc.)
└── templates/          # Scaffold templates copied to workspaces on xyz init
```

## Dependency direction

Lower layers never import higher ones:

```
commands → controllers → services → models
                       ↘ integrations
utils / logger / exceptions  (imported by all layers)
```

## Development

```powershell
# Install dependencies
uv sync

# Run the CLI locally
uv run xyz-platform --help

# Lint + format + type-check
scripts/Check.ps1

# Tests
uv run pytest
```
