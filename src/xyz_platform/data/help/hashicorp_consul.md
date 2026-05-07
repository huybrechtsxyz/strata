# HashiCorp Consul Integration

Installation
- Download from https://www.consul.io/downloads or use package managers:
  - macOS: `brew install consul`
  - Linux: download release and add to `PATH`

Configuration
- Set `CONSUL_HTTP_ADDR` to your Consul HTTP API endpoint (e.g., `http://127.0.0.1:8500`).
- If ACLs are enabled, set `CONSUL_HTTP_TOKEN`.
- For enterprise namespaces set `CONSUL_NAMESPACE`.

Connection parameters

- Required:
  - `CONSUL_HTTP_ADDR` — Consul HTTP API endpoint (default can be provided via `endpoints.address` in integration YAML).

- Optional (when ACLs / enterprise features are used):
  - `CONSUL_HTTP_TOKEN` — ACL token for requests (integration also reads `authentication.api_key.api_key` if provided in YAML).
  - `CONSUL_NAMESPACE` — enterprise namespace if applicable

Example — integration YAML override for token

```yaml
integration:
  name: consul
  type: hashicorp-consul
  endpoints:
    address: ${CONSUL_HTTP_ADDR}
  authentication:
    method: api_key
    api_key:
      api_key: "CONSUL_HTTP_TOKEN"
```

How xyz-platform connects
- The integration prefers CLI (consul) when available, otherwise it will use direct HTTP API calls to `CONSUL_HTTP_ADDR`. If you specify a custom env-var name for the token in the YAML, the integration will read that env var.

Common Commands
- `consul version`
- KV get: `consul kv get <key>`
- KV put: `consul kv put <key> <value>`

Troubleshooting
- Verify `consul` is reachable at `CONSUL_HTTP_ADDR`.
- Check firewall/network rules.

Docs
- https://www.consul.io/docs
