# Deploy-time security gates are two vendor-neutral integration capabilities

- Status: accepted
- Date: 2026-10-27
- Related: [docs/design/security-gates.md](../design/security-gates.md) (full design, D1-D11),
  [docs/archive/security-gates-implementation-plan.md](../archive/security-gates-implementation-plan.md) (phases and
  remaining work), [ADR-0021](0021-integration-layer.md) (integration layer and capabilities),
  [ADR-0022](0022-strata-build-run.md) D4 (build renders, deploy resolves), [ADR-0048](0048-integration-credentials-through-authentication-key-references.md),
  [ADR-0049](0049-strata-policy-command-group.md)

## Context and Problem Statement

Consumers want a deploy to stop when a security platform says the code or its container images are not fit to
ship. Aikido Security was evaluated first: it offers a release-gate verdict for a repository at a commit (CI API)
and the open issues per container image (REST API). strata already gates `build run` on locally installed scanners
(`cve_policy` with Trivy/Grype, `checkov_policy`), but nothing consults a remote platform, and nothing runs at
deploy time.

Every strata command is named after a function, with vendors behind it as integrations (`values`: Infisical, Key
Vault; `deploy`: Terraform, Helm, ArgoCD; `audit`: webhook, Sentinel). A vendor-named command or policy would be the
first exception, and renaming a CLI command later breaks every consumer pipeline. Platforms also differ in which
slices they cover: one gives a repository verdict but no image data, another the reverse.

## Considered Options

- **An Aikido-specific feature** (`strata aikido ...`, `aikido_policy`). Quickest; ties pipelines and documents to
  one vendor.
- **One broad "security platform" capability** with release gate, image findings, upload and suppression methods.
  Every implementation would stub the half it does not support.
- **Two narrow capabilities** - `release_gate` ("does repository X at commit Y pass?") and `image_findings`
  ("which open issues does image Z have?") - each with its own ABC and vendor-neutral result types, consumed by
  two workspace policies (`release_gate_policy`, `image_gate_policy`) that name their Integration document, and
  evaluated at deploy preflight by one vendor-neutral controller.

## Decision Outcome

Chosen: **two narrow capabilities**, because a policy then asks for a capability, never a product: Aikido
implements both, a later vendor implements only what it covers, and no command, policy field or diagnostic code
carries a vendor name. The gates run after `deploy run` resolves its values and before the first step (every
deploy entry point; `--smoke-test` only reports them), never in `build run`, which makes no network calls and holds
no secrets. Both behave like the existing build-time policies: `enforcement: deny | warn`,
`on_missing_data: skip | warn | block`, results in `policy_results.json` for the audit manifest. A vendor's own
thresholds and naming live on its Integration document's `spec.configuration`, validated by the vendor class, so
the policies stay generic. Aikido is the first implementation of both.

### Consequences

- Good: pipelines and documents never name a vendor; swapping or adding a platform is a new integration class.
- Good: the gates reuse what deploy already has (resolved values, redaction, policy results, audit), not a new path.
- Good: an unknown repository is always a hard error, while an outage follows `on_missing_data` - "could not
  confirm" is never silently "clean" under the default `block`.
- Bad: deploy gains a network dependency before any step when a gate is configured; a slow platform slows every
  deploy (bounded by the release gate's `timeout`).
- Bad: Aikido's REST response shapes are partly unconfirmed until tested against a real workspace; the vendor class
  parses them tolerantly.
