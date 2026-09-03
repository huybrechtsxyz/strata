# Unify Build Artifact Path Construction Across Builder/Deployer Pairs

- Status: proposed
- Date: 2026-08-25

## Context and Problem Statement

Every builder/deployer pair independently re-derives the same per-namespace/per-module
build output path shape, with no shared helper enforcing agreement between the two
sides. Found while writing [ADR 0070](./0070-helm-oci-repositories-and-value-substitution.md):

- Helm builder ([src/strata/builders/helm_builder.py](../../src/strata/builders/helm_builder.py#L230)):
  `deployment_build_path / namespace_name / module_name / "values.yaml"` (+ `"meta.yaml"`)
- Helm deployer ([src/strata/deployers/helm_deployer.py](../../src/strata/deployers/helm_deployer.py#L185)):
  same shape, re-typed independently (plus a third variant at
  [line 212](../../src/strata/deployers/helm_deployer.py#L212) for local chart refs)
- Compose builder ([src/strata/builders/compose_builder.py](../../src/strata/builders/compose_builder.py#L296)):
  `deployment_build_path / namespace_name / "docker-compose.yml"`
- Compose deployer ([src/strata/deployers/compose_deployer.py](../../src/strata/deployers/compose_deployer.py#L114)):
  same shape, re-typed independently

Nothing breaks today — both sides happen to agree — but the agreement is by convention
only. A future change to one side's path shape (e.g. adding a subdirectory) would not
be caught until the other side fails to find its file at runtime.

## Impact Analysis — Verified Inventory of Every Builder/Deployer Pair (2026-09-03)

Traced every builder/deployer pair directly against the current code (not assumed) to
scope exactly which pairs still need fixing before deciding on Option B. The pairs split
into two groups — genuinely still duplicated, and already fixed via an existing shared
helper that can serve as Option B's template.

### Group 1 — Genuinely duplicated (no shared helper today) — 3 pairs, 7 sites

| Pair        | Builder site(s)                                                                                                                                                                                                                                | Deployer site(s)                                                                                                                                                                                                                                                                                                   | Shape                                                  |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------ |
| **Helm**    | [`helm_builder.py:230`](../../src/strata/builders/helm_builder.py#L230) — `module_dir = deployment_build_path / namespace_name / module_name` (then `/ "values.yaml"`, `/ "meta.yaml"`)                                                        | [`helm_deployer.py:347`](../../src/strata/deployers/helm_deployer.py#L347) (`values.yaml`), [`:348`](../../src/strata/deployers/helm_deployer.py#L348) (`meta.yaml`), [`:380`](../../src/strata/deployers/helm_deployer.py#L380) (`chart_ref` for local chart refs) — **3 independent re-derivations in one file** | `deployment_build_path/{namespace}/{module}/...`       |
| **Compose** | [`compose_builder.py:296,301,323,328,397`](../../src/strata/builders/compose_builder.py#L296) — `deployment_build_path / namespace_name / "docker-compose.yml"` (and `/ module_name` for module-level file copies, not needed by the deployer) | [`compose_deployer.py:114`](../../src/strata/deployers/compose_deployer.py#L114) — `deployment_build_path / str(ns_name) / "docker-compose.yml"`                                                                                                                                                                   | `deployment_build_path/{namespace}/docker-compose.yml` |
| **Sync**    | [`sync_builder.py:239`](../../src/strata/builders/sync_builder.py#L239) — `deployment_build_path / stage_name / output_rel` (`output_rel` from `integration.properties["output_file"]`)                                                        | [`sync_deployer.py:142`](../../src/strata/deployers/sync_deployer.py#L142) — `deployment_build_path / self.stage.name / output_file_rel` (same `integration.properties["output_file"]` field, read independently)                                                                                                  | `deployment_build_path/{stage}/{output_file}`          |

**Sync was not in the original inventory** — found during this pass. Same failure mode
as helm/compose: both sides independently call `integration.properties["output_file"]`
and independently join it under `deployment_build_path / stage_name`; nothing enforces
they stay in agreement if either side's join logic changes.

### Group 2 — Already fixed via a shared helper — 3 pairs (terraform, bicep, ansible)

Not part of this ADR's remaining scope. All three route through
[`SolutionController.get_provisioner_path()`](../../src/strata/controllers/solution_controller.py#L553),
whose own docstring already states the intent this ADR is asking for: *"Single source
of truth used by both the builder (copy destination) and the deployer (working
directory)."* This is Option B, already implemented for IaC provisioners — it can serve
as the concrete template for extending the same pattern to helm/compose/sync.

| Pair          | Builder call site                                                                                                                                                                                                     | Deployer call site                                                                                              |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| **Terraform** | [`terraform_builder.py:1600`](../../src/strata/builders/terraform_builder.py#L1600), [`:1397`](../../src/strata/builders/terraform_builder.py#L1397) — `solution_controller.get_provisioner_path(...)` (primary path) | [`terraform_deployer.py:133`](../../src/strata/deployers/terraform_deployer.py#L133) — same call (primary path) |
| **Bicep**     | *(no builder copy step exists for bicep at all — see Known Gap below)*                                                                                                                                                | [`bicep_deployer.py:163`](../../src/strata/deployers/bicep_deployer.py#L163) — same call (primary path)         |
| **Ansible**   | [`ansible_builder.py`](../../src/strata/builders/ansible_builder.py) via `_resolve_ansible_paths()`                                                                                                                   | [`ansible_deployer.py:139`](../../src/strata/deployers/ansible_deployer.py#L139) — same call (primary path)     |

**Caveat — the fallback path, not the primary path, still duplicates independently.**
All three pairs call `get_provisioner_path()` only when `solution_controller is not None`;
each side keeps its own inline fallback for when it's `None` (tests, some dry-run
paths), and those fallbacks are **not** identical to each other or to
`get_provisioner_path()`'s own resolution order:

- `get_provisioner_path()`: `target_path` → else `source_path`.
- `terraform_deployer._get_working_dir()` fallback: `target_path` → else
  `Path("terraform") / iac_model.name` (different second choice).
- `terraform_builder.py:1608`'s inline fallback: `target_path` → else `source_path`
  (matches the helper, but is a second, separately-maintained copy of the same
  two-line logic rather than a call to it).

Same class of risk this ADR exists to flag, just already mostly mitigated rather than
fully closed. Whether to also unify these fallbacks (e.g. by having
`get_provisioner_path()` accept an already-loaded `solution_controller`-free mode
instead of each caller re-implementing its own fallback) is a candidate addition to
Option B's scope — see Remaining Work.

### Related but distinct finding — bicep has no builder-side copy step at all

Not a duplication bug, the opposite: [`terraform_builder.py`](../../src/strata/builders/terraform_builder.py)'s
provisioner-copy logic is explicitly gated on `prov.provisioner == "terraform"` (three
call sites, e.g. [`:1392`](../../src/strata/builders/terraform_builder.py#L1392)) and
there is no `prov.provisioner == "bicep"` branch anywhere in `src/strata/builders/` —
confirmed via a repo-wide search. `bicep_deployer.py` already expects a builder to have
copied `.bicep` files into the build output (its own error message says *"Run 'strata
build run' first to copy IaC artefacts to the build folder"*), but nothing does this
today. This is a separate, real gap in bicep provisioner support — out of scope for
this ADR (which is about reconciling *duplicated* path logic, not adding *missing*
functionality) but worth tracking somewhere before bicep is considered production-ready.

### Related but distinct finding — two divergent, independently-implemented `_resolve_lock_backend()` functions

Found while auditing every `.provisioner ==`/`!=` comparison in the codebase for the
same class of risk this ADR is about (two independent implementations silently drifting
apart). This one is a real correctness bug, not just duplicated path logic:

|         | [`base_deploy_command.py:139`](../../src/strata/commands/deploy/base_deploy_command.py#L139)                                                | [`lock_deploy_command.py:21`](../../src/strata/commands/deploy/lock_deploy_command.py#L21)                                            |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Used by | `strata deploy run` (the real lock-acquire path)                                                                                            | `strata deploy lock status` / `lock release` / `lock history`                                                                         |
| Logic   | Iterates **the deployment's stages**, finds the IaC provisioner matching `stage.provisioner`, uses *that specific provisioner's* `.backend` | Iterates **all workspace provisioners**, returns the backend of the **first** Terraform provisioner that has one — no stage awareness |

`WorkspaceIacModel.backend` is per-provisioner (each entry in `spec.provisioners` can
declare its own independent backend), and a workspace can legitimately declare more than
one Terraform provisioner (e.g. a `networking` stage on one backend, a `compute` stage
on another). For such a workspace, `deploy run` correctly locks/uses the stage-specific
backend, but `deploy lock status`/`release`/`history` will silently report on — or
**release** — whichever Terraform provisioner happens to come first in
`spec.provisioners`, regardless of which stage/deployment was actually asked about.
Worst case: force-releasing the wrong environment's lock. Confirmed both are live,
separately-used code paths (`lock_deploy_command.py` calls its module-level function at
lines 83, 191, and 298 — all three lock subcommands); `base_deploy_command`'s
stage-aware version is never reused there. Not affected by OpenTofu specifically —
OpenTofu is an integration/binary choice (`type: opentofu` under `spec.integrations`),
not a separate `ProvisionerType`, so OpenTofu-backed provisioners still declare
`provisioner: terraform` and hit this exact same bug.

**Fix design (2026-09-03) — reuse, don't build a new helper.** All three `Lock*Command`
classes in `lock_deploy_command.py` already extend `BaseDeployCommand`, so they already
inherit the correct, stage-aware `_resolve_lock_backend(stages)` method — no new shared
abstraction is needed, only deletion of the duplicate:

1. Delete the module-level `_resolve_lock_backend(deployment_service, work_path)` free
   function in `lock_deploy_command.py` entirely.
2. Change all 3 call sites (`LockStatusCommand._execute`, `LockReleaseCommand._execute`,
   `LockHistoryCommand._execute`) to call the inherited method instead, passing **all**
   of the deployment's own stages (these commands have no `--stage` filter, so "every
   stage this deployment could use" is the correct default — the same set
   `base_deploy_command`'s algorithm expects):
   ```python
   stages = self._deployment_service.model.spec.stages or []  # type: ignore[union-attr]
   backend = self._resolve_lock_backend(stages)
   ```
3. Drop the now-unused `ProvisionerType` import from `lock_deploy_command.py` if nothing
   else in that file needs it.

Net effect: one implementation instead of two, reused by all 5 call sites (`run`,
`destroy`, `status`, `release`, `history`).

### Related but distinct finding — provisioner-type comparisons mix string literals and the `ProvisionerType` enum inconsistently

`ProvisionerType` ([`common_models.py:98`](../../src/strata/models/common_models.py#L98))
already exists as the canonical enumeration (`TERRAFORM`, `ANSIBLE`, `SCRIPT`,
`COMPOSE`, `HELM`, `ARGOCD`, `FLUX`, `BICEP`), and roughly half of the
`.provisioner ==`/`!=` comparisons in the codebase already use it. The other half
compares against a bare string literal instead — functionally equivalent today only
because `ProvisionerType(str, Enum)` mixes in `str`, but it means a typo'd literal
(e.g. `"terrafom"`) would silently never match instead of failing loudly the way
`ProvisionerType.TERRAFORM` would (mypy-checked attribute access vs. an unchecked
string), and it's a second, uncoordinated spelling of the same constant. Full inventory:

**Uses a bare string literal (should use `ProvisionerType`):**
- [`terraform_builder.py:123,769,1230`](../../src/strata/builders/terraform_builder.py#L123) — `== "terraform"` (×3)
- [`terraform_builder.py:1392,1585`](../../src/strata/builders/terraform_builder.py#L1392) — `!= "terraform"` (×2)
- [`ansible_builder.py:731`](../../src/strata/builders/ansible_builder.py#L731) — `!= "ansible"`
- [`ansible_builder.py:793`](../../src/strata/builders/ansible_builder.py#L793) — `== "ansible"`

**Already uses `ProvisionerType` (the pattern to standardize on):**
- [`overlap_controller.py:252`](../../src/strata/controllers/overlap_controller.py#L252),
  [`sbom/helm_collector.py:38`](../../src/strata/builders/sbom/helm_collector.py#L38),
  [`sbom/ansible_collector.py:43`](../../src/strata/builders/sbom/ansible_collector.py#L43),
  [`lock_deploy_command.py:39`](../../src/strata/commands/deploy/lock_deploy_command.py#L39),
  [`base_deploy_command.py:157`](../../src/strata/commands/deploy/base_deploy_command.py#L157),
  [`cost_controller.py:487`](../../src/strata/controllers/cost_controller.py#L487),
  [`workspace_model.py:533`](../../src/strata/models/workspace_model.py#L533) — all
  `== ProvisionerType.X` / `!= ProvisionerType.X`.
- [`workspace_model.py:524`](../../src/strata/models/workspace_model.py#L524) — a mixed
  form, `self.provisioner in {str(t) for t in _SYNC_PROVISIONER_TYPES}` (starts from
  the enum set, then stringifies it to compare — arguably fine since `_SYNC_PROVISIONER_TYPES`
  itself is enum-typed).

Not a functional bug today, but a straightforward, low-risk consistency cleanup: change
the 7 bare-string sites (5 in `terraform_builder.py`, 2 in `ansible_builder.py`) to
`ProvisionerType.TERRAFORM` / `ProvisionerType.ANSIBLE`.

### Related but distinct finding — `WorkspaceIacModel.backend` and `.output` aren't validator-restricted to Terraform the way `.properties` is restricted to Ansible

Found while auditing every `ProvisionerType.TERRAFORM` comparison for places where
another provisioner type might silently need the same handling and not get it.
`WorkspaceIacModel.validate_provisioner_fields()`
([`workspace_model.py:520`](../../src/strata/models/workspace_model.py#L520)) already
restricts `properties` to `ProvisionerType.ANSIBLE` only, raising a clear validation
error if declared on any other provisioner type. Two other fields, each documented in
their own field description as Terraform-specific, have no equivalent restriction:

| Field     | Field description says                                                                 | Only ever read by                                                                                                                                                                                                                                             |
| --------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend` | *"Backend configuration for state storage (e.g., Terraform Cloud, S3, Azure Storage)"* | [`base_deploy_command.py:157`](../../src/strata/commands/deploy/base_deploy_command.py#L157) (`_resolve_lock_backend`) and [`overlap_controller.py:252`](../../src/strata/controllers/overlap_controller.py#L252) — both gated `== ProvisionerType.TERRAFORM` |
| `output`  | *"Build output profile for Terraform provisioners..."*                                 | [`terraform_builder.py:128,1257`](../../src/strata/builders/terraform_builder.py#L128) only, always from an already-filtered terraform-only list                                                                                                              |

Concretely: declaring `backend: {type: azurerm, ...}` or `output: {...}` on a
`provisioner: bicep`/`ansible`/`compose`/`helm` entry validates successfully today and
is then silently ignored everywhere — no lock is ever acquired for it, no overlap check
ever runs against it, no tfvars emission profile ever applies. No error, no warning.
This is not "bicep needs the same locking feature" (ARM manages deployment state
server-side; there is no comparable remote-backend-locking concept to port over) — it's
that the schema doesn't reject the combination the way it already does for `properties`,
so a misconfiguration silently has zero effect instead of failing loudly at validation
time. Same class of risk as the `_resolve_lock_backend()` bug above, just a schema gap
instead of a duplicated-logic gap.

## Considered Options

- **A. Status quo.** Leave each builder/deployer pair to independently hardcode the
  path shape it needs.
- **B. Shared path-construction helper(s).** e.g. a `module_build_path(build_path, ns,
  module, filename=None)` function (per deployer type or generic) imported by both the
  builder and deployer side of each pair, so the shape is defined once. **A working
  precedent for this already exists and does not need to be designed from scratch** —
  `SolutionController.get_provisioner_path()` (see Impact Analysis, Group 2) is exactly
  this pattern, already shipped for terraform/bicep/ansible. Extending the same shape
  of helper to helm/compose/sync (Group 1) is the concrete remaining work, not a new
  design.

## Decision Outcome

Not yet decided — deliberately deferred. Out of scope for ADR 0070's bug fixes (neither
bug touches this path-agreement logic), and unifying it properly means touching every
builder/deployer pair (helm, compose, terraform, sync), which is a larger, independent
refactor.

## Remaining Work

<!-- Required while Status is proposed / in-progress / partially-implemented.
     Remove this section once Status becomes implemented. -->

- Not started. Scope is now concrete after the 2026-09-03 inventory pass — see Impact
  Analysis. Decide:
  1. Whether to implement Option B for the 3 genuinely-duplicated pairs (helm, compose,
     sync — 7 sites total), following `get_provisioner_path()`'s existing shape as the
     template (a controller method, or a plain function, taking `deployment_service`,
     `build_path`, and the namespace/module/stage identifiers, returning a `Path`).
  2. Whether to also unify the divergent solution_controller-`None` fallback logic
     already present in the terraform/bicep/ansible pairs (Group 2's caveat) as part of
     the same pass, or leave it as a smaller separate follow-up.
  3. Whether the bicep builder-copy gap (Related but distinct finding, above) belongs in
     this ADR's scope or a new one — it's a missing-functionality bug, not a
     path-duplication bug, so probably its own ADR/issue.
  4. **Done (2026-09-03).** The `_resolve_lock_backend()` divergence (Related but
     distinct finding, above) was a real correctness bug, not a documentation nicety —
     fixed independently of Option B's broader design decision. `lock_deploy_command.py`'s
     duplicate module-level function was deleted; `LockStatusCommand`/`LockReleaseCommand`/
     `LockHistoryCommand` now call the inherited, stage-aware
     `BaseDeployCommand._resolve_lock_backend()` instead. Added 6 direct regression tests
     (`TestResolveLockBackendMatching` in `test_commands_deploy.py`) — this method had zero
     direct test coverage before.
  5. **Done (2026-09-03).** The bare-string vs. `ProvisionerType` enum inconsistency
     (Related but distinct finding, above) — all 7 sites (5 in `terraform_builder.py`, 2 in
     `ansible_builder.py`) converted to `ProvisionerType.TERRAFORM`/`ProvisionerType.ANSIBLE`.
     Verified via the full project-wide `mypy .` run (782 source files, 0 new errors) and
     the terraform/ansible builder test suites (102 tests, all passing unchanged).
  6. **Done (2026-09-03).** Extended `WorkspaceIacModel.validate_provisioner_fields()` to
     reject `backend`/`output` on any provisioner type other than Terraform, mirroring the
     existing `properties`→ansible-only restriction in the same validator. Added
     `TestWorkspaceIacModelProvisionerFieldValidation` (10 tests) in `test_models_workspace.py`
     — this validator had zero direct test coverage before. Verified no bundled `config/`
     example combines `backend:`/`output:` with a non-terraform provisioner (safe to add).

     **Bonus finding while writing tests for the above:** the same validator's `is_sync`
     check was independently broken and unrelated to backend/output — `self.provisioner in
     {str(t) for t in _SYNC_PROVISIONER_TYPES}` built a set of enum reprs
     (`"ProvisionerType.ARGOCD"`) instead of values (`"argocd"`), because `str()` on a
     `class X(str, Enum)` member returns the enum's default `__str__` (`"ClassName.MEMBER"`),
     not `.value`. This meant `is_sync` was **always** `False`, so `source` was wrongly
     required even for `argocd`/`flux` sync provisioners — contradicting the field's own
     documented behavior ("Optional for sync provisioner types"). Confirmed live/real via
     `WorkspaceIacModel.model_validate({"provisioner": "argocd", "source": None, ...})`
     (realistic YAML-shaped input, not just enum-typed test construction). Fixed by comparing
     against `{t.value for t in _SYNC_PROVISIONER_TYPES}` instead. Covered by
     `test_source_not_required_for_sync_provisioner`. Full suite verified green (6356 passed,
     up from 6346; 0 failed) and full-project `mypy .`/`ruff` clean.

