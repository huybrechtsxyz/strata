# OpenTofu support — Work

- Status: draft
- Last updated: 2026-11-12

## Overview

A gap analysis between "Terraform works" and "OpenTofu works" in strata. A provisioner declares `tool: terraform`
and strata runs `terraform` through `TerraformIntegration` (`src/strata/integrations/terraform.py`). `opentofu` is a
known v1 type that is not ported (`src/strata/integrations/registry.py:63`, `_KNOWN_V1_TYPES`), so a provisioner
with `tool: opentofu` fails at `build run` with "no integration registered for type 'opentofu' (a v1 integration
type not yet ported to v2)". Several places already treat OpenTofu as supported, which is part of what this doc
sorts out.

Why now: Terraform 1.6 and later ships under the BSL 1.1, so strata's container image cannot safely include it in a
public image. OpenTofu is MPL 2.0 and could ship in the planned `strata:<v>-full` image, but only once strata runs it.
The image itself is [cli-image.md](../design/cli-image.md); this doc only lists what the image needs from strata.

Evidence is "by reading" unless stated otherwise: no `tofu` binary was available for this review. Line numbers are
from the `feature/parameterset` branch on 2026-11-12.

## Current Design / Progress

### Already in place

These need no change, or only a test once the type is registered:

- `ProvisionerType.OPENTOFU` and `TERRAFORM_COMPATIBLE_TYPES = {TERRAFORM, OPENTOFU}`
  (`src/strata/utils/builtin_types.py:36`, `:55`).
- `backend` is accepted for `tool: opentofu` (`src/strata/models/provisioning_model.py:372-374`), with tests
  (`tests/strata/models/test_models_provisioning.py:90`, `:127`).
- `provisioner_lock_policy` judges `opentofu` provisioners (`src/strata/controllers/provisioner_lock_policy.py:22`,
  `:71`; `tests/strata/controllers/test_provisioner_lock_policy.py:112`). The host problem is gap 3.
- The Checkov gate matches `tool: opentofu` for `framework: terraform`
  (`src/strata/controllers/checkov_controller.py:70-71`; `tests/strata/controllers/test_checkov_controller.py:522`).
- The purl helpers keep a non-default registry host as `repository_url`, and a test covers
  `registry.opentofu.org` (`src/strata/utils/sbom_utils.py:413-430`;
  `tests/strata/utils/test_utils_sbom_utils.py:358`).
- The deploy flow never names a tool. Payload delivery keys on `integration.ENV_VAR_PREFIX`
  (`src/strata/controllers/deploy_controller.py:1384`, `:1618-1646`), `deploy apply` on `SUPPORTS_SAVED_PLAN`
  (`:1246-1255`), `deploy destroy` on `SUPPORTS_DESTROY` (`src/strata/integrations/capabilities.py:215-219`), and
  `deploy steps --check-tools` and the deploy preflight on the shared readiness check
  (`src/strata/controllers/integration_readiness.py:148-220`). An integration that inherits from
  `TerraformIntegration` gets all of it.
- OpenTofu reads `TF_VAR_<name>` like Terraform, so `strata.auto.tfvars.json`, `TF_VAR_strata_secrets` and the
  `TF_VAR_<key>` values reach a `tofu` run unchanged. `step_secrets.py` and `effective_images.py` already reason in
  terms of `ENV_VAR_PREFIX`, not the tool name (`src/strata/controllers/step_secrets.py:62`,
  `src/strata/controllers/effective_images.py:286`).
- Output descriptions and declared variables are read from the root module's HCL, not from the binary
  (`src/strata/utils/terraform_outputs.py:43-57`), so they work for OpenTofu roots that use `*.tf` files.

### It runs today, by accident

`resolve_integration()` constructs the class of the named Integration document's `spec.type`, not of
`provisioner.tool` (`src/strata/controllers/integration_resolution.py:93-100`). `Integration.command` takes
`spec.command` first (`src/strata/integrations/base.py:137-150`). So this runs `tofu` through `TerraformIntegration`
today:

