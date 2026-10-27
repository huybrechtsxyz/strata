# How To: Gate deploys on security findings

Two workspace policies let a deploy ask a security platform before it changes anything:

- `release_gate_policy` - the platform's pass/fail verdict on the repository at the commit being
  deployed (code, dependency, IaC and secret scanning done by the platform).
- `image_gate_policy` - the platform's open issues for every container image in the build's
  `sbom.json`, counted against a severity threshold.

Both run in `strata deploy run` after values resolve and before any step, and both can be run on
their own with `strata policy check`. They are vendor-neutral: an Integration document with the
`release_gate` or `image_findings` capability backs each one. Aikido Security (`type: aikido`) is
the first such integration; this guide uses it throughout.

## 1. Create the credentials in Aikido

Aikido uses two unrelated credentials, one per API:

| Credential                              | Used for         | Where to create it                                                  |
| --------------------------------------- | ---------------- | ------------------------------------------------------------------- |
| CI token (`AIK_CI_...`)                 | `release_gate`   | Settings - Integrations - Continuous Integration (shown once)       |
| API client (client id + secret, OAuth2) | `image_findings` | The integrations page, "API clients" (a workspace admin creates it) |

Store the secret values in your secret store (for example Azure Key Vault). strata never reads
them from YAML.

## 2. Declare them in an Environment

Any store works; the deployment must reach this environment.

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  secrets:
    - { key: AIKIDO_CI_TOKEN, store: azure-keyvault, value: aikido-ci-token }
    - { key: AIKIDO_CLIENT_SECRET, store: azure-keyvault, value: aikido-client-secret }
  variables:
    - { key: AIKIDO_CLIENT_ID, store: constant, value: "aik_client_xxxxxxxx" }
```

## 3. Add one Integration document per credential

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: aikido-ci
spec:
  type: aikido
  capabilities: [release_gate]
  authentication:
    method: api_key
    api_key:
      api_key: AIKIDO_CI_TOKEN        # a key from step 2, never a value
  configuration:
    region: eu                        # eu | us | me | au | us-gov
    release_gate:
      minimum_severity: high          # critical | high | medium | low
      fail_on: [dependency, iac, secrets]
---
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: aikido-api
spec:
  type: aikido
  capabilities: [image_findings]
  authentication:
    method: oauth2
    oauth2:
      client_id: AIKIDO_CLIENT_ID
      client_secret: AIKIDO_CLIENT_SECRET
  configuration:
    region: eu
    images:
      name_strategy: repository       # repository | full
      names:                          # optional: image path -> Aikido container name
        ghcr.io/acme/api: acme/api
```

Without an `authentication` block the integration reads `AIKIDO_CI_TOKEN` (or `AIKIDO_API_KEY`)
and `AIKIDO_CLIENT_ID` / `AIKIDO_CLIENT_SECRET` from the process environment instead. Unknown
`configuration` keys and mismatched authentication (`api_key` on an `image_findings` document,
`oauth2` on a `release_gate` one) fail validation.

## 4. Add the policies to the workspace

```yaml
kind: workspace
spec:
  release_gate_policy:
    integration: aikido-ci
    repository: acme/infra            # Aikido's repository id or name
    enforcement: deny                 # deny | warn
    on_missing_data: block            # skip | warn | block
    timeout: 900                      # seconds to wait for the verdict (30-7200)
    poll_interval: 10
  image_gate_policy:
    integration: aikido-api
    enforcement: deny
    on_missing_data: block
    max_severity: HIGH                # CRITICAL | HIGH | MEDIUM | LOW
    max_count: 0
    on_unknown_image: warn            # an image Aikido does not know: skip | warn | block
```

- `enforcement: warn` reports a breach and lets the deploy continue.
- `on_missing_data` decides what an outage, a timeout or a missing `sbom.json` does. `block` (the
  default) fails the gate: "could not confirm" is not "clean".
- An unknown repository is always an error, whatever `on_missing_data` says.
- The commit judged is, in order: `--commit`, the policy's `commit`, `STRATA_COMMIT_SHA`, the CI's
  own variable (`GITHUB_SHA`, `BUILD_SOURCEVERSION`, `CI_COMMIT_SHA`, ...), then `git rev-parse HEAD`.
- The solution's `cve_allowed` allowlist also suppresses image issues with a matching CVE id.

## 5. Check before deploying

```powershell
strata integration check aikido-ci --deployment prd-app      # 0 ok, 1 unreachable, 3 credential rejected
strata integration check aikido-api --deployment prd-app
strata build run prd-app                                     # writes sbom.json
strata policy list prd-app                                   # what is configured, last results
strata policy check prd-app                                  # every build and deploy policy, as build/deploy run them
strata policy findings prd-app --policy image_gate_policy    # per image, allowlisted issues marked
strata policy findings prd-app --policy cve_policy           # per CVE in sbom.json, allowlisted ones marked
strata policy findings prd-app --policy checkov_policy       # per provisioner/Helm module, failed checks
```

`strata policy check` exits 0 on pass (a `warn` breach included), 3 on a `deny` breach or missing
data under `block`, and 2 when there is nothing to check. Its results are appended to
`policy_results.json` in the build path, which the deploy audit record picks up.

`strata policy findings --integration aikido-api` works before `image_gate_policy` is configured,
so you can see what the gate would count before adopting it. `--severity` lowers or raises the
threshold for the listing only; for `cve_policy` it defaults to `severity_threshold`, else
`max_severity`. `policy findings --policy cve_policy` exits 2 without `sbom.json` and 1 when no
scanner is available or the scan fails.

## Notes

- `strata deploy run --smoke-test` only reports that the gates would run; `--dry-run` runs them.
- Credential values are added to the deploy's redaction set and never appear in diagnostics,
  `policy_results.json` or streamed output.
- `cve_policy` is checked by `strata policy check` against the build path's `sbom.json`, with the
  same scanner, allowlist and `on_missing_data` as `strata build run`, and its result replaces the
  build's entry in `policy_results.json`. `checkov_policy` is checked the same way against the
  rendered sources in the build path; `.terraform/` left by a deploy is not scanned.
- Open-source Checkov (no API key) reports no severity. `checkov_policy` counts such an unrated
  failed check at any `severity_gate`, so a `deny` policy fails on it; use `enforcement: warn`,
  `skip_checks` or `include_checks` to relax it. `strata policy findings --policy checkov_policy`
  marks them `[unrated]`.
