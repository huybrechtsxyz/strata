# Integration credentials are AuthenticationModel key references, one document per credential

- Status: accepted
- Date: 2026-10-27
- Related: [ADR-0047](0047-deploy-security-gates-as-vendor-neutral-capabilities.md) (the first consumer),
  [docs/design/security-gates.md](../design/security-gates.md) D2,
  [ADR-0021](0021-integration-layer.md) (integration layer; `integrations` never imports `controllers`)

## Context and Problem Statement

The security-gate integrations (ADR-0047) need secrets: Aikido uses a CI token for its release gate and separate
OAuth client credentials for its image findings. strata already resolves secrets for git remotes and Helm/OCI
registries through the Integration document's `spec.authentication`: its fields name **keys**, declared in an
Environment and resolved by the value pipeline from any store (Key Vault, Infisical, App Config, GitHub,
environment). `AuthenticationModel` allows exactly one method per document, but Aikido needs two unrelated
credentials. The integration layer must also stay below the controllers, so an integration cannot resolve values
itself.

## Considered Options

- **A new credential block** on the Integration document (or on `configuration.credentials`) holding both secrets
  for one vendor. A second credential mechanism beside `authentication`, with its own validation and redaction.
- **Allow several authentication methods per document.** Changes a model rule every existing consumer relies on.
- **Reuse `spec.authentication` unchanged and declare one Integration document per credential**: each declares
  only the capability its credential serves; the controller resolves `authentication.key_references()` through the
  value pipeline and hands the values to the integration (`CredentialedIntegration.bind_credentials()`). Without an
  `authentication` block the integration reads its own environment variables.

## Decision Outcome

Chosen: **one document per credential, key references resolved by the controller**, because it adds no credential
mechanism: secrets stay in the stores strata already supports, the redaction set already covers resolved values,
and the layering holds (the integration receives values, never resolves them). The capability split of ADR-0047
makes two documents of one type natural: `aikido-ci` (`api_key`, `release_gate`) and `aikido-api` (`oauth2`,
`image_findings`). A declared key that does not resolve is a hard error naming the key, never the value.

### Consequences

- Good: one credential story for remotes, registries and gates; reviewers check one model.
- Good: the vendor class rejects an authentication method or capability combination it cannot serve, so a
  mismatched document fails at construction instead of at first use.
- Bad: a vendor with two credentials needs two Integration documents and repeats shared settings (region,
  timeouts) on both.
- Bad: `AuthenticationModel.key_references()` must list each method's key fields; methods without a consumer
  return nothing until one appears.