```yaml
# Integration
spec: {type: terraform, command: tofu, version: ">= 1.6"}
# Workspace
provisioners:
  - {name: infra, tool: opentofu, integration: tofu, source: {source_path: infra}}
```

Nothing checks that a provisioner's `tool` matches its integration's `spec.type`
(`src/strata/controllers/semantic_checks.py:1231-1252` only checks `enabled`). The same goes for `tool: terraform`
with `command: tofu`. This is untested, undocumented, and it still hits gap 3. It is useful as a stopgap and as
evidence that the integration class itself needs almost no change.

### Findings

| #  | Gap                                                                  | Kind        | Size |
| -- | -------------------------------------------------------------------- | ----------- | ---- |
| 1  | No `opentofu` integration is registered                              | missing     | S    |
| 2  | No minimum OpenTofu version is stated or checked                     | validation  | S    |
| 3  | Provider addresses assume `registry.terraform.io`                    | correctness | M    |
| 4  | Lock policy and SBOM messages say "terraform init"                   | wording     | S    |
| 5  | `.tofu` files are not read                                           | correctness | M    |
| 6  | `tool` and the bound integration's type are not compared             | validation  | S    |
| 7  | Schema descriptions and config docs disagree about OpenTofu          | docs        | S    |
| 8  | Real-binary tests only run `terraform`                               | tests       | M    |
| 9  | Moving a deployment from Terraform to OpenTofu is not described      | docs        | S    |
| 10 | The `-full` image needs `tofu`, and a test forbids it                | image       | M    |
| 11 | State encryption has no strata support                               | feature     | L    |

#### 1. No `opentofu` integration is registered

`_KNOWN` maps `terraform` to `TerraformIntegration` (`src/strata/integrations/registry.py:40`) and has no
`opentofu` entry. `opentofu` sits in `_KNOWN_V1_TYPES` (`:63`) only to improve the error message. Every path that
resolves an integration by tool therefore fails for `tool: opentofu`: `build run`
(`src/strata/controllers/build_controller.py:778`), `deploy steps` (`src/strata/controllers/deployment_steps.py:168`),
`deploy run` (`src/strata/controllers/deploy_controller.py:1211`) and the readiness check
(`src/strata/controllers/integration_readiness.py:314`).

`TerraformIntegration` hard-codes the parts that differ: `TYPE = "terraform"` (`terraform.py:47`) and
`COMMAND = "terraform"` (`:50`). The constructor rejects a document whose `spec.type` is not the class's `TYPE`
(`base.py:114`), so a single class cannot serve both types without a change there. Everything else is the same CLI
contract: `VERSION_ARGS = ("version",)` (`:56`), `ENV_VAR_PREFIX = "TF_VAR_"` (`:54`), and every subcommand and
flag strata passes.

Flags strata passes (`terraform.py:67-264`): `init -backend-config K=V -upgrade -reconfigure`,
`validate -json`, `output -json -raw NAME`, `show -json [PLAN]`, `plan -var-file -var -out -destroy
-detailed-exitcode -target`, `apply -auto-approve [PLAN] -var-file -var -target`, `destroy -auto-approve -var-file
-var -target`. All of them predate the fork (Terraform 1.5), so every OpenTofu release from 1.6.0, the first stable
one, has them. strata passes no `-input=false` to either tool; that is not specific to OpenTofu.

`parse_version()` looks for `v(\d+\.\d+\.\d+)` (`terraform.py:58-61`), which also matches `tofu version`'s
`OpenTofu v1.8.3`.

