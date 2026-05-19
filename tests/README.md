# tests/

Automated test suite for the `strata` CLI.

## Structure

```
tests/
├── data/               # Static YAML fixtures used by tests
│   ├── configurations/ # Configuration YAML samples
│   ├── deployments/    # Deployment YAML samples
│   ├── environments/   # Environment YAML samples
│   ├── firewalls/      # Firewall YAML samples
│   ├── modules/        # Module YAML samples
│   ├── namespaces/     # Namespace YAML samples
│   ├── providers/      # Provider YAML samples
│   ├── resources/      # Resource YAML samples
│   ├── solutions/      # Solution JSON samples
│   └── workspaces/     # Workspace YAML samples
├── scripts/            # Helper PowerShell scripts for smoke-testing the CLI
│   └── Test-Commands.ps1
└── STRATA_platform/       # Python unit/integration tests (mirrors src/ layout)
    ├── builders/
    ├── commands/
    ├── controllers/
    ├── deployers/
    ├── exceptions/
    ├── integrations/
    ├── models/
    ├── services/
    ├── utils/
    └── validators/
```

## Running

```powershell
# All tests
uv run pytest

# Specific module
uv run pytest tests/strata/commands/

# With short traceback
uv run pytest --tb=short

# CLI smoke tests (requires an initialised workspace)
pwsh tests/scripts/Test-Commands.ps1
```

## Conventions

- Test classes use plain `class Test<Subject>:` — no `unittest.TestCase`.
- CLI commands are tested via `click.testing.CliRunner` + `runner.invoke(main, [...])`.
- External tools and subprocess calls are always mocked — no real tool invocations.
- Exit codes are asserted explicitly (`assert result.exit_code == 0`).
- Fixture YAML files in `data/` are intentionally minimal; invalid variants sit alongside valid ones to exercise error paths.
