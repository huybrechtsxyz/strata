# Setup Strata

Install Python, uv, and the strata CLI. Required before any other strata action.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
  with:
    python-version: "3.13"
    strata-version: "latest"
```

## Inputs

| Input            | Required | Default  | Description                                                                                                             |
| ---------------- | -------- | -------- | ----------------------------------------------------------------------------------------------------------------------- |
| `python-version` | No       | `3.13`   | Python version to install                                                                                               |
| `strata-version` | No       | `latest` | Version to install: `latest`, `local` (from current repo), an exact version (`2.1.0`), or a PEP 440 specifier (`>=2.0`) |
| `strata-ref`     | No       | —        | Git branch/tag/SHA to install strata from. Overrides `strata-version` when set.                                         |

## Outputs

| Output    | Description                         |
| --------- | ----------------------------------- |
| `version` | The installed strata version string |

## Examples

```yaml
# Latest release
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1

# Pin an exact version
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
  with:
    strata-version: "2.1.0"

# Install from a specific branch (for testing unreleased changes)
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
  with:
    strata-ref: "feature/my-branch"
```