**Fix.** `OpenTofuIntegration(TerraformIntegration)` with `TYPE = "opentofu"` and `COMMAND = "tofu"`, registered in
`_KNOWN` and removed from `_KNOWN_V1_TYPES`. This matches v1, where `OpenTofuIntegration` subclassed
`TerraformIntegration` (ADR-0011's correction notes). It can live in `terraform.py`, the way `TrivyIntegration` and
`GrypeIntegration` share `cve_scanner.py`. Update the stale remarks: the registry comment (`registry.py:54`) and
"OpenTofu once ported" (`effective_images.py:264`). Rename the test id `unknown-tool` that covers `tool: opentofu`
(`tests/strata/controllers/test_effective_images.py:336`, `:341`); its expected result does not change.

Argv tests: parametrise `tests/strata/integrations/test_integrations_terraform.py` over both classes. Its `_capture()`
fake replaces `run_command` (`:20-34`), so it needs nothing new except asserting `argv[0] == "tofu"`.

**Size: S.** This is the smallest path to a `tool: opentofu` step that runs plan, apply and destroy.

#### 2. No minimum OpenTofu version is stated or checked

`spec.version` works as for Terraform: the readiness check reads the version when one is declared and compares it
(`integration_readiness.py:183-193`). There is no floor, and `READ_VERSION` stays `False`. Helm has a floor through
`version_problems()` (`src/strata/integrations/helm.py:126`); Terraform has none.

Pre-1.6 OpenTofu releases were alphas and betas, and all flags strata passes exist in 1.6.0, so a floor guards
little. What matters more is that OpenTofu's version numbers are its own: `~> 1.9` for Terraform and `~> 1.9` for
OpenTofu are different feature sets. A team that points `type: terraform` at `tofu` (see "It runs today") gets a
version check against the wrong numbering.

**Fix.** Document `>= 1.6` as the supported floor in the config docs. Do not add `version_problems()` unless a
feature strata uses later needs a newer release (`.tofu` files need 1.8, see gap 5).

**Size: S.**

#### 3. Provider addresses assume `registry.terraform.io`

`terraform_provider_address()` fills in `registry.terraform.io` when a provider source names no host
(`src/strata/utils/sbom_utils.py:392-410`). Under OpenTofu, `source = "hashicorp/null"` resolves to
`registry.opentofu.org/hashicorp/null`, and `.terraform.lock.hcl` is keyed by that full address. Two callers compare
the computed address with the lock file's keys:

- `read_provider_locks()` (`src/strata/integrations/sbom_collectors/terraform_collector.py:69-94`, address at
  `:84`), used by the lock policy (`src/strata/controllers/provisioner_lock_policy.py:14`, `:82-96`). Every
  short-form provider of a correctly locked OpenTofu root is reported as "does not lock". With
  `enforcement: deny` that fails `build run`.
- The SBOM collector (`terraform_collector.py:233`, `:292`). The declared provider gets no version (it looks up the
  `registry.terraform.io` key), and the lock-only pass adds the real `registry.opentofu.org` entry as a second
  component (`:295-296`). The SBOM lists every short-form provider twice, and an info notice
  `sbom_provider_unlocked` calls it unlocked.

The existing tests pass because they write the host out in full (`tests/strata/controllers/
test_provisioner_lock_policy.py:88`, `tests/strata/integrations/test_sbom_collectors_terraform.py:202`). The lock
policy's `_short()` (`provisioner_lock_policy.py:101-103`) also only shortens the Terraform host.

The SBOM collector is harder than the lock policy. The lock policy knows each provisioner and its folder. The
collector walks every `*.tf` file under the build path (`terraform_collector.py:188-216`) and does not know which
tool owns which root.

**Fix.** Give `terraform_provider_address()` a `default_registry` argument. The lock policy passes
`registry.opentofu.org` for `tool: opentofu`. For the SBOM, either pass the collector a map of root folder to tool
(the build already has `materialised`, see `provisioner_lock_policy.py:46-48`), or resolve a short-form provider to
whichever host the covering lock file holds, and fall back to `registry.terraform.io` without a lock file. The
second needs no new input and is right whenever a lock file exists.

**Size: M.**

#### 4. Lock policy and SBOM messages say "terraform init"

The lock policy tells the user to "Run terraform init" (`provisioner_lock_policy.py:88`, `:94`). The SBOM notice
built by `_unlocked_message()` (`terraform_collector.py:111`) is about Terraform roots too.

**Fix.** Name the tool's own command (`tofu init` for `tool: opentofu`), or say "init" without a tool name.

**Size: S.** Do it with gap 3.

#### 5. `.tofu` files are not read

OpenTofu 1.8 reads `*.tofu` and `*.tofu.json` files, and a `.tofu` file replaces the `.tf` file of the same name.
strata only looks at `*.tf` (and `*.tf.json` in one place):

- The `variable "strata"` opt-out check globs `*.tf` (`src/strata/integrations/terraform.py:303`). A root that
  declares `variable "strata"` in a `.tofu` file gets a second, generated declaration, and `tofu` fails on the
  duplicate.
- Declared outputs and variables: `*.tf` and `*.tf.json` (`src/strata/utils/terraform_outputs.py:57`).
- SBOM and lock policy: `*.tf` only (`terraform_collector.py:50`).
- Checkov scans with `framework: terraform`; whether it reads `.tofu` files was not checked.

**Fix.** Either state that strata supports `.tf` roots only, or read `.tofu`/`.tofu.json` for `tool: opentofu`,
applying OpenTofu's override rule. Stating the limit first is enough for most roots.

**Size: M** to support, **S** to document.

#### 6. `tool` and the bound integration's type are not compared

See "It runs today". A provisioner with `tool: opentofu` and an integration of `type: terraform` runs
`TerraformIntegration` (`integration_resolution.py:93-100`), and nothing reports the mismatch
(`semantic_checks.py:1231-1252`). Once `opentofu` is registered, the same document should bind an `opentofu`
integration. A mismatch also confuses the code that reads `provisioner.tool` directly, such as the lock policy
(`provisioner_lock_policy.py:71`) and `_received_variable_keys()` (`effective_images.py:283`), while
`_pinnable_steps()` prefers the integration's type (`semantic_checks.py:262-265`).

**Fix.** A `strata validate` error when a provisioner names an integration whose `spec.type` is a different known
type, with one allowed pair or none (see open question 2).

**Size: S.**

#### 7. Schema descriptions and config docs disagree about OpenTofu

- `ProvisionerBackendModel` and the `backend` field say "Only valid when tool is 'terraform'"
  (`src/strata/models/provisioning_model.py:41`, `:271`), but the validator also accepts `opentofu` (`:372`) and the
  schema rules say so (`:251`). These descriptions feed the exported schemas.
- `docs/config/workspace.md:30-31` lists tools without `opentofu` and says "`backend` (terraform only)", while the
  same page says "Terraform and OpenTofu only" (`:126`).
- [integration.md](../config/integration.md) lists `terraform` in `spec.type` (line 14) and has a Terraform example
  only.
- [provisioner-lock-policy.md](../design/provisioner-lock-policy.md) and
  [deployment-documentation.md](../design/deployment-documentation.md) already name OpenTofu. They become true once
  gaps 1 and 3 are fixed.
- [unified-terraform-projection.md](../design/unified-terraform-projection.md) and
  [terraform-tfvars-parity.md](../design/terraform-tfvars-parity.md) describe Terraform only. One sentence each
  ("OpenTofu gets the same files") is enough.

**Fix.** Correct the two descriptions, add `opentofu` to the tool list and an `opentofu` example to the integration
page, and add the `>= 1.6` floor and the `.tf`-only limit. Regenerate the schemas.

**Size: S.**

#### 8. Real-binary tests only run `terraform`

Unit tests fake the CLI by replacing `run_command` (`tests/strata/integrations/test_integrations_terraform.py:20-34`)
or `run_command`/`shutil.which` (`tests/strata/commands/test_commands_deploy.py:6`). Those need no fake `tofu`.
The tests that run a real binary skip when `shutil.which("terraform")` finds nothing:
`tests/strata/integrations/test_terraform_variable_precedence.py:44`,
`tests/strata/integrations/test_unified_terraform_projection_locals.py:49`,
`tests/strata/controllers/test_deploy_destroy.py:1107`. No workflow under `.github/workflows/` installs
`terraform` or `tofu`, so these tests never run in CI.

**Fix.** Parametrise the real-binary tests over `terraform` and `tofu`, each skipped when its binary is missing. Add
one `tofu` real run of plan, apply and destroy against a `null_resource` or `terraform_data` root with a local
backend. Installing `tofu` in one CI job is cheap (MPL 2.0, single binary) and would make these tests run for the
first time in CI.

**Size: M.**

#### 9. Moving a deployment from Terraform to OpenTofu is not described

State backends and the state format are shared, so `backend` and `-backend-config` need no change. Changing
`tool: terraform` to `tool: opentofu` on an existing deployment means:

- `tofu init` adds `registry.opentofu.org` entries to `.terraform.lock.hcl`; the team commits the new file. Until
  then the lock policy reports the providers as unlocked (once gap 3 is fixed, correctly).
- A saved `<step>.tfplan` from one tool cannot be applied by the other. `deploy plan` and `deploy apply` must run
  the same tool. They do when both read the same workspace, but not if a team switches between the two commands.
- OpenTofu's migration guide is per Terraform version; state written by a newer Terraform than the fork point
  should be checked against it. The first `deploy plan` after the switch should show no changes.

**Fix.** A short how-to in the config docs. No code.

**Size: S.**

#### 10. The `-full` image needs `tofu`, and a test forbids it

`tests/strata/test_cli_image.py:44` asserts that the published image holds no `tofu` (nor `terraform`), because
the pipeline brings its own tool at the version it pins. [cli-image.md](../design/cli-image.md) is being changed
for a `strata:<v>-full` image in parallel; this doc does not decide its contents.

What strata needs for `tofu` in that image: gaps 1 and 3 done, a pinned `tofu` version with a checksum, and a test
for the `-full` variant that expects `tofu` (and still no `terraform`) while the base image test keeps forbidding
both. The `-full` image should also state which OpenTofu version it holds, since `spec.version` is checked against
it.

**Size: M**, mostly in the image work.

#### 11. State encryption has no strata support

OpenTofu 1.7 can encrypt state and plan files. It is configured in the root module (`terraform { encryption {} }`)
or in the `TF_ENCRYPTION` environment variable. A root that configures it in HCL works with strata as is. strata
has no way to deliver a key from a secret store into `TF_ENCRYPTION`, and the SBOM and lock policy do not need it.

**Fix.** None now. State in the docs that HCL-configured encryption works and that strata does not manage keys.

**Size: L** if ever built. Out of scope here.

### Smaller notes

- `TerraformIntegration`'s docstrings say "terraform" throughout; a subclass inherits them. Not worth editing
  beyond the class docstring.
- `deploy destroy --yes` help says "Terraform's '-auto-approve'" (`src/strata/commands/deploy_command.py:839`).
  True for both tools.
- The SBOM purl type stays `pkg:terraform/...` for OpenTofu providers. There is no separate purl type for OpenTofu.

## Related Decisions

- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) — `tool` is an open string; OpenTofu is the
  one terraform-compatible leniency, and v1 modelled it as a subclass.
