# Get Bitwarden Secrets

Fetch one or more secrets from Bitwarden Secrets Manager and expose them as masked environment variables. Generic — you supply the item-id-to-variable-name mapping; nothing is hardcoded to a specific secret.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/get-bitwarden-secrets@v1
  with:
    bitwarden_token: ${{ secrets.BITWARDEN_TOKEN }}
    secrets: |
      f16fffe2-77b7-4d20-bf6c-b2c9015c71d3 > DEPLOY_SSH_KEY
      a1b2c3d4-5678-90ab-cdef-1234567890ab > DB_PASSWORD

- name: Use the fetched secrets
  run: |
    echo "Key length: ${#DEPLOY_SSH_KEY}"
    # DB_PASSWORD is also available as an env var here
```

## Inputs

| Input             | Required | Default | Description                                                      |
| ----------------- | -------- | ------- | ---------------------------------------------------------------- |
| `bitwarden_token` | Yes      | —       | Bitwarden Secrets Manager access token                           |
| `secrets`         | Yes      | —       | Multi-line mapping: `<secret-id> > <ENV_VAR_NAME>`, one per line |

## Outputs

None. Fetched values are exported as environment variables (matching the names you specify in `secrets`), not as action outputs — composite actions can't declare a dynamic number of named outputs, and env vars are directly usable by every subsequent step without extra plumbing.

## Security

- Every fetched value is masked (`::add-mask::`) before it's written anywhere, so it never appears in logs even if a later step echoes it accidentally.
- Values are written to `$GITHUB_ENV` using a heredoc delimiter, so values containing newlines are stored verbatim rather than being split into unintended extra variables.

## Notes

This action is Bitwarden-specific — for checking which store integrations are configured and reachable (without fetching specific secret values), use `verify-integrations` instead.
