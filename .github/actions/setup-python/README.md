# Setup Python

Install Python, uv, and sync project dependencies (`uv sync`). Used internally by strata's own CI — not a consumer-facing action (referenced via local `./` path, not published).

## Usage

```yaml
- uses: ./.github/actions/setup-python
  with:
    python-version: "3.13"
```

## Inputs

| Input            | Required | Default | Description               |
| ---------------- | -------- | ------- | ------------------------- |
| `python-version` | No       | `3.13`  | Python version to install |

## Notes

This is required before `test-python`. Both actions are internal to strata's own `ci-build.yml` — consumer repos building strata deployments should use `setup-strata` instead.
