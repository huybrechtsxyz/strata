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

Environment Variables
- `BWS_ACCESS_TOKEN`: Bitwarden Secrets Manager access token (required)

Usage
- Get secret: `bws secret get <secret-id>`
- List secrets: `bws secret list`
- Create secret: `bws secret create`

Docs
- Official docs: https://bitwarden.com/help/secrets-manager-cli/
- SDK repo: https://github.com/bitwarden/sdk-sm
