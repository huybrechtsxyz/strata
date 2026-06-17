# Setting Up Azure OIDC for GitHub Actions

OIDC (OpenID Connect) lets GitHub Actions authenticate to Azure without storing a client secret. Azure issues a short-lived token scoped to each individual run, meaning there is nothing to rotate, nothing to leak, and every authentication event is visible in Azure Entra ID sign-in logs.

---

## Prerequisites

- An Azure subscription
- Owner, or Contributor + Application Administrator role on the Entra ID tenant
- A GitHub repository that calls the `deploy-workspace.yml` reusable workflow (or the `azure-login` composite action directly)

---

## Step 1: Create (or identify) an App Registration

1. Go to **Azure Portal → Microsoft Entra ID → App registrations → New registration**.
2. Give it a descriptive name, e.g. `github-actions-<repo-name>`. Leave all other defaults.
3. Click **Register**.

If a service principal already exists for this repository, use it instead of creating a new one.

Note the **Application (client) ID** and the **Directory (tenant) ID** from the Overview page — you will need both in Step 4.

---

## Step 2: Add a Federated Credential

1. Open the App Registration → **Certificates & secrets → Federated credentials → Add credential**.
2. Select the **GitHub Actions deploying Azure resources** scenario.
3. Fill in the fields:

   | Field                   | Value                                                                                 |
   | ----------------------- | ------------------------------------------------------------------------------------- |
   | Organization            | Your GitHub org or username                                                           |
   | Repository              | The repo name (without the org prefix)                                                |
   | Entity type             | **Environment** (recommended — locks the credential to a specific GitHub environment) |
   | GitHub environment name | e.g. `production`                                                                     |
   | Name                    | e.g. `github-actions-production`                                                      |

4. Click **Add**.

Azure generates a subject claim in the format:

```
repo:<org>/<repo>:environment:<environment-name>
```

This claim must match exactly what GitHub sends during the OIDC token exchange. If you use `Entity type: Branch` or `Pull request` instead, the subject claim format changes accordingly — make sure the credential type matches how your workflow is configured.

> **Tip:** You can add multiple federated credentials to a single App Registration — one per environment (e.g. `production`, `staging`, `dev`). Each credential is independent and maps to a different GitHub environment.

---

## Step 3: Grant Azure Role Assignments

The App Registration needs at minimum the following role assignments, depending on which Azure stores your deployment reads from:

| Azure Resource    | Role                            |
| ----------------- | ------------------------------- |
| Key Vault         | `Key Vault Secrets User`        |
| App Configuration | `App Configuration Data Reader` |

To assign a role:

1. Navigate to the target resource in the Azure Portal.
2. Open **IAM → Add role assignment**.
3. Select the required role.
4. On the **Members** tab, choose **User, group, or service principal** and search for the App Registration by name.
5. Click **Review + assign**.

Repeat for each Key Vault and App Configuration store that your deployment accesses.

---

## Step 4: Configure GitHub Variables

In your GitHub repository, go to **Settings → Secrets and variables → Actions → Variables**.

Add the following **repository variables** (not secrets — these values are not sensitive):

| Variable                | Value                               |
| ----------------------- | ----------------------------------- |
| `AZURE_TENANT_ID`       | Directory (tenant) ID from Step 1   |
| `AZURE_SUBSCRIPTION_ID` | Your Azure subscription ID          |
| `AZURE_CLIENT_ID`       | Application (client) ID from Step 1 |

> **Important:** Do **not** add `AZURE_CLIENT_SECRET`. If one already exists from a previous client-secret setup, you can leave it in place — as long as you do not pass it to the `azure-login` action, OIDC mode is used automatically.

If you want the variables scoped to a specific environment instead of the whole repository, add them under **Settings → Environments → \<environment name\> → Environment variables**.

---

## Step 5: Update the Calling Workflow

Pass the three variables to the reusable workflow. The absence of `azure_client_secret` is what triggers OIDC mode:

```yaml
jobs:
  deploy:
    uses: huybrechtsxyz/strata/.github/workflows/deploy-workspace.yml@v0
    with:
      deployment_file: deploy/deploy-prd.yaml
      azure_tenant_id: ${{ vars.AZURE_TENANT_ID }}
      azure_subscription_id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
      azure_client_id: ${{ vars.AZURE_CLIENT_ID }}
      # azure_client_secret is NOT passed — this triggers OIDC mode
    secrets: inherit
```

The reusable workflow already declares `permissions: id-token: write` internally. If you are calling the `azure-login` action directly (not via the reusable workflow), add the permission to your job:

```yaml
    permissions:
      contents: read
      id-token: write
```

---

## Verifying the Setup

Trigger a run and open the **Azure Login** step in the GitHub Actions log. It should report `Login successful` without any secret value being printed.

To confirm in Azure: go to **Microsoft Entra ID → Sign-in logs → Service principal sign-ins** and filter by the App Registration name. You should see the GitHub Actions run listed with authentication method **Federated identity credential**.

---

## Switching Back to Client Secret

To revert to client-secret mode, pass `azure_client_secret: ${{ secrets.AZURE_CLIENT_SECRET }}` in the calling workflow. The `azure-login` action detects the input and switches to client-secret mode automatically — no other changes are required.

---

## Troubleshooting

| Symptom                                                                 | Likely cause                         | Fix                                                                                                                                              |
| ----------------------------------------------------------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `AADSTS70021: No matching federated identity record found`              | Subject claim mismatch               | Verify the entity type (Environment) and environment name in the federated credential exactly match the GitHub environment name in your workflow |
| `Resource not accessible by integration`                                | Missing `id-token: write` permission | Ensure the calling job has `permissions: id-token: write`                                                                                        |
| Login succeeds but Key Vault returns 403                                | Missing role assignment              | Add the `Key Vault Secrets User` IAM role for the App Registration on the vault                                                                  |
| Login succeeds but App Configuration returns 403                        | Missing role assignment              | Add the `App Configuration Data Reader` IAM role for the App Registration                                                                        |
| Credential works for `main` branch but not for `production` environment | Wrong entity type                    | The federated credential entity type must match how your workflow is triggered; use **Environment** if the job targets a GitHub environment      |