- [ADR-0021](../decisions/0021-integration-layer.md) — the integration layer and registry; lists `opentofu` as a v1
  type to port.
- [ADR-0035](../decisions/0035-unified-terraform-projection.md) — the `strata` payload and tfvars files OpenTofu
  receives unchanged.
- [ADR-0050](../decisions/0050-one-offline-integration-readiness-check.md) — the readiness check `spec.version` and
  `--check-tools` go through.
- [ADR-0051](../decisions/0051-local-tools-as-declarable-integration-types.md) — each tool its own type, several
  classes in one module (Trivy, Grype); the precedent for gap 1.
- No ADR covers OpenTofu itself. See open question 5.

Related docs: [cli-image.md](../design/cli-image.md), [provisioner-lock-policy.md](../design/provisioner-lock-policy.md),
[sbom-generation.md](../design/sbom-generation.md), [integration-layer.md](integration-layer.md),
[integration-readiness.md](integration-readiness.md), and the style model for this doc,
[helm-gap-analysis.md](../archive/helm-gap-analysis.md).

## Remaining Work / Open Questions

### Proposed order

1. **Gap 1** — register `OpenTofuIntegration`, parametrise the argv tests, and run plan, apply and destroy once by
   hand with a real `tofu`. After this, a `tool: opentofu` step works end to end.
