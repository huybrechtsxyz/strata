# scripts/

PowerShell helper scripts for local development.

| Script        | Purpose                                                                                                      |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| `Setup.ps1`   | One-time setup — creates the virtual environment and installs dependencies via `uv`.                         |
| `Check.ps1`   | Code quality gate — runs Ruff lint, Ruff format check, and Mypy type check. Equivalent to a build step.      |
| `Tests.ps1`   | Manual test reference — end-to-end CLI workflow examples and per-command variations for exploratory testing. |
| `Clean.ps1`   | Removes `__pycache__`, `.pyc` files, and other build artefacts.                                              |
| `Docs.ps1`    | Builds the Sphinx documentation site.                                                                        |
| `Run.ps1`     | Thin wrapper that forwards all arguments to `uv run strata`.                                           |
| `Release.ps1` | Bumps `VERSION.txt`, commits, and creates an annotated git tag. Run `git push origin main --tags` after.     |

Run any script from the repository root:

```powershell
.\scripts\Setup.ps1
.\scripts\Check.ps1
.\scripts\Tests.ps1
```
