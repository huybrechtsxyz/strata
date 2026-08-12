# Setup Bitwarden

Install and authenticate the Bitwarden Secrets Manager CLI (`bws`). Sets `BWS_ACCESS_TOKEN` for subsequent steps.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-bitwarden@v1
  with:
    bitwarden_token: ${{ secrets.BITWARDEN_TOKEN }}

- run: bws secret get <secret-id>
```

## Inputs

| Input             | Required | Default | Description                            |
| ----------------- | -------- | ------- | -------------------------------------- |
| `bitwarden_token` | Yes      | —       | Bitwarden Secrets Manager access token |
| `bws-version`     | No       | `2.0.0` | `bws` CLI version to install           |

## Outputs

None. `BWS_ACCESS_TOKEN` is exported as an environment variable for the rest of the job.

## Notes

- Installs the Linux x86_64 build of `bws` — this action assumes an `ubuntu-latest` (or compatible) runner with `sudo`.
- To fetch specific secret values as environment variables (rather than just authenticating the CLI), use `get-bitwarden-secrets` instead — it wraps the official `bitwarden/sm-action` for that use case.
- The token is masked (`::add-mask::`) before being written anywhere, so it never appears in logs.
