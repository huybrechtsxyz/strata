# Decisions

Canonical record of architectural decisions made by the squad.

---

## AD-NET-1: CidrSourceModel as reusable union type

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Dedicated `CidrSourceModel(value | var | secret)` rather than inlining three fields. Used in two positions: `address_space` (list) and `subnet.cidr` (single). Avoids duplication and makes union validation DRY.

---

## AD-NET-2: Subnets required per network

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

`min_length=1` on subnets. A network without subnets has no operational value — resources reference subnets, not bare networks.

---

## AD-NET-3: Peering as lightweight reference

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

`(name, target)` pairs only. No routing config, no gateway settings. Strata declares intent; Terraform implements. Keeps model ~3 lines per peering.

---

## AD-NET-4: Qualified subnet references

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

`<network_name>/<subnet_name>` string on `WorkspaceResourceModel.subnet`. Explicit, grep-friendly, trivially splittable. Dot notation rejected (PlatformName regex conflict).

---

## AD-NET-5: CIDR overlap severity

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Warning for non-peered networks. Hard error for mutually-peered networks. Non-peered may legitimately overlap (isolated envs); peered overlap fails at provider level.

---

## AD-NET-6: Deferred CIDR validation for var/secret

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Parse-time validates syntax only. Build-time re-validates after variable injection. Models load without environment context.

---

## AD-NET-7: DNS-style merge strategy

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Network merge by name (last-wins). Subnet merge by `(network, subnet)` replacement. Post-merge CIDR re-validation.

---

## AD-NET-8: Separate tfvars file

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

`networks.auto.tfvars.json` — consistent with `dns.auto.tfvars.json`, `firewalls.auto.tfvars.json`.

---

## AD-NET-9: No provider field

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Networks inherit provider from workspace topology. No redundant `spec.provider`. Unlike DNS which needs per-zone provider routing.

---

## AD-NET-10: Field name `spec.networks`

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Plural list, consistent with `spec.firewalls`, `spec.dns_zones`, `spec.namespaces`.

---

## INFO: Network kind test patterns

**Date:** 2026-06-10 | **Author:** Livingston | **Status:** INFO

Network tests follow DNS test pattern: inline dict helpers for unit tests, YAML fixtures for integration tests, programmatic construction for merge tests. Non-peered overlap warning (V11) not tested as `ValidationError` — requires service/warning layer.

---

## AD-GUIDE-1: `guide` is a top-level command, not under `sln`

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

`guide` is a UX navigation aid applicable before and after init. Placing it under `sln` would bury it from first-time users who haven't yet learned the group structure. `strata guide` must be reachable with zero prior knowledge of the CLI.

---

## AD-GUIDE-2: `INIT_REQUIRED = False`

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

The most important guide use case is "I just cloned this repo — what do I do?" Guide must work before init, because guide teaches you how to init. Mirrors `StatusCommand`.

---

## AD-GUIDE-3: `_load_solution()` delegates to the solution controller

**Date:** 2026-06-10 | **Author:** Linus | **Status:** APPROVED

`_load_solution()` reads `self._solution_controller.solution` (already loaded by `_initialize()`). Does NOT call `SolutionService` directly. Phase 1 ⬜ vs ⚠️ is distinguished by `Path.exists()` check before reading the controller.

---

## AD-GUIDE-4: Exit code always 0

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

`guide` is advisory. It is not a validation gate. `execute()` always returns `True` and calls `_finalize(success=True)`. A CI pipeline calling `strata guide` must never fail because of it.

---

## AD-GUIDE-5: No `--profile` flag

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Guide always reads the active profile. A `--profile` flag would create a phantom view of state that doesn't match what deploy commands will use, undermining the command's purpose.

---

## AD-GUIDE-6: Phase 2 (tools check) deferred to v2

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Tool availability requires subprocess invocations — out of scope for v1. `IntegrationController` already provides this; it will be wired in v2. Deferring keeps v1 fast, pure read-only, and testable without subprocess mocks.

---

## AD-GUIDE-7: Phase 9 (deploy history) deferred to v2

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

No clean read-only deploy history artifact exists yet. Deploy state tracking is a separate workstream. Deferred until that work lands.

---

## AD-GUIDE-8: ⚠️ (partial) not ❌ (failure) in v1

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

v1 is purely advisory. No state is condemned as wrong. ❌ is reserved for v2 once invalid states are formally defined.

---

## AD-GUIDE-9: `ChecklistItem` / `NextStepItem` are module-local dataclasses

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Single consumer (`show_guide_command.py`). No shared extraction needed. Follows implementation discipline — no premature abstraction.

---

## AD-GUIDE-10: Console rendering is single-pass via `click.echo()`

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Matches the existing pattern in `StatusCommand`. No template engine, no string buffer. Direct, readable, testable.

---

## AD-GUIDE-13: Built-in hints in `src/strata/data/guide-hints.yaml`

**Date:** 2026-06-10 | **Author:** Linus | **Status:** APPROVED

Hint file loaded at `Path(__file__).parent.parent.parent / "data" / "guide-hints.yaml"` (3 parents up from `commands/guide/`). Phase 3 hint is `null` in YAML — always built dynamically from the missing repos list.

---

## AD-GUIDE-14: `.strata/guide.yaml` shallow-merge overrides

**Date:** 2026-06-10 | **Author:** Linus | **Status:** APPROVED

Project-level overrides are shallow-merged: scalar keys replace scalars directly; `phases` and `kinds` sub-keys are merged individually (per-phase, per-kind replacement). Full-section replacement is not supported.

---

## INFO: Guide command test patterns

**Date:** 2026-06-10 | **Author:** Livingston | **Status:** INFO

`IMPL_MISSING` guard (try/except ImportError + pytestmark skipif) used for all 26 tests until implementation lands. Real files over mocking — tests write actual `solution.json` and YAML fixtures to `tmp_path`. JSON shape assertions use `json.loads(result.output)` directly. Local repos identified by `url: ""` + `type: "local"`. `.strata/guide.yaml` override format: `phases: {6: {hint: "..."}}`. File mode `next_steps` entries must have `action` and `hint` keys.
