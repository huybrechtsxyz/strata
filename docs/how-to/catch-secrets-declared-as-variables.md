# How To: Catch secrets declared as variables

A secret declared under `spec.variables` instead of `spec.secrets` loses a secret's protection:

- its value is written in clear to `strata.auto.tfvars.json` and Terraform state;
- with `store: constant` it sits in your git repository;
- it is not masked in diagnostics, deploy output or the generated documentation.

Nothing in the value tells strata it is a secret, but the key usually does: `DB_PASSWORD`,
`GIT_TOKEN`, `STORAGE_ACCOUNT_KEY`. `spec.security.disallowed_variable_keys` in the Configuration
document lists the keys that give such a mistake away, and `strata validate` reports every variable
or feature that uses one. It is off until you configure it, and strata ships no list of its own:
this guide gives a starting list and a way to roll it out. The reference is
[Keys a variable may not use](../config/configuration.md#keys-a-variable-may-not-use).

## 1. Add the block at `warn`

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: configuration
meta:
  name: platform
spec:
  security:
    disallowed_variable_keys:
      patterns:
        # Generic
        - "*PASSWORD*"
        - "*PASSWD*"
        - "*SECRET*"
        - "*TOKEN*"
        - "*CREDENTIAL*"
        # Keys and certificates
        - "*API_KEY*"
        - "*PRIVATE_KEY*"
        # Storage and connection data
        - "*ACCESS_KEY*"
        - "*ACCOUNT_KEY*"
        - "*CONNECTION_STRING*"
      exceptions:
        - "*_SECRET_ID"      # a Key Vault secret's id: it points at the secret, it does not hold it
      enforcement: warn
```

`warn` reports and blocks nothing, so the block can go in before anyone has looked at the results.
The example solution in this repository ([config/configuration.yaml](../../config/configuration.yaml))
uses this list, and a test keeps it free of false hits.

Patterns are globs (`*`, `?`, `[...]`) matched against the whole key, ignoring case, so `*TOKEN*`
matches `git_token` and `TOKEN_LIFETIME` alike. Add patterns for your own naming, for example
`"*_PAT"` for personal access tokens.

A specific pattern under a generic one adds nothing: `*SECRET*` already matches `CLIENT_SECRET`,
and `*TOKEN*` matches `SAS_TOKEN`. Add a specific pattern only in place of a generic one you
removed, for example `"*CERT_PASSWORD*"` if `*PASSWORD*` flags too much for you.

## 2. Run `strata validate` and sort the warnings

```text
$ strata validate
environments/prd.yaml
  warning  spec.variables[2].key: Variable 'DB_PASSWORD' (store 'constant') matches '*PASSWORD*' in the
           platform's disallowed_variable_keys: a name like that holds a secret, ... [variable_key_looks_secret]

  PASSED  36 documents checked - 1 warning
```

Every warning is one of three cases:

| The key holds...                                     | Fix                                                                       |
| ---------------------------------------------------- | ------------------------------------------------------------------------- |
| a real secret                                        | move it to `spec.secrets` with a secret store, and rotate the value if it was ever in git |
| the name, id or address of a secret, not the secret  | add the key, or a pattern for its kind, to `exceptions`                   |
| something unrelated that a pattern happens to match  | narrow the pattern, or add the key to `exceptions`                        |

A real secret moved to `spec.secrets`:

```yaml
spec:
  secrets:
    - key: DB_PASSWORD
      store: azure-keyvault
      value: db-password        # the name of the secret in the vault
```

Prefer an exception for one key or one well-named kind (`*_SECRET_ID`, `*_SECRET_NAME`,
`*_SECRET_URI`, `*_TOKEN_URL`). A broad exception such as `"*SECRET*"` undoes its own pattern.

## 3. Switch to `deny`

When `strata validate` shows no `variable_key_looks_secret` warning, set `enforcement: deny`. A key
that matches is then an error: `strata validate`, `build run` and `deploy run` fail, so a new
mistake cannot reach a deploy.

Keep the list tidy afterwards: an exception that matches no variable or feature key in any
environment document is reported as information (`disallowed_variable_keys_unused_exception`).
Remove it, so it cannot hide a future key by accident.

## Patterns to avoid

- **`*KEY*`** matches `KEY_VAULT_NAME`, `PARTITION_KEY` and `SSH_KEY_PATH`. Name the kind of key
  instead: `*API_KEY*`, `*ACCESS_KEY*`, `*ACCOUNT_KEY*`, `*PRIVATE_KEY*`.
- **`*PWD*`** matches a working-directory variable as well as a password. Add it only if your
  teams really abbreviate passwords that way.
- **`*PASS*`** matches `PASSTHROUGH` and `BYPASS_CACHE`; `*PASSWORD*` and `*PASSWD*` cover the
  real cases.
- **`*AUTH*`** matches `AUTHENTIK_HOST` and `OAUTH_ISSUER_URL`. Match the secret part
  (`*SECRET*`, `*TOKEN*`) rather than the protocol.

## Notes

- The check covers `spec.variables[].key` and `spec.features[].key` in every environment
  document, whatever the store. Secrets are never checked: a key under `spec.secrets` is where it
  belongs.
- It looks at keys only. A secret under an innocent key, such as `DB_SETTING`, is not caught; a
  secret scanner on the repository is the complement for values.
- The block belongs to the Configuration document, so one platform team sets it for every
  solution that uses that configuration.
