# OpenTofu as a first-class provisioner tool

- Status: accepted
- Date: 2026-11-12
- Related: [docs/design/opentofu.md](../design/opentofu.md) (the design),
  [ADR-0011](0011-topology-and-provisioning-decoupling.md) (`tool` is an open string; OpenTofu the one
  terraform-compatible leniency), [ADR-0021](0021-integration-layer.md) (the integration registry),
  [ADR-0051](0051-local-tools-as-declarable-integration-types.md) (each tool its own type, several classes in one
  module), [docs/design/cli-image.md](../design/cli-image.md) (the container image)

## Context and Problem Statement

strata runs root modules with Terraform. Terraform 1.6 and later is under the Business Source License 1.1, which
allows internal use but leaves it unclear whether a public image that bundles Terraform with a deployment
orchestrator is a "competitive offering". So strata's published `-full` image carries no Terraform, and a team using
it had no IaC tool at all.

OpenTofu is Terraform's MPL 2.0 fork with the same CLI contract. strata already reserved `tool: opentofu` (the model,
the backend validator, the lock policy and the Checkov gate named it), but no integration was registered, so a
`tool: opentofu` step failed at build. It could be made to run by accident: bound to a `type: terraform` integration
with `command: tofu`, the step ran `tofu` through the Terraform class, with Terraform's version numbering, init
advice and provider registry. A short provider source is where the two tools really differ: `tofu init` keys
`hashicorp/null` as `registry.opentofu.org/hashicorp/null` in `.terraform.lock.hcl`, and strata assumed
`registry.terraform.io`, so its lock policy called a correctly locked OpenTofu root unlocked and its SBOM listed
each provider twice.

## Considered Options

- **A. One class whose binary follows the document** (`type: terraform`, `command: tofu`). No new type, but the
  registry, the constructor's type check and the readiness check all key on one type per class, and nothing would
  tell strata which tool a step runs.
- **B. Its own type and a subclass**, `OpenTofuIntegration(TerraformIntegration)` with `TYPE = "opentofu"` and
  `COMMAND = "tofu"`, as v1 did. The provisioner's `tool` names the tool; everything that differs keys on it.
- **C. Wait for a consumer.** Keeps the `-full` image without an IaC tool and leaves the accidental path open.

## Decision Outcome

Chosen: **B.**

- `tool: opentofu` runs `OpenTofuIntegration`, which inherits Terraform's argv, `TF_VAR_` delivery, saved plans,
  destroy and the generated `strata` files unchanged. Every flag strata passes predates the fork, so OpenTofu 1.6.0
  and later run it.
- **No `tofu` behind `tool: terraform`.** A provisioner bound to an integration of the other Terraform-family type,
  and a `terraform` integration whose `spec.command` is `tofu` (or the reverse), are `strata validate` errors.
  `spec.command` stays for a renamed or wrapped binary of the same tool.
- **The lock file decides the registry host.** A short provider source resolves to the default host the root's
  lock file holds (Terraform's or OpenTofu's), and to the tool's own default without one. The SBOM keeps
  `repository_url=registry.opentofu.org` on an OpenTofu provider's purl: it says where the provider came from.
- **The `-full` image ships OpenTofu and not Terraform**, for the licence reason above.
- Out of scope: reading `*.tofu` files (strata reads `.tf` roots; `tofu` still runs them) and delivering OpenTofu
  state-encryption keys (HCL-configured encryption works as is).

### Consequences

- Good: a pipeline on the `-full` image can run IaC; OpenTofu roots get correct lock-policy and SBOM results; the
  accidental binding becomes an explicit error with the fix in its message.
- Bad: a solution that ran `tofu` through `type: terraform` stops validating until it switches to `tool: opentofu`
  and `type: opentofu`. That path was undocumented and gave wrong lock and SBOM results.
- Neutral: the purl type stays `pkg:terraform/...` for OpenTofu providers; there is no separate purl type.