2. **Gaps 3 and 4** — the registry host. Without it an OpenTofu solution with a lock policy fails `build run`, and
   every SBOM double-counts providers.
3. **Gap 7 and gap 2** — docs and schema descriptions, with the version floor and the `.tf`-only limit (gap 5's
   short form).
4. **Gap 6** — the tool/type check, once open question 2 is decided.
5. **Gap 8** — real-binary tests over both tools, and a CI job with `tofu`.
6. **Gap 9** — the migration how-to.
7. **Gap 10** — `tofu` in the `-full` image, following [cli-image.md](../design/cli-image.md).
8. **Gap 5** — read `.tofu` files, if a consumer needs it.
9. **Gap 11** — not planned.

### Open questions

1. **A subclass, or one class with a binary switch?** A subclass `OpenTofuIntegration(TerraformIntegration)` with
   its own `TYPE` and `COMMAND`, or one class whose `TYPE` follows the document.
   **Recommendation: a subclass.** The registry, the constructor's type check (`base.py:114`) and readiness all key
   on one `TYPE` per class. It matches v1 and ADR-0051, and leaves room for OpenTofu-only behaviour (gap 5,
   gap 11) without `if tool == ...` branches.
2. **May `tool: terraform` run `tofu` through `spec.command`?** It works today (see "It runs today").
   **Recommendation: no, once `opentofu` exists.** Report a `type: terraform` integration whose `spec.command`
   resolves to `tofu`, and a provisioner whose `tool` differs from its integration's type, as a `strata validate`
   error with the fix (`tool: opentofu`). Otherwise the registry host, the init advice and the version numbering are
   all wrong for that step. `spec.command` stays for a renamed or wrapped `terraform` binary.
