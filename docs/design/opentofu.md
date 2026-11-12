# OpenTofu - Design

- Status: implemented
- Last updated: 2026-11-12

## Overview

`tool: opentofu` runs a Terraform-style root module with OpenTofu's `tofu` binary. OpenTofu is Terraform's MPL 2.0
fork with the same CLI contract, so strata runs it through `OpenTofuIntegration`, a subclass of
`TerraformIntegration` that changes the type and the binary only ([ADR-0060](../decisions/0060-opentofu-as-a-first-class-provisioner-tool.md)).
The configuration reference, with the migration steps, is the "OpenTofu" section of
[workspace.md](../config/workspace.md#opentofu).

It matters for the container image: Terraform's BSL licence keeps Terraform out of the published `-full` image, and
OpenTofu is the IaC tool that image carries ([cli-image.md](cli-image.md)).

## Current Design

### The integration

| | `TerraformIntegration` | `OpenTofuIntegration` |
| --- | --- | --- |
| `TYPE` | `terraform` | `opentofu` |
| `COMMAND` | `terraform` | `tofu` |
| argv, `TF_VAR_` delivery, saved plans, destroy, outputs, generated `strata` files | yes | inherited, unchanged |
| `parse_version()` | `Terraform vX.Y.Z` | `OpenTofu vX.Y.Z` (the same pattern) |

Both live in `integrations/terraform.py` and are registered in `integrations/registry.py`, the way Trivy and Grype
share `cve_scanner.py` (ADR-0051). Every subcommand and flag strata passes (`init -backend-config -upgrade
-reconfigure`, `validate -json`, `plan -var-file -var -out -destroy -detailed-exitcode -target`, `apply
-auto-approve`, `destroy`, `output -json -raw`, `show -json`) predates the fork, so OpenTofu 1.6.0, its first
stable release, and later run them. There is no version floor in code; `spec.version` on a `type: opentofu`
integration is checked against `tofu version`, in OpenTofu's own numbering.

Everything that keys on capabilities rather than the tool name works for both without change: the deploy flow
(`ENV_VAR_PREFIX` for the payload, `SUPPORTS_SAVED_PLAN` for `deploy apply`, `SUPPORTS_DESTROY`), `deploy steps
--check-tools` and the readiness check, `backend`, the Checkov gate (`framework: terraform` matches both) and
`TERRAFORM_COMPATIBLE_TYPES`.

### One tool per type

The integration document's `spec.type`, not the provisioner's `tool`, decides the class that runs. So two
`strata validate` errors keep them aligned:

- `provisioner_integration_type_mismatch`: a Terraform or OpenTofu provisioner names an integration of another type.
- `integration_command_other_tool`: a `type: terraform` integration whose `spec.command` is `tofu`, or a
  `type: opentofu` one whose command is `terraform` (the binary name, path and `.exe` ignored). `spec.command` stays
  for a renamed or wrapped binary of the same tool.

### The provider registry host

A provider source without a host (`hashicorp/null`, or `null`) is `registry.terraform.io/...` to Terraform and
`registry.opentofu.org/...` to OpenTofu, and `.terraform.lock.hcl` is keyed by the full address. In
`utils/sbom_utils.py`:

- `terraform_provider_address(source, default_registry)` applies a given default host.
- `locked_provider_address(source, locked, default_registry)` resolves a short source to whichever default host the
  root's lock file holds, and to `default_registry` when it holds neither. A source with a host is that host.
- `default_provider_registry(tool)` is `registry.opentofu.org` for `opentofu`, else `registry.terraform.io`.

The lock policy (`controllers/provisioner_lock_policy.py`) knows each provisioner's tool, so it passes the tool's
default, names the tool's own init command (`tofu init`), and shortens either default host in its messages. The
SBOM's Terraform collector walks every root under the build path without knowing its tool, so it relies on the
lock file: a locked OpenTofu root gets one provider component, with its locked version and
`repository_url=registry.opentofu.org`. An unlocked root falls back to Terraform's host, and is reported as
unlocked either way.

### Tests

- `tests/strata/integrations/test_integrations_opentofu.py`: the contract, the registry, version parsing, the type
  check, and every CLI call giving Terraform's argv with `tofu` as the binary.
- The lock policy, the SBOM collector and `sbom_utils` cover an OpenTofu-locked root, a Terraform-locked root under
  `tool: opentofu`, and both defaults.
- `test_deploy_destroy.py`'s real-binary test runs deploy, a destroy preview and destroy for both tools, each
  skipped when its binary is not on `PATH`.

### Out of scope

- `*.tofu` and `*.tofu.json` files (OpenTofu 1.8): `tofu` runs them, but strata reads `*.tf` roots to find the
  `strata` variable, outputs and providers. Revisit when a consumer uses them.
- OpenTofu state encryption keys: an `encryption {}` block in the root module works as is; strata does not deliver
  `TF_ENCRYPTION`.
- A purl type of its own: OpenTofu providers stay `pkg:terraform/...`, with the registry as `repository_url`.

## Related Decisions

- [ADR-0060](../decisions/0060-opentofu-as-a-first-class-provisioner-tool.md) - its own type and subclass, no `tofu`
  behind `tool: terraform`, the registry host rule, OpenTofu and not Terraform in the `-full` image
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) - `tool` is an open string; OpenTofu the
  terraform-compatible leniency
- [ADR-0051](../decisions/0051-local-tools-as-declarable-integration-types.md) - each tool its own type
- [ADR-0035](../decisions/0035-unified-terraform-projection.md) - the `strata` payload OpenTofu receives unchanged

## History

- `opentofu` was a reserved v1 type for a long time: the model, the backend validator, the lock policy and the
  Checkov gate named it, but no integration was registered, so a step failed at build. The licence question around
  the container image made it worth finishing.
- A step could already run `tofu` by binding a `type: terraform` integration with `command: tofu`. It worked, but
  with Terraform's version numbering, init advice and registry host; it is now a validation error with the fix in
  the message, rather than a supported shortcut.
- The registry host was the one real difference found. Reading lock files as Terraform's made the lock policy fail
  a correctly locked OpenTofu root under `deny`, and the SBOM list every short-form provider twice. Taking the host
  from the lock file was chosen over passing the SBOM a root-to-tool map: it needs no new input and is right for
  every locked root.
- The full gap analysis is in [archive/opentofu-support.md](../archive/opentofu-support.md).
