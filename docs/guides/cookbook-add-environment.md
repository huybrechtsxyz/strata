# Cookbook: Add a New Environment

> **Audience:** Operator who needs to add a staging, dev, or DR environment alongside an existing production deployment.

---

## When to use this

You already have a working deployment (e.g., `deploy-prd.yaml` pointing to `env-prd.yaml`). You want to create a second environment — same infrastructure blueprint, different settings.

---

## Step-by-step

### 1. Copy the existing environment file

```bash
cp envs/env-prd.yaml envs/env-stg.yaml
```

### 2. Edit the new environment

Change the name, description, and any environment-specific values:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: haven_env_stg                          # ← unique name
  annotations:
    description: Staging environment for Haven
  labels:
    version: "1.0.0"
  tags: [haven, staging]
spec:
  variables:
    - key: WORKSPACE
      store: constant
      value: haven
    - key: DATACENTER
      store: constant
      value: kamatera-eu-fr
  secrets:
    # Use DIFFERENT secret references for staging
    - key: TERRAFORM_API_TOKEN
      store: bitwarden
      value: <your-staging-bitwarden-id>
    - key: KAMATERA_API_KEY
      store: bitwarden
      value: <your-staging-bitwarden-id>
    # ... (all secrets for this environment)
```

**Key rules:**
- `meta.name` must be unique across all environments.
- Secret IDs should point to staging-specific credentials — never share production secrets.
- Variables that differ per environment (e.g., `DATACENTER`) get new values; shared ones stay the same.

### 3. Create a deployment file for the new environment

```bash
cp deploy/deploy-prd.yaml deploy/deploy-stg.yaml
```

Edit it to reference the new environment:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: haven_deploy_stg                       # ← unique name
  annotations:
    description: Staging deployment for Haven
  labels:
    version: "1.0.0"
  tags: [haven, staging, deployment]
spec:
  layers:
    environment: stg

  properties:
    environment: stg

  workspace:
    name: haven_platform
    description: Haven Platform Workspace
    file: "@haven/stack/ws-platform.yaml"      # same workspace blueprint

  environments:
    - "@haven/envs/env-stg.yaml"               # ← points to staging env

  stages:
    - name: infrastructure
      type: infrastructure
      scope: all
      depends_on: null
      on_failure: stop
```

**The workspace stays the same** — that's the point. The workspace defines WHAT to build; the environment defines HOW to customize it.

### 4. Validate

```bash
strata validate --file deploy/deploy-stg.yaml
```

Fix any errors (usually: missing secret references, name collisions, or typos in `@repo/path`).

### 5. Build and review

```bash
strata build plan --file deploy/deploy-stg.yaml
```

This shows what Terraform artifacts would be generated without writing anything. Review the output to confirm your staging environment looks correct.

### 6. Build for real

```bash
strata build run --file deploy/deploy-stg.yaml
```

### 7. Deploy (when ready)

```bash
strata deploy run --file deploy/deploy-stg.yaml
```

---

## Checklist

- [ ] New environment file with unique `meta.name`
- [ ] Staging-specific secrets (never reuse production credentials)
- [ ] New deployment file referencing the new environment
- [ ] `strata validate` passes
- [ ] `strata build plan` output reviewed
- [ ] Committed to version control before deploying

---

## Common mistakes

| Mistake | Symptom | Fix |
|---------|---------|-----|
| Same `meta.name` as production | Validation error: duplicate name | Use a unique name like `haven_env_stg` |
| Forgot to update secret references | Build fails: cannot resolve secret | Create staging secrets in your vault, update IDs |
| Wrong `@repo/path` in deployment | File not found error | Check repository alias in your solution config |
| Deployed before validating | Terraform errors with incomplete config | Always run `strata validate` first |
