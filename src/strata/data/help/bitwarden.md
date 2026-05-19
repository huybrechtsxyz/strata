# Bitwarden Secrets Manager Integration

Official Installation
- Windows: download the appropriate `bws` release from the Bitwarden SDK releases and add to `PATH`.
- macOS / Linux: download the matching binary for your platform and add to `PATH`.

Manual Install
- Download binary from https://github.com/bitwarden/sdk-sm/releases
- Extract the `bws` executable and ensure it's on `PATH`

Configuration
- Ensure `bws` is in your `PATH` and verify with `bws --version`.
- Set access token in environment: `BWS_ACCESS_TOKEN="your-access-token"` (machine account token)

Connection parameters

- Required:
	- `BWS_ACCESS_TOKEN` — Bitwarden Secrets Manager access token (default env var name). The integration also supports overriding the env-var name in the integration YAML (see example).

- How strata uses it:
	- The integration looks for an API key/env-var name in `authentication.api_key.api_key` in the integration YAML. If omitted, it falls back to `BWS_ACCESS_TOKEN`.

Example — integration YAML override

```yaml
integration:
	name: bitwarden
	type: bitwarden
	authentication:
		method: api_key
		api_key:
			api_key: "MY_BWS_TOKEN_ENV"
```

Notes
- Ensure `bws` CLI is installed and the access token has necessary permissions to list/get secrets.

Usage
- Get secret: `bws secret get <secret-id>`
- List secrets: `bws secret list`
- Create secret: `bws secret create`

Docs
- Official docs: https://bitwarden.com/help/secrets-manager-cli/
- SDK repo: https://github.com/bitwarden/sdk-sm