3. **Default registry host in the SBOM.** A short-form provider in an OpenTofu root: purl with
   `repository_url=registry.opentofu.org`, or without a qualifier like Terraform's default?
   **Recommendation: keep the qualifier.** It says where the provider was really downloaded from, the helper already
   does it for a written-out host, and scanners match on namespace and name.
4. **How does the SBOM collector learn which roots are OpenTofu?** A root-to-tool map from the build, or the host
   in the lock file.
   **Recommendation: the lock file first.** It needs no new input, and it is right for every locked root. Fall back
   to `registry.terraform.io` for an unlocked root; that root is already reported as unlocked.
5. **Does this need an ADR?** The tool name is already reserved and the subclass follows ADR-0011 and ADR-0051, so
   gap 1 alone does not. Questions 2 and 3, and shipping `tofu` while not shipping `terraform`, are lasting choices.
   **Recommendation: a short ADR, "OpenTofu as a first-class provisioner tool"**, written when gap 1 starts. It
   records: own type and class, no `tofu` behind `tool: terraform`, the registry host rule, and that the `-full`
   image ships OpenTofu and not Terraform for licence reasons (linking cli-image.md). This doc then names it in
   Related Decisions.
6. **Should `.tofu` files be supported?** **Recommendation: not now.** Document `.tf` roots as the supported form;
   revisit when a consumer uses `.tofu` files.
7. **Should strata manage state encryption keys?** **Recommendation: no.** HCL-configured encryption already works;
   key delivery is a separate feature with its own secret-handling questions.

## Changelog

- 2026-11-12: Created. Gap analysis of OpenTofu support against the Terraform integration: eleven gaps with
  evidence and sizes, a proposed order, and seven open questions with recommendations.
