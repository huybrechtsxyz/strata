# Azure Key Vault (`store: azure-keyvault`)

Resolves `secret` values from an [Azure Key Vault](https://learn.microsoft.com/azure/key-vault/)
instance — via the real Azure SDK (`azure-keyvault-secrets`), not the `az` CLI.

## Prerequisites

- An Azure Key Vault with the secrets you want to use already populated.
- The identity strata runs as must have `Key Vault Secrets User` (or equivalent) access on that
  vault — see [Authentication](#authentication) below for how that identity is chosen.

## Authentication — ambient, via `DefaultAzureCredential`, never `spec.authentication`

`kind: integration`'s `spec.authentication` field is not used here, deliberately — see
[docs/work/store-integration-configuration.md](../work/store-integration-configuration.md#deliberately-out-of-scope)
for the full reasoning. In short: `DefaultAzureCredential`'s entire point is *not* needing an
explicit credential value — it chains through managed identity, workload identity/OIDC, then
`az login`, automatically, with nothing to declare in the common case (a managed identity assigned
to the resource strata runs on).

| Env var              | Required                                      | Purpose                                                                          |
| -------------------- | --------------------------------------------- | -------------------------------------------------------------------------------- |
| `AZURE_KEYVAULT_URL` | Yes                                           | The vault's URL, e.g. `https://my-vault.vault.azure.net`                         |
| `AZURE_CLIENT_ID`    | Only for a **user-assigned** managed identity | Picked up automatically by `DefaultAzureCredential` itself — not strata-specific |

If none of `DefaultAzureCredential`'s chain applies (e.g. running fully locally, outside Azure),
set whichever of its own supported env vars match your situation
(`AZURE_TENANT_ID`/`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET` for a service principal) — this is the
SDK's own mechanism, not something strata adds or needs to know about.

## Configuring the `kind: integration` document (optional)

Everything above already works with **no** `kind: integration` document at all — `AZURE_KEYVAULT_URL`
alone is enough. Declare one when you want the vault URL checked into the solution itself instead
of living only in an env var:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: azure-keyvault-prod
spec:
  type: azure-keyvault
  capabilities: [secrets]
  enabled: true
  endpoints:
    address: https://my-vault.vault.azure.net
```

## Using it from an Environment

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  secrets:
    - key: DB_PASSWORD
      store: azure-keyvault
      value: db-password # the secret's own name in the vault
```

`value` is the secret's own name inside the vault — not a strata-declared key, and not a literal
value.

## Troubleshooting

- **"AZURE_KEYVAULT_URL is not set"** — neither the env var nor a `kind: integration` document's
  `endpoints.address` resolved to a URL.
- **"no secret named '...' in '...'"** — the secret name doesn't exist in that vault; check for a
  typo or the wrong vault URL.
- **"could not resolve '...': ..."** — usually an authentication/authorization failure; confirm
  the identity `DefaultAzureCredential` picked has `Key Vault Secrets User` (or broader) access on
  the vault.
- **Multiple candidates named '...' — set 'integration:' explicitly"** (once the binding design is
  implemented) — more than one enabled `kind: integration` document declares `type: azure-keyvault`.
  Disable all but one — a store cannot name which one to use by itself.
