# scripts/

PowerShell helper scripts for local development. Ported from strata v1's
`scripts/` folder, trimmed to what applies to v2 (no VS Code extension, no
state-service/dashboard, no ADR-0030-style migration guards).

| Script      | Purpose                                                                                          |
| ----------- | ------------------------------------------------------------------------------------------------ |
| `Setup.ps1` | One-time setup — creates the virtual environment and installs dependencies via `uv`.             |
| `Check.ps1` | Code quality gate — ruff lint, ruff format check, mypy, import-linter, pytest, smoke test, docs. |
| `Clean.ps1` | Removes `__pycache__`, `.pyc` files, and other build artefacts.                                  |
| `Docs.ps1`  | Builds the Sphinx documentation site into `docs/_build/html`.                                    |
| `Run.ps1`   | Thin wrapper that forwards all arguments to `uv run strata`.                                     |

Run any script from the repository root:

```powershell
.\scripts\Setup.ps1
.\scripts\Check.ps1
.\scripts\Run.ps1 version
```
