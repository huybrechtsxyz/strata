# Infisical (`store: infisical`)

Resolves `variable`/`secret` values from an [Infisical](https://infisical.com/) project —
strata talks to it over its REST API directly (no `infisical` CLI dependency).

## Prerequisites

- An Infisical project with the secrets/variables you want to use already populated.
- Either a service token, or a [machine identity](https://infisical.com/docs/documentation/platform/identities/universal-auth)
  set up for Universal Auth (client ID + client secret) — **not** a personal user login.

## Authentication — always an environment variable, never `spec.authentication`

`kind: integration`'s `spec.authentication` field is not used for Infisical, deliberately —
see [docs/work/store-integration-configuration.md](../work/store-integration-configuration.md#deliberately-out-of-scope)
for the full reasoning. In short: an `authentication` value would itself need to be a
`${secret:...}` reference, which would need to already be resolved before this very integration
can resolve anything — a real circularity, not just an inconvenience. Set these as real process
environment variables instead (a CI secret, a `.env` file your shell loads, your local machine's
own env — never committed to a strata document):

| Env var                                           | Required                                     | Purpose                                                                         |
| ------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------- |
| `INFISICAL_TOKEN`                                 | One of this or the two below                 | A service token, used directly as the bearer token                              |
| `INFISICAL_CLIENT_ID` + `INFISICAL_CLIENT_SECRET` | One of this pair or the token above          | Universal Auth (machine identity) — exchanged for an access token automatically |
| `INFISICAL_PROJECT_ID`                            | Yes                                          | Which Infisical project to read from                                            |
| `INFISICAL_ENVIRONMENT`                           | No — defaults to `prod`                      | Which Infisical environment slug to read from                                   |
| `INFISICAL_ADDR`                                  | No — defaults to `https://app.infisical.com` | Only needed for a self-hosted Infisical instance                                |

## Configuring the `kind: integration` document (optional)

Everything above already works with **no** `kind: integration` document at all — env vars alone
are enough. Declare one when you want the non-secret connection details (address, project,
environment) checked into the solution itself instead of living only in env vars:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: infisical-prod
spec:
  type: infisical
  capabilities: [variables, secrets]
  enabled: true
  endpoints:
    address: https://app.infisical.com
  configuration:
    project_id: "3fa1c2e4-9b8d-4e2a-8c1f-2a9d7e6b5c40"
    environment: prod
```

`INFISICAL_TOKEN` (or `INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET`) is **still required** as a
real env var either way — nothing in this document replaces the actual credential.

## Using it from an Environment

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  secrets:
    - key: DB_PASSWORD
      store: infisical
      value: db_password # the secret's name/path inside the Infisical project
  variables:
    - key: API_HOST
      store: infisical
      value: api_host
```

`value` is the secret/variable's own name inside Infisical — not a strata-declared key, and not
a literal value.

## Troubleshooting

- **"not authenticated"** — neither `INFISICAL_TOKEN` nor
  `INFISICAL_CLIENT_ID`+`INFISICAL_CLIENT_SECRET` is set in the process environment `strata` runs
  in.
- **"no secret named '...' in this project/environment"** — the key doesn't exist in the
  Infisical project/environment combination resolved (check `INFISICAL_PROJECT_ID`/
  `INFISICAL_ENVIRONMENT`, or the `kind: integration` document's `configuration` if you declared
  one).
- **Multiple candidates named '...' — set 'integration:' explicitly"** (once the binding design is
  implemented) — more than one enabled `kind: integration` document declares `type: infisical`.
  Disable all but one, or remove the extras — a store cannot name which one to use by itself (see
  the design doc's own "Why type-based auto-bind" section for why).
