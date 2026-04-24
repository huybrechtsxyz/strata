# HashiCorp Consul Integration

Installation
- Download from https://www.consul.io/downloads or use package managers:
  - macOS: `brew install consul`
  - Linux: download release and add to `PATH`

Configuration
- Set `CONSUL_HTTP_ADDR` to your Consul HTTP API endpoint (e.g., `http://127.0.0.1:8500`).
- If ACLs are enabled, set `CONSUL_HTTP_TOKEN`.
- For enterprise namespaces set `CONSUL_NAMESPACE`.

Common Commands
- `consul version`
- KV get: `consul kv get <key>`
- KV put: `consul kv put <key> <value>`

Troubleshooting
- Verify `consul` is reachable at `CONSUL_HTTP_ADDR`.
- Check firewall/network rules.

Docs
- https://www.consul.io/docs
