# XYZ Platform - Contributing

Thanks for your interest in contributing! This document describes the preferred workflow and guidelines to make collaboration smooth and efficient.

## Get started

- Fork the repo and create a branch from main: git checkout -b my-feature
- Keep changes focused: one logical change per PR.
- Write clear, descriptive commit messages.

## Report bugs & request features

- For usage questions or discussion, open a GitHub Discussion (preferred) or an Issue.
- For bugs, include steps to reproduce, expected vs actual behaviour, environment, and relevant logs or stack traces.

## Development workflow

- Run tests and linters locally before opening a PR.
- Add or update tests for new behavior.
- Update documentation where applicable (README, docs, CHANGELOG).

## Pull requests

- Open a PR against main with a clear title and description of the change.
- Link related issues and include screenshots or logs if relevant.
- PRs should pass CI checks before review.
- Be responsive to review feedback; maintainers may request changes.

## Code style

- Follow existing project style and patterns.
- Keep changes minimal and consistent with the repository conventions.

## Review & merging

- Maintainers will review PRs and may request changes.
- Merge is performed when CI passes and reviewers approve; maintainers may squash or rebase as needed.

## Security

- Do not include secrets, credentials, or private data in PRs or issues.
- For security vulnerabilities, follow SECURITY.md (do not open a public issue).

## Questions & support

- See [SUPPORT](./support.md) for where to get help and expected response times.

Thank you for contributing — your help improves Platform XYZ for everyone.

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
uv run pytest tests/ --cov=xyz_platform --cov-report=term-missing
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
