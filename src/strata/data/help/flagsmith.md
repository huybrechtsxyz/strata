# Flagsmith Integration

Flagsmith is an open-source feature flag platform (BSD-3-Clause) that supports both the
Flagsmith SaaS service and self-hosted deployments. strata uses the Flagsmith REST API
for feature flag and variable resolution — **no CLI binary required**.

Flagsmith SaaS (no install)
Sign up at https://flagsmith.com or use the hosted Edge API directly.

Self-hosted installation
- Docker: `docker run flagsmith/flagsmith`
- Docker Compose / Helm: https://docs.flagsmith.com/deployment/hosting/self-hosted
- The self-hosted instance exposes the same REST API as the SaaS Edge API

Configuration YAML

```yaml
integrations:
  - name: flagsmith
    type: flagsmith
    capabilities: [features, variables]
    endpoints:
      address: https://edge.api.flagsmith.com   # or your self-hosted address
    authentication:
      method: api_key
      api_key:
        api_key: FLAGSMITH_ENV_KEY              # env var name holding the key
```

Authentication

| Key type            | Purpose                      | Where to find                     |
| ------------------- | ---------------------------- | --------------------------------- |
| Environment API key | Read features and identities | Flagsmith UI → Environment → Keys |
| Management API key  | Create/toggle flags via API  | Flagsmith UI → Account → API Keys |

Environment variables

| Variable                   | Purpose                            | Required |
| -------------------------- | ---------------------------------- | -------- |
| `FLAGSMITH_ENV_KEY`        | Environment API key (default name) | Yes      |
| `FLAGSMITH_MANAGEMENT_KEY` | Management API key for write ops   | No       |

Override the env var names in YAML:
```yaml
authentication:
  method: api_key
  api_key:
    api_key: MY_FLAGSMITH_KEY    # reads from env var MY_FLAGSMITH_KEY
```

For management operations (creating/toggling flags), set:
```yaml
authentication:
  method: oauth2
  oauth2:
    client_secret: MY_MANAGEMENT_KEY    # reads from env var MY_MANAGEMENT_KEY
```

Test connectivity
```
curl -H "X-Environment-Key: $FLAGSMITH_ENV_KEY" \
     https://edge.api.flagsmith.com/api/v1/flags/
```

Docs
- https://docs.flagsmith.com
- API reference: https://api.flagsmith.com/api/v1/docs/
