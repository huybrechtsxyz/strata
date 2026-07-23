# Infisical Integration

Infisical is an open-source secrets manager (MPL-2.0) that supports both the Infisical
Cloud service and self-hosted deployments. strata resolves secrets from Infisical at
build time via the REST API.

Installation (Infisical CLI — optional, for local testing)
- macOS: `brew install infisical/get-cli/infisical`
- Linux: `curl -1sLf 'https://dl.cloudsmith.io/public/infisical/infisical-cli/setup.rpm.sh' | bash && yum install infisical`
- Docs: https://infisical.com/docs/cli/overview

Infisical Cloud (no install required)
Sign up at https://infisical.com — API access is available immediately.

Self-hosted
- Docker: https://infisical.com/docs/self-hosting/deployment-options/docker-compose
- Set `endpoints.address` to your self-hosted instance URL.

Configuration YAML

```yaml
integrations:
  - name: infisical
    type: infisical
    capabilities: [secrets, variables]
    endpoints:
      address: https://app.infisical.com   # or self-hosted URL
    authentication:
      method: api_key
      api_key:
        api_key: INFISICAL_TOKEN           # env var name holding the service token
```

Authentication methods

**Option 1 — Service token (simpler)**
```yaml
authentication:
  method: api_key
  api_key:
    api_key: INFISICAL_TOKEN
```
Set env var: `INFISICAL_TOKEN=st.xxx...`

**Option 2 — Universal auth / Machine identity (recommended for CI/CD)**
```yaml
authentication:
  method: oauth2
  oauth2:
    client_id: INFISICAL_CLIENT_ID
    client_secret: INFISICAL_CLIENT_SECRET
```
Set env vars: `INFISICAL_CLIENT_ID=...` and `INFISICAL_CLIENT_SECRET=...`

Environment variables

| Variable                  | Purpose                                   | Required       |
| ------------------------- | ----------------------------------------- | -------------- |
| `INFISICAL_TOKEN`         | Service token (Option 1)                  | Yes (Option 1) |
| `INFISICAL_CLIENT_ID`     | Machine identity client ID (Option 2)     | Yes (Option 2) |
| `INFISICAL_CLIENT_SECRET` | Machine identity client secret (Option 2) | Yes (Option 2) |
| `INFISICAL_PROJECT_ID`    | Target project ID                         | Recommended    |
| `INFISICAL_ENVIRONMENT`   | Target environment slug (default: `prod`) | No             |

Secret reference in deployment YAML
```yaml
secrets:
  - key: DATABASE_PASSWORD
    source: infisical
    value: my-db-password-secret-name
```

Common checks (Infisical CLI)
```
infisical login
infisical secrets --env=prod
infisical run -- env | grep MY_SECRET
```

Docs
- https://infisical.com/docs
