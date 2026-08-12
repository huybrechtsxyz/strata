# Test Python

Run ruff lint, ruff format check, mypy type-check, and pytest. Used internally by strata's own CI — not a consumer-facing action (referenced via local `./` path, not published).

## Usage

```yaml
- uses: ./.github/actions/setup-python
- uses: ./.github/actions/test-python
```

## Inputs

None.

## Notes

Requires `setup-python` to have run first (needs `uv` and synced dependencies). This action runs strata's own test suite — it does not test consumer deployment files.
