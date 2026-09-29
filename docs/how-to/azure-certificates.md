# How To: Get a TLS Certificate onto an Azure Application Gateway / WAF

You are deploying an Azure Application Gateway v2 (WAF_v2) with Bicep/Terraform and its HTTPS
listener needs a certificate. This guide covers what the IaC actually automates, what stays a
manual (or separately-automated) step, and how it maps onto strata's own model — see the
worked example in [`config/`](../../config/README.md): `resources/networking.yaml`
(`key-vault`, `app-gateway-waf`), `integrations/azure-keyvault.yaml`, and the
`APPGW_CERT_SECRET_ID` secret in `environments/prd.yaml`.

## The short answer

**Terraform/Bicep automate the plumbing, not the certificate itself:**

| Automated by Terraform/Bicep                                                                        | NOT automated by Terraform/Bicep        |
| --------------------------------------------------------------------------------------------------- | --------------------------------------- |
| Creating the Key Vault                                                                              | Issuing/renewing the certificate        |
| The user-assigned managed identity                                                                  | Uploading the certificate's private key |
| The RBAC role assignment (`Key Vault Secrets User`) granting App Gateway read access                | Rotating the certificate before expiry  |
| The App Gateway's `ssl_certificate`/`sslCertificates` block referencing the Key Vault secret by URI |                                         |

So yes — **populating the certificate is a separate action from the `terraform apply`/`bicep
deploy` that creates the Key Vault and wires up access.** It is either:

1. **A one-time manual action** (`az keyvault certificate import`, or the portal) — fine for an
   internal cert, a short-lived dev cert, or a cert from a CA with no Key Vault integration.
2. **A separately-automated, ongoing process** — a Key Vault-integrated CA issuer (DigiCert/
   GlobalSign), or a small standalone component like
   [Key Vault Acmebot](https://github.com/shibayan/keyvault-acmebot) for Let's Encrypt — which
   renews the secret in Key Vault on its own schedule, entirely outside the App Gateway's own
   IaC lifecycle.

Either way, the App Gateway's Terraform/Bicep never sees the certificate bytes: it only ever
reads a `keyVaultSecretId`/`key_vault_secret_id` that already exists.

## What the IaC creates

**Bicep:**

```text
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
  }
}

resource appgwIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-appgw'
  location: location
}

resource kvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, appgwIdentity.id, 'KeyVaultSecretsUser')
  scope: kv
  properties: {
    principalId: appgwIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User
    )
  }
}

resource appgw 'Microsoft.Network/applicationGateways@2023-11-01' = {
  name: 'appgw-example'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appgwIdentity.id}': {} }
  }
  properties: {
    sslCertificates: [
      {
        name: 'primary-cert'
        properties: {
          // The secret must already exist — this apply does not create it.
          keyVaultSecretId: 'https://${kvName}${environment().suffixes.keyvaultDns}/secrets/appgw-cert'
        }
      }
    ]
  }
}
```

**Terraform:**

```hcl
resource "azurerm_key_vault" "this" {
  name                       = var.kv_name
  location                   = var.location
  resource_group_name        = var.rg_name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true
}

resource "azurerm_user_assigned_identity" "appgw" {
  name                = "id-appgw"
  location            = var.location
  resource_group_name = var.rg_name
}

resource "azurerm_role_assignment" "appgw_kv_reader" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.appgw.principal_id
}

# Data source, not a managed resource — the secret is assumed to already
# exist. `terraform apply` never creates or rotates it.
data "azurerm_key_vault_secret" "appgw_cert" {
  name         = "appgw-cert"
  key_vault_id = azurerm_key_vault.this.id
}

resource "azurerm_application_gateway" "this" {
  # ...
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.appgw.id]
  }

  ssl_certificate {
    name                = "primary-cert"
    key_vault_secret_id = data.azurerm_key_vault_secret.appgw_cert.id
  }
}
```

Using a `data` source (not `azurerm_key_vault_certificate`) is deliberate: it keeps certificate
issuance out of the App Gateway's own `apply` cycle, and keeps the private key out of Terraform
state.

## Getting the certificate into Key Vault

### Option A — manual, one-time import

```powershell
az keyvault certificate import `
  --vault-name kv-example `
  --name appgw-cert `
  --file ./appgw-cert.pfx `
  --password <pfx-password>
```

Use this for an internal/private-CA cert, a dev/self-signed cert, or any CA that has no Key
Vault integration. You (or whoever owns the cert) are responsible for re-running this before
expiry — nothing in the IaC reminds you.

### Option B — Key Vault-integrated CA issuer (semi-automated)

If you have a DigiCert/GlobalSign contract, configure it once as a Key Vault "issuer", then let
Key Vault manage the CSR, submission, and renewal itself:

```powershell
az keyvault certificate issuer create --vault-name kv-example --issuer-name digicert --provider DigiCert
az keyvault certificate create --vault-name kv-example --name appgw-cert `
  --policy "$(az keyvault certificate get-default-policy --output json | jq '.issuerParameters.name = "digicert"')"
```

Key Vault auto-renews before expiry — no manual re-import needed once configured.

### Option C — Let's Encrypt via Key Vault Acmebot (fully automated, free CA)

Deploy [Key Vault Acmebot](https://github.com/shibayan/keyvault-acmebot) (an Azure Functions app,
itself a small separate Bicep/Terraform deployment) once. It performs the ACME DNS-01/HTTP-01
challenge and writes/renews the certificate into Key Vault on a timer trigger — completely
decoupled from the App Gateway's own IaC pipeline.

## Mapping this onto strata

The `config/` example encodes exactly this split:

- `resources/networking.yaml`'s `key-vault` resource — the Key Vault Terraform creates.
- `resources/networking.yaml`'s `app-gateway-waf` resource — declares a capability
  `dependencies[]` entry on `key_vault`, and its `configuration.key_vault_secret_id` is
  `${secret:APPGW_CERT_SECRET_ID}` — never a literal PFX/password.
- `environments/prd.yaml`'s `APPGW_CERT_SECRET_ID` secret (`store: azure-keyvault`) — names the
  *existing* secret; its own `description` field is where you record which of Options A/B/C
  above populates it, since strata itself never issues or rotates the certificate.
- `integrations/azure-keyvault.yaml` — the integration that resolves that token at deploy time.

`strata build run`/`deploy run` only ever read the secret's identifier — issuing or renewing the
certificate stays entirely outside strata's own responsibility, same as it stays outside
Terraform/Bicep's.
