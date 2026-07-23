# OpenBao Integration

OpenBao is the Linux Foundation fork of HashiCorp Vault, released under MPL-2.0.
It maintains full API and authentication compatibility with Vault. Use `type: openbao`
to target the `bao` binary instead of `vault`.

Installation
- Linux (package): https://openbao.org/docs/install/
- macOS (Homebrew): `brew install openbao` (or download binary from GitHub releases)
- Docker: `docker run openbao/openbao`
- Docs: https://openbao.org/docs/install/

Verify install
```
bao --version
```

Configuration YAML

```yaml
integrations:
  - name: openbao
    type: openbao
    capabilities: [secrets, variables, keyvalue]
    endpoints:
      address: https://bao.example.com
    authentication:
      method: api_key
      api_key:
        api_key: VAULT_TOKEN    # env var name — VAULT_TOKEN works for OpenBao too
```

Authentication methods

| Method         | Description                                                                   |
| -------------- | ----------------------------------------------------------------------------- |
| **Token**      | Set `VAULT_TOKEN`. Most common for automation.                                |
| **AppRole**    | `bao write auth/approle/login role_id=... secret_id=...` → set returned token |
| **Kubernetes** | Mount service account JWT and configure `bao auth enable kubernetes`          |

Environment variables

| Variable          | Purpose                                                           | Required         |
| ----------------- | ----------------------------------------------------------------- | ---------------- |
| `VAULT_ADDR`      | OpenBao server address (overridden by `endpoints.address` if set) | Yes              |
| `VAULT_TOKEN`     | Authentication token                                              | Yes (token auth) |
| `VAULT_ROLE_ID`   | AppRole role ID                                                   | Yes (AppRole)    |
| `VAULT_SECRET_ID` | AppRole secret ID                                                 | Yes (AppRole)    |
| `VAULT_NAMESPACE` | Namespace (OpenBao Enterprise)                                    | No               |

Secret reference in deployment YAML
```yaml
secrets:
  - key: MY_SECRET
    source: openbao
    value: secret/myapp/key
```

Common checks
```
bao status
bao kv get secret/myapp
bao kv put secret/myapp key=value
bao auth list
```

OpenBao vs HashiCorp Vault
OpenBao and HashiCorp Vault share the same CLI interface, API, and authentication
methods. The only difference is the binary name (`bao` vs `vault`) and the project
governance. Configuration YAML and environment variables are identical.

Docs
- https://openbao.org/docs
