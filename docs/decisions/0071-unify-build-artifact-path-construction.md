# Unify Build Artifact Path Construction Across Builder/Deployer Pairs

- Status: implemented — all 6 findings from the 2026-09-03 inventory pass are done: Option B extended to helm/compose/sync (item 1), the terraform_deployer and bicep_deployer divergent fallbacks unified (items 2-3), the `_resolve_lock_backend()` duplication and provisioner-type string/enum inconsistency fixed (items 4-5), and the `backend`/`output` validator gap closed (item 6). See Implementation Summary below.
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
paths). **Done (2026-09-03):** `terraform_deployer._get_working_dir()`'s fallback used
to diverge from the other two — `target_path` → else
`Path("terraform") / iac_model.name` — a made-up shape that never actually agreed with
where the builder copies source, unlike `get_provisioner_path()`'s own
`target_path` → else `source_path` order. In practice this path was unreachable in
real CLI usage (`base_command.py` always constructs a real `SolutionController`, so
`solution_controller` is never `None` in production), but it was still a live landmine
for tests/direct/library use, and its docstring didn't disclose the divergence. Fixed by
changing the fallback to `target_path` → else `source_path` (matching
`get_provisioner_path()` and `terraform_builder.py:1608`'s inline fallback exactly), and
adding a `ValueError` when neither is set (previously would have silently produced
`terraform/{name}`, masking a misconfigured provisioner instead of failing loudly).
Covered by 2 updated + 1 new test in `TestTerraformDeployerGetWorkingDir`
(`test_deployers_terraform.py`). `terraform_builder.py:1608`'s inline fallback already
matched and was left as-is (still a separately-maintained copy of the same two-line
logic rather than a call to the helper, but no longer a *divergent* one — deduplicating
it into a shared call is folded into Option B's broader scope, not fixed here).

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

**Compounding bug found alongside — `bicep_deployer.py`'s own `solution_controller is
None` fallback uses a third, independently-different path shape.**
[`bicep_deployer.py:168`](../../src/strata/deployers/bicep_deployer.py#L168):
`self._working_dir = self.build_path / self._iac_model.name` — neither
`get_provisioner_path()`'s `target_path` → else `source_path` order, nor even
`terraform_deployer`'s pre-fix `terraform/{name}` shape, nor does it descend into
`deployment_service.get_build_path(build_path)` first (every other provisioner type's
fallback does). Confirmed via `grep` there is zero existing test coverage for this
branch (`test_bicep_deployer.py` only ever sets `d._working_dir` directly, never drives
it through `validate_workspace()` with `solution_controller=None`). Currently harmless
only because the branch is unreachable in real CLI usage (same reasoning as
`terraform_deployer`'s pre-fix bug) — but it must be corrected as part of closing this
gap, since the new builder-side copy step (below) needs a fallback destination that
actually agrees with where the deployer will look.

**Fix design (2026-09-03) — add a minimal `BicepBuilder`, reusing the existing
`_copy_provisioner_source()` shape rather than inventing a new one.** Bicep needs
strictly less than Ansible's builder does (no generated vars files, no platform.json
dependency, no git-ref pinning currently in scope) — it only needs the copy step every
other IaC provisioner already has. `AnsibleBuilder._copy_provisioner_source()`
([`ansible_builder.py:704`](../../src/strata/builders/ansible_builder.py#L704)) is the
closest, simplest template (no tfvars-validation or lock-backend complexity like
Terraform's version has).

1. **New file `src/strata/builders/bicep_builder.py` — `class BicepBuilder(BaseBuilder)`.**
   - `before_build()`: validate `deployment_service.is_validated()` and that
     `get_workspace_service()` is available (matches Ansible/Compose's `before_build`).
   - `build()`: iterate `workspace_service.model.spec.provisioners`, filter
     `prov.provisioner == ProvisionerType.BICEP`, resolve `repo_root` from `repo_map`
     (falling back to `work_path`), copy `repo_root / source.source_path` →
     `solution_controller.get_provisioner_path(...)` when available, else
     `deployment_service.get_build_path(build_path) / (source.target_path or
     source.source_path)` — reusing the **corrected** fallback shape from the
     `terraform_deployer` fix above, not a fourth new one. Raise the same
     `ValueError`-style error message pattern as `terraform_deployer._get_working_dir()`
     when neither is set. Apply Jinja templates via the inherited
     `self._apply_templates_to_dir()`, matching every other copy-based builder. Same
     error semantics as Ansible's version (`"has no source_path"` /
     `"source directory not found"`). Honors `dry_run` (message-only, still validates
     `src_dir.exists()`).
   - `after_build()`: `return True` — no generated-file existence check needed here;
     `bicep_deployer.validate_workspace()` already checks for `*.bicep` files at deploy
     time, so duplicating that check in the builder would just be a third copy of the
     same assertion.
2. **Wire into `run_build_command.py`.** Add `("bicep", self._execute_bicep_build)` to
   the phase tuple in `_execute()` (next to `("ansible", ...)`), and a
   `_execute_bicep_build()` method that mirrors `_execute_ansible_build()`'s shape
   (`before_build` → `build(repo_map=...)` → `after_build`) minus the
   `platform_model`/vars-generation parts Bicep doesn't need.
3. **Fix `bicep_deployer.py`'s divergent fallback** (Compounding bug, above): change
   `validate_workspace()`'s `solution_controller is None` branch from
   `self.build_path / self._iac_model.name` to the same corrected
   `get_build_path(...) / (target_path or source_path)` shape used by the new builder,
   so builder-writes and deployer-reads agree in both the primary and fallback path —
   required for the new builder's output to actually be discoverable when
   `solution_controller` is `None` (tests, direct/library use).
4. **Tests:**
   - New `tests/strata/builders/test_builders_bicep.py`: successful copy + template
     substitution, missing `source_path` error, missing source directory error, dry-run
     mode, no-op when no bicep provisioners declared, `get_provisioner_path()` used when
     `solution_controller` is set vs. the corrected fallback when `None`, and pinned-ref
     extraction (see below).
   - `tests/strata/deployers/test_bicep_deployer.py`: add coverage for the
     `solution_controller is None` fallback branch in `validate_workspace()` (currently
     zero — same class of gap as `terraform_deployer` before its item #2 fix).
   - `tests/strata/commands/test_commands_build.py` (or wherever `run_build_command`'s
     phase wiring is tested): a test confirming `_execute_bicep_build()` runs as part of
     the phase list.

**Revised (2026-09-03) — match Terraform's git-ref-pinning support instead of leaving it
out, since it's a generic capability, not a Terraform-specific one.**
`SourceModel.reference` ([`common_models.py:259`](../../src/strata/models/common_models.py#L259))
is documented as *"Only valid for git-based sources"* — nothing Terraform-specific about
it — yet `TerraformBuilder._extract_source_at_ref()` is the only place that actually
honors it. Checked while answering "why not match Terraform where possible":
`AnsibleBuilder._copy_provisioner_source()` has **zero** references to `.reference`
anywhere — declaring `reference: v1.2.0` on an ansible provisioner's source validates
successfully today and is then silently ignored; the builder copies whatever the current
working-tree checkout happens to be instead of the pinned ref. Same bug class as the
`backend`/`output` schema gap above, just for a builder-side no-op instead of a
validator gap. `_extract_source_at_ref()`'s body
([`terraform_builder.py:1659`](../../src/strata/builders/terraform_builder.py#L1659))
has no Terraform-specific coupling at all — it only uses `self._messages` (a
`BaseBuilder` attribute), `shutil`, and `GitIntegration` — so it can move as-is, not be
reimplemented. Revised plan:

5. **Move `_extract_source_at_ref()` from `TerraformBuilder` to `BaseBuilder`** —
   relocate the method unchanged (no logic changes; it's already fully generic), so all
   copy-based builders can call `self._extract_source_at_ref(...)`.
6. **Fix `AnsibleBuilder._copy_provisioner_source()`** to check `source.reference` and
   call the (now-shared) `self._extract_source_at_ref(...)` before falling through to
   the plain `shutil.copytree`, mirroring `TerraformBuilder`'s exact conditional
   structure (dry-run message, then extract-at-ref, then apply templates).
7. **`BicepBuilder.build()`** (item 1, above) also checks `source.reference` and calls
   the shared `self._extract_source_at_ref(...)` first, exactly like Terraform/the
   now-fixed Ansible — so all three IaC-provisioner builders behave identically for
   pinned-ref sources, not two-out-of-three.
8. `TerraformBuilder._copy_provisioner_source()` itself needs no behavior change — only
   the helper's *location* moves; its call sites (`self._extract_source_at_ref(...)`)
   are unaffected since it's still an inherited method.

Out of scope, still: `strata build plan`'s temp-build path in `plan_build_command.py`
only builds Platform/Terraform/Ansible today — Compose/Helm/Sync/Bicep are all equally
absent there, a pre-existing, broader gap unrelated to this specific finding.


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

### Fix design (2026-09-04) — Option B for Group 1 (helm, compose, sync)

Extends the exact pattern already shipped for Group 2 — three new methods next to
`get_provisioner_path()` in `SolutionController`'s "Canonical build path helpers"
section, each a single source of truth called from both the builder and deployer side
of its pair. Unlike Group 2, none of these three shapes has a `target_path`-vs-
`source_path` branching choice, so there is no equivalent of the divergent-fallback bug
found (and fixed) for `terraform_deployer` in item #2 — the `solution_controller is
None` fallback at every call site is just the identical one-line expression the helper
itself computes, not a second, independently-designed shape.

**1. Helm — `get_module_build_path(deployment_service, build_path, namespace_name,
module_name) -> Path`.**
```python
def get_module_build_path(
    self,
    deployment_service: "DeploymentService",
    build_path: Path,
    namespace_name: str,
    module_name: str,
) -> Path:
    """Return the canonical build directory for a namespace module (Helm).

    Single source of truth used by HelmBuilder (values.yaml/meta.yaml write
    destination, local chart source copy destination) and HelmDeployer
    (values_file/meta_file read location, local chart_ref resolution) — ADR-0071.
    """
    return deployment_service.get_build_path(build_path) / namespace_name / module_name
```
Replaces 4 independent re-derivations with 1 call each:
- [`helm_builder.py:230`](../../src/strata/builders/helm_builder.py#L230) —
  `module_dir = ...` becomes
  `module_dir = solution_controller.get_module_build_path(deployment_service, build_path, namespace_name, module_name) if solution_controller is not None else deployment_build_path / namespace_name / module_name`
  (then `/ "values.yaml"`, `/ "meta.yaml"` as today).
- [`helm_deployer.py:347`](../../src/strata/deployers/helm_deployer.py#L347) (values),
  [`:348`](../../src/strata/deployers/helm_deployer.py#L348) (meta),
  [`:380`](../../src/strata/deployers/helm_deployer.py#L380) (local chart_ref) — all
  three currently re-derive independently inside the same per-module loop iteration;
  replaced by computing `module_dir` once per iteration via the same call, then
  `values_file = module_dir / "values.yaml"`, `meta_file = module_dir / "meta.yaml"`,
  `chart_ref = str(module_dir)` for the local-chart branch.

**2. Compose — `get_namespace_compose_path(deployment_service, build_path,
namespace_name) -> Path`.**
```python
def get_namespace_compose_path(
    self,
    deployment_service: "DeploymentService",
    build_path: Path,
    namespace_name: str,
) -> Path:
    """Return the canonical docker-compose.yml path for a namespace (Compose).

    Single source of truth used by ComposeBuilder (write destination) and
    ComposeDeployer.validate_workspace() (discovery) — ADR-0071.
    """
    return deployment_service.get_build_path(build_path) / namespace_name / "docker-compose.yml"
```
- [`compose_builder.py`](../../src/strata/builders/compose_builder.py) — the 4
  `docker-compose.yml`-related sites (lines 296, 301, 323, 328) all live inside the same
  `_build_namespace()` method; replaced by computing the path once at the top of that
  method via the new helper and reusing the local variable for the dry-run/pass-through/
  write branches, instead of 4 independent re-derivations.
  **Line 397 (`_copy_namespace_module_files()`, `deployment_build_path / namespace_name
  / module_name` for per-module file copies) is a different, builder-only shape with no
  deployer counterpart — out of scope, left unchanged**, matching the ADR's own
  Impact Analysis note.
- [`compose_deployer.py:114`](../../src/strata/deployers/compose_deployer.py#L114) —
  `compose_file = ...` becomes a call to the same helper.

**3. Sync — `get_sync_output_path(deployment_service, build_path, stage_name,
output_rel) -> Path`.**
```python
def get_sync_output_path(
    self,
    deployment_service: "DeploymentService",
    build_path: Path,
    stage_name: str,
    output_rel: str,
) -> Path:
    """Return the canonical rendered-manifest path for a sync stage.

    Single source of truth used by SyncBuilder (render/write destination) and
    SyncDeployer.validate_workspace() (discovery) — ADR-0071. output_rel comes from
    the stage's integration.properties["output_file"], read independently by both
    sides today.
    """
    return deployment_service.get_build_path(build_path) / stage_name / output_rel
```
- [`sync_builder.py:239`](../../src/strata/builders/sync_builder.py#L239) —
  `output_path = ...` becomes a call to the new helper.
- [`sync_deployer.py:142`](../../src/strata/deployers/sync_deployer.py#L142) —
  `rendered_file = ...` becomes a call to the new helper.

**Fallback pattern at every call site** (matching Group 2's existing style exactly,
for consistency — not because these shapes need a *different* fallback, since there is
only one possible shape here):
```python
dest = (
    solution_controller.get_module_build_path(deployment_service, build_path, ns, mod)
    if solution_controller is not None
    else deployment_service.get_build_path(build_path) / ns / mod
)
```

**Tests:** each of the 3 new `SolutionController` methods gets direct unit tests
(mirroring however `get_provisioner_path()` itself is tested, if at all — to be
confirmed during implementation) plus updated/added coverage at each of the 7 call
sites confirming the `solution_controller`-present and `-None`-fallback paths both
resolve to the identical location (the same regression-test shape used for item #2's
`terraform_deployer._get_working_dir()` fix).

**Out of scope for this design:** `compose_builder.py:397`'s per-module file-copy path
(no deployer counterpart, not a duplication risk) and any change to Group 2's
already-shipped `get_provisioner_path()` itself.

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
  design. **Design for this extension written 2026-09-04 — see "Fix design" above.**

## Decision Outcome

**Adopted: Option B**, extending `SolutionController`'s existing canonical-path-helper
pattern to helm/compose/sync via 3 new methods (design above). Implemented
2026-09-04 — see Implementation Summary below.


## Implementation Summary

All 6 findings from the 2026-09-03 inventory pass (Impact Analysis) are complete:
  1. **Done (2026-09-04).** Option B for the 3 genuinely-duplicated pairs (helm,
     compose, sync — 7 sites total) — 3 new `SolutionController` methods
     (`get_module_build_path`, `get_namespace_compose_path`, `get_sync_output_path`),
     following `get_provisioner_path()`'s existing shape exactly (see the "Fix design
     (2026-09-04)" subsection above for full signatures). All 7 call sites updated:
     `helm_builder.py` (`_build_namespace()` + `_copy_namespace_module_files()`,
     which turned out to share the same `module_dir` shape and both needed the
     fix — not just the 1 site originally scoped), `helm_deployer.py`
     (`validate_workspace()`'s 3 re-derivations consolidated into 1 call per
     iteration), `compose_builder.py` (`_build_namespace()`'s 4 sites consolidated
     into 1 call reused via a local variable — `_copy_namespace_module_files()`'s
     per-module-file shape correctly left untouched, no deployer counterpart),
     `compose_deployer.py`, `sync_builder.py`, `sync_deployer.py`. Added 18 new
     tests across 7 files — `test_builders_sync.py` and `test_deployers_sync.py`
     are entirely new (SyncBuilder/SyncDeployer had zero test coverage of any kind
     before this), plus new `TestSolutionControllerCanonicalBuildPathHelpers`
     direct-unit-test coverage for all 4 canonical path helpers (including
     `get_provisioner_path()` itself, which also had zero direct coverage before).
     Verified: full-project `ruff`/`mypy` clean (783 files, same 2 pre-existing
     unrelated errors), full suite 6398 passed (up from 6380), 16 skipped, 0 failed.
     A new mypy+MagicMock gotcha was found and fixed along the way (saved to user
     memory): adding a type annotation to one test-helper parameter makes mypy
     fully check that function body, surfacing `.return_value`-on-real-method
     false positives that stay silent everywhere else in a file that otherwise
     relies on being fully unannotated (mypy's default "skip untyped defs").
  2. **Done (2026-09-03).** The divergent `terraform_deployer._get_working_dir()`
     fallback (Group 2's caveat, above) has been unified with `get_provisioner_path()`'s
     resolution order (`target_path` → else `source_path`); a `ValueError` is now raised
     when neither is set instead of silently fabricating a `terraform/{name}` path.
     `terraform_builder.py:1608`'s inline fallback already matched and needed no change.
     Deduplicating these inline fallbacks into a single shared call (rather than
     independently-maintained copies) remains folded into Option B's broader scope.
  3. **Done (2026-09-04).** The bicep builder-copy gap (Related but distinct finding,
     above) is fixed: new `src/strata/builders/bicep_builder.py` (`BicepBuilder`),
     wired into `run_build_command.py`'s phase list as `_execute_bicep_build()`, plus
     `bicep_deployer.py`'s own divergent `solution_controller is None` fallback
     (Compounding bug, above) replaced with a `_get_working_dir()` method mirroring
     `terraform_deployer`'s corrected shape (`target_path` → else `source_path`, raising
     `ValueError` when neither is set). Also implemented as part of the same pass:
     `TerraformBuilder._extract_source_at_ref()` moved to `BaseBuilder` unchanged (no
     Terraform-specific coupling), and `AnsibleBuilder._copy_provisioner_source()` fixed
     to honor `source.reference` (previously silently ignored it — a real, separate bug
     found while confirming Bicep should get ref-pinning parity from day one). All three
     IaC-provisioner builders (Terraform, Ansible, Bicep) now share the same copy +
     ref-pinning behavior via `BaseBuilder`. Added 23 new tests: 13 in
     `test_builders_bicep.py` (before/after/build, no-op cases, repo_map resolution,
     `get_provisioner_path()` vs. fallback, ref-pinning), 6 in `test_builders_ansible.py`
     (basic copy — previously zero coverage — plus the new ref-pinning behavior), 4 in
     `test_bicep_deployer.py` (`_get_working_dir()` unit tests + an end-to-end
     `validate_workspace()` fallback test); updated `test_commands_build.py`'s
     `_PHASES` list to include `"bicep"` (would otherwise have broken that test, since
     the real `_execute_bicep_build()` would now run unmocked). Verified: full-project
     `ruff`/`mypy` clean (782 files, same 2 pre-existing unrelated errors), full suite
     6380 passed (up from 6357), 16 skipped, 0 failed. Kept as its own item rather than
     folded into #1/#2 because it was missing functionality, not duplicated-path
     reconciliation — still undecided whether it ultimately belongs in this ADR or
     should be split into its own ADR/issue in retrospect.
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

## Addendum (2026-09-04) — full provisioner-type audit, one more ref-pinning gap found

After Status became `implemented`, all 8 `ProvisionerType` values were audited end to
end (builder existence, deployer existence, whether `WorkspaceIacModel.source` is
actually read, ref-pinning support, `backend`/`output`/`properties` restriction) to
check nothing from this ADR's fixes was missed for any provisioner type.

**Non-findings (verified, not bugs):**
- `source` being required for `helm`/`compose`/`script` (not just terraform/ansible/
  bicep) is documented as intentional in the field's own docstring and is genuinely
  used — `workspace_service.py` cross-references every non-sync provisioner's
  `.source.repository` against solution repositories, independent of whether the
  builder reads `.source_path` for path construction.
- `script` having no builder is correct — `ScriptDeployer` runs lifecycle scripts
  directly against `work_path`/`deployment.spec.lifecycle`, never reads
  `WorkspaceIacModel` at all, and needs no build-time artifact copy.
- `argocd`/`flux` (sync) and `script` never read `WorkspaceIacModel.source` — ref-pinning
  doesn't apply to them.
- `compose` has no raw external-source copy step (it generates YAML from
  `module.spec.services`), so `.reference` doesn't apply there either.

**Finding — Helm's local chart copy silently ignored `module.spec.source.reference`.**
Same bug class as the Ansible gap fixed earlier in this ADR (Related but distinct
finding, above), just one level down: at the **module** level
(`ModuleModel.spec.source`), not the provisioner level. In
`helm_builder.py`'s `_build_namespace()`, the local-chart (non-registry) copy branch
did a plain `shutil.copytree(src_dir, module_dir, ...)` from the current working-tree
checkout, never checking `source.reference` — even though `SourceModel.reference` is
documented as generic ("only valid for git-based sources"), not Terraform-specific.
Declaring `reference: v1.2.0` on a helm module's local chart source validated
successfully and was silently ignored.

**Fix:** restructured the local-chart copy branch into a 3-way split (reference /
dry-run / normal copy), mirroring Terraform/Ansible/Bicep's exact structure — when
`source.reference` is set, calls the same shared `BaseBuilder._extract_source_at_ref()`
helper (already used by all three IaC-provisioner builders) instead of a fourth
independent implementation. Added `TestHelmBuilderLocalChartRefPinning` (3 tests:
dry-run message, non-git-dir fallback, no-reference standard copy) to
`test_builders_helm.py`. Also fixed a latent test-authoring trap discovered while
adding these: the pre-existing `TestHelmBuilderLocalChartTemplates._build_with_local_chart`
helper never set `src.reference` on its `MagicMock()` source, so it defaulted to a
truthy `MagicMock` instance — meaning those pre-existing regression tests were silently
being routed through the (now-added) reference-extraction branch instead of the
plain-copy branch, without ever failing, purely by coincidental identical output.
Fixed by explicitly setting `src.reference = None` there. Verified: full-project
`ruff`/`mypy` clean (783 files, same 2 pre-existing unrelated errors), full suite 6401
passed (up from 6398), 16 skipped, 0 failed.

