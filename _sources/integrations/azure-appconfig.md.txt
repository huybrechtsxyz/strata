# Azure App Configuration (`store: azure-appconfig`)

Resolves `variable`/`feature` values from an
[Azure App Configuration](https://learn.microsoft.com/azure/azure-app-configuration/) store — via
the real Azure SDK (`azure-appconfiguration`), not the `az` CLI.

## Prerequisites

- An Azure App Configuration store with the keys you want to use already populated.
- The identity strata runs as must have `App Configuration Data Reader` (or equivalent) access on
  that store — see [Authentication](#authentication) below.

## Authentication — ambient, via `DefaultAzureCredential`, never `spec.authentication`

Identical story to [Azure Key Vault](azure-keyvault.md#authentication-ambient-via-defaultazurecredential-never-spec-authentication) —
`kind: integration`'s `spec.authentication` is not used here either, for the same reason (see
[docs/design/store-integration-configuration.md](../design/store-integration-configuration.md#deliberately-out-of-scope)).
`DefaultAzureCredential` chains through managed identity automatically, with nothing to declare in
the common case.

| Env var                    | Required                                      | Purpose                                                    |
| -------------------------- | --------------------------------------------- | ---------------------------------------------------------- |
| `AZURE_APPCONFIG_ENDPOINT` | Yes                                           | The store's endpoint, e.g. `https://my-config.azconfig.io` |
| `AZURE_CLIENT_ID`          | Only for a **user-assigned** managed identity | Picked up automatically by `DefaultAzureCredential` itself |

## Configuring the `kind: integration` document (optional)

Everything above already works with **no** `kind: integration` document at all —
`AZURE_APPCONFIG_ENDPOINT` alone is enough. Declare one when you want the endpoint checked into
the solution itself instead of living only in an env var:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: azure-appconfig-prod
spec:
  type: azure-appconfig
  capabilities: [variables, features]
  enabled: true
  endpoints:
    address: https://my-config.azconfig.io
```

## Using it from an Environment

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  variables:
    - key: API_HOST
      store: azure-appconfig
      value: api-host # the configuration key's own name in the store
  features:
    - key: ENABLE_NEW_UI
      store: azure-appconfig
      value: enable-new-ui
```

`value` is the configuration key's own name inside the store — not a strata-declared key, and not
a literal value.

## Known limitation — feature flag values

Azure App Configuration stores a feature flag as a JSON blob
(`{"id": ..., "enabled": ..., ...}`), not a plain `true`/`false` string. The resolver currently
returns that blob verbatim rather than parsing out `enabled` — unaddressed so far because no
production evidence yet shows a feature (as opposed to a plain variable) actually resolving
through this store. If you hit this, that's why; it isn't specific to the configuration-binding
design this guide otherwise describes.

## Troubleshooting

- **"AZURE_APPCONFIG_ENDPOINT is not set"** — neither the env var nor a `kind: integration`
  document's `endpoints.address` resolved to an endpoint.
- **"no key named '...' in the store"** — the key doesn't exist in that store; check for a typo or
  the wrong endpoint.
- **"could not resolve '...': ..."** — usually an authentication/authorization failure; confirm
  the identity `DefaultAzureCredential` picked has read access on the store.
- **Multiple candidates named '...' — set 'integration:' explicitly"** (once the binding design is
  implemented) — more than one enabled `kind: integration` document declares `type: azure-appconfig`.
  Disable all but one — a store cannot name which one to use by itself.
