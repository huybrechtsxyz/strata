# Design — `deploy show`: make it a real preview of `deploy run`

Scratch design note (underscore-prefixed: excluded from the Sphinx build and from
the docs-index check). Not an ADR — no new convention. Gap A applies a pattern
ADR-0083 Phase 6 already established; Gap B aligns a flag with the meaning every
other command already gives it. Delete once implemented.

---

## Premise

`deploy show` is the preview surface. A user runs it to answer *"what would
`deploy run` do?"* — so it should answer that question in the same vocabulary.
Today it answers a subtly different one, in two ways:

- it lists every stage with no indication which would be **skipped** (Gap A)
- its `--stage` means something no other command's `--stage` means (Gap B)

Both make the preview misleading rather than merely incomplete.

### What "works like `deploy run`" does *not* mean

It must **not** filter disabled stages out. `deploy run` gates; `deploy show`
must not — otherwise you cannot preview what a run would skip, and "disabled"
becomes indistinguishable from "deleted", which is the exact confusion ADR-0083
exists to remove. D11's rule, sharpened in that ADR:

> A read-only surface must never **filter** on `enabled` — and a human-facing one
> should always **disclose** it.

So: **the same stage *selection* as run, in `INSPECT` mode, with gating
disclosed.** That is a more faithful preview of a run than literally filtering
would be.

---

## Gap A — the preview does not disclose gating

**Created by ADR-0083.** Today a user reads `enable_dispatcher_api: false` in the
features section and `dispatcher_api` in the stage list, and joins them by hand —
the confusion ADR-0083 removed from the audit trail, relocated to the preview.

`build plan` already does both halves of the rule; `deploy show` does only the
first.

### Obstacle: `resolved` does not exist yet where the rows are built

```
_collect()
  ├─ L132-142  build stage_rows                     ← needs `resolved`, has none
  └─ L144      _collect_environment(env_service)
                 └─ L185  _resolve_values(strict=False)   ← produced here
```

`_resolve_values` must be **hoisted** above the stage-row loop and the result
threaded into `_collect_environment(...)`. Calling it twice instead would hit the
secret stores twice — unacceptable for a read-only command.

### Not an obstacle: no workspace needed

ADR-0026 keeps `show` environment-only (`_load_related_services` loads the
environment and deliberately never the workspace). `evaluate_enabled()` needs only
the stage plus `ResolvedValues`, so disclosure does not break that constraint.

### Changes

| Where                                  | Change                                                                           |
| -------------------------------------- | -------------------------------------------------------------------------------- |
| `_collect()`                           | hoist `_resolve_values(strict=False)` above the stage loop; pass `resolved` down |
| `_collect_environment(env_service)`    | accept `resolved` instead of resolving internally                                |
| stage row (L134-142)                   | add `enabled` (raw), `would_skip` (bool), `skip_reason` (str or None)            |
| `_print_environment_detail()` (L300-4) | render a marker on a gated stage line                                            |

Reuse `strata.utils.stage_selection.evaluate_enabled(stage, resolved)` — the same
call `plan_build_command._plan_stage` makes, so the two previews cannot disagree.

Console shape, matching the existing line format:

```
Stages (2):
  • core_iac  (platform_iac)
  • dispatcher_api  (dispatcher_api)  ⏭️ disabled — 'enabled' (${feature:enable_dispatcher_api}) resolved to 'false'
```

---

## Gap B — `--stage` means something different here than everywhere else

**Pre-existing, independent of ADR-0083.** `cli_deploy.py` L334:

```python
@click.option(
    "--stage",
    help="Filter secrets visibility to a specific stage's allowlist.",
)
```

Every other command's `--stage` selects *which stages to act on*. Here it is
documented as scoping *which secrets are visible* — so a user cannot preview a
scoped run, which is the main reason to reach for `show --stage` in the first
place.

### It is unimplemented, so there is no compatibility constraint

`self._stage` is stored (`show_deploy_command.py` L54) and **never read**;
`_collect_environment` lists every declared secret regardless. `deploy show
--stage X` silently ignores `X` today.

That matters for the design: we are not *changing* a meaning, we are choosing one
for the first time. Nothing that works today breaks.

### ADR-0020 already settles which meaning

[ADR-0020](decisions/0020-cli-parameter-consistency-standard.md) is the CLI
parameter consistency standard. Its spec for `deploy show` (L551) lists only
`-f`, `--work-path`, `--output`, `--verbose`, `--quiet` — **no `--stage` at
all.** The flag was added outside the standard.

The same ADR *does* specify it for the analogous read-only command:

> `strata deploy plan` — `--stage NAME` (optional) — Limit display to specific
> deployment stage

So unifying `--stage` on `show` is not a deviation from an existing decision, it
is a *return* to one. Adding `--scope` alongside follows `run`/`destroy`.

**Doc bug, same area:** [commands.md L1534](platform/commands.md) documents the
unimplemented meaning ("Filter secrets visibility to a specific stage's
allowlist") as though it works. Both docs need updating with the code.

### Unify it — the documented behaviour survives as a consequence

Give `--stage` the standard meaning (select stages). The secrets scoping then
falls out rather than being a special case:

| `--stage X`     | Effect                                                       |
| --------------- | ------------------------------------------------------------ |
| stage list      | scoped to `X`                                                |
| secrets         | scoped to what `X` can access — i.e. what a run would inject |
| everything else | unchanged                                                    |

Add `--scope` too, for parity with `run`/`destroy` — the selection pair should
behave identically on the preview.

### Reuse, do not re-implement

- **Selection:** `self._resolve_stages(StageSelectionMode.INSPECT)` — the
  ADR-0083 Phase 8 mixin `ShowDeployCommand` already inherits. `INSPECT` is the
  point: it applies `--stage`/`--scope` without gating.
  This supersedes the earlier "do not consolidate this `--stage`" note, which was
  protecting a meaning that was never implemented.
- **Secret scoping:** `provisioner_resolution.allowed_secret_keys_for_stages(stages, all_keys)`
  — already the single source of truth, shared by the Terraform and Helm builders.
  Its contract matters here:
  - empty `stages` → **all** keys (unscoped fallback, not "none")
  - any stage with `secrets: ['*']` → all keys
  - otherwise → union of the selected stages' `secrets:` allowlists

  With no `--stage`/`--scope`, every stage is selected, so the union is the
  natural unscoped view — today's behaviour, unchanged.

### Why only secrets are scoped, not variables or features

This looks arbitrary and is not — it mirrors exactly what a run does. From
[`terraform_builder._collect_declared_input_keys`](../src/strata/builders/terraform_builder.py):

> Variables and features are never stage-scoped at deploy time (they're injected
> into every stage unfiltered — see `ResolvedValues.for_stage()`) … Secrets ARE
> stage-scoped at deploy time via each stage's `secrets:` allowlist.

So `--stage X` scoping secrets *and leaving variables/features whole* is the
accurate answer to "what would stage X receive?". Stated here because the
asymmetry otherwise reads as an oversight someone will later "complete".

### The null-guard shrinks, it does not disappear

`_collect()`'s guard checks **two** services:

```python
if self._deployment_service is None or self._configuration_service is None:
```

`_resolve_stages` only covers `_deployment_service`, so a `_configuration_service`
check remains — the remotes section needs it. Same caveat as `status` in ADR-0083
Phase 8; noted so the migration is not surprised by it.

### Changes

| Where                    | Change                                                                |
| ------------------------ | --------------------------------------------------------------------- |
| `cli_deploy.py` L334     | reword help to the standard wording; note secrets scope with it       |
| `cli_deploy.py`          | add `--scope`                                                         |
| `__init__`               | accept `scope`; set `self._scope`                                     |
| `_collect()`             | select via `_resolve_stages(INSPECT)`; build rows from the selection  |
| `_collect_environment()` | scope `secret_rows` via `allowed_secret_keys_for_stages()`            |
| unknown stage name       | handled by the shared helper — errors naming it and listing available |

### Decided: list out-of-scope secrets, do not omit them

When `--stage X` scopes the secrets, a secret outside `X`'s allowlist is **still
listed**, marked out of scope, with its value omitted.

Omitting the row entirely was the simpler option and is **rejected**, because it
reproduces the exact confusion this whole line of work exists to remove: a
vanished secret is indistinguishable from one that was never declared. That is
ADR-0083's "deliberately excluded vs. absent" problem, one level down — and the
common debugging case (*"why can't my stage see `SECRET_FOO`?"*) is precisely the
one omission answers worst. Listing it answers immediately: *declared, but not in
this stage's allowlist.*

The row already carries a per-item `resolved: bool`, so a second boolean fits the
established shape:

| Field      | Out-of-scope secret                                         |
| ---------- | ----------------------------------------------------------- |
| `key`      | shown                                                       |
| `in_scope` | `false`                                                     |
| `value`    | `null` — a run of that stage genuinely would not receive it |
| `store`    | shown                                                       |
| `resolved` | shown                                                       |

Omitting the **value** is not a security boundary — the same operator sees every
value by dropping `--stage`. It is modelling: the view answers "what would this
stage get?", and the answer for an out-of-scope secret is "nothing".

---

## Plan

Three steps, each independently landable and green on `scripts/Check.ps1` (ruff,
`mypy ./src ./tests`, pytest, docs build). Step 1 is a pure refactor and should
land on its own so the two behaviour changes that follow are reviewable in
isolation.

There is **no existing test module for this command** — `tests/strata/commands/`
has no `*show*` file. Step 1 therefore creates one and characterises today's
behaviour *before* anything changes; without that there is no baseline proving
the refactor is inert.

### Step 1 — Characterise, then hoist `_resolve_values` (no behaviour change)

`_resolve_values(strict=False)` returns `(success, resolved, errors)` and is
currently called inside `_collect_environment` (L185), after the stage rows are
built (L132-142).

| Change                              | Detail                                                           |
| ----------------------------------- | ---------------------------------------------------------------- |
| new `test_commands_deploy_show.py`  | characterisation tests for today's output shape                  |
| `_collect()`                        | call `_resolve_values(strict=False)` once, before the stage loop |
| `_collect_environment(env_service)` | take `resolved` as a parameter instead of resolving internally   |

**Harness:** construct the command with
`patch.object(BaseDeployCommand, "_initialize", return_value=None)`, then set
`_deployment_service`/`_configuration_service` to mocks — the same shape as
`_make_run_command` in `test_commands_deploy.py`. Named here because it is not
obvious and there is no existing `show` test module to copy from.

**Exit criteria:** the characterisation tests pass **unchanged** before and after
the hoist, and a test asserts `_resolve_values` is called **exactly once** — the
whole reason for hoisting rather than calling it twice is that a second call
re-reads the secret stores (`_resolve_values` caches variables and features, but
never secrets — those are always resolved live).

### Step 2 — Gap A: disclose gating

| Change                        | Detail                                                                     |
| ----------------------------- | -------------------------------------------------------------------------- |
| stage row (L134-142)          | `enabled` (raw), `would_skip` (bool), `skip_reason` (str or None)          |
| `_print_environment_detail()` | marker on a gated stage line                                               |
| reuse                         | `evaluate_enabled(stage, resolved)` — same call `build plan` already makes |

**Exit criteria:** a disabled stage is listed *and* marked; an enabled one is
unmarked. The marker names the flag and its resolved value, not just "disabled" —
a preview that says only "skipped" reproduces the problem one level down.

Changelog: one line.

### Step 3 — Gap B: unify `--stage`, add `--scope`

| Change                   | Detail                                                                     |
| ------------------------ | -------------------------------------------------------------------------- |
| `cli_deploy.py` L334     | standard `--stage` help wording; mention the secrets consequence           |
| `cli_deploy.py`          | add `--scope`                                                              |
| `__init__`               | accept `scope`; set `self._scope`                                          |
| `_collect()`             | select via `self._resolve_stages(StageSelectionMode.INSPECT)`              |
| `_collect_environment()` | scope `secret_rows` via `allowed_secret_keys_for_stages()`; add `in_scope` |
| unknown stage name       | handled by the shared helper — errors naming it and listing available      |

**Exit criteria:** `--stage X` scopes both the stage list and the secrets; a
**disabled** `X` is still shown (marked), because `show` previews rather than
gates; omitting both flags reproduces today's full output byte-for-byte.

**Risk to watch:** `INSPECT` is load-bearing. `DEPLOY` mode would filter disabled
stages out and silently undo Step 2 — the marker would have nothing to mark. The
"disabled `--stage` is still shown" test is what catches that.

Changelog: one line, noting `--stage` now does something it previously ignored.

### Step 4 — CLI wiring tests and doc alignment

Steps 1–3 are unit-level against `_collect()`/`_collect_environment()`. This step
closes the loop with the CLI and fixes the docs that currently describe behaviour
which never existed.

**Scope limit, deliberately.** The established pattern for deploy commands
([`test_commands_deploy.py` L89](../tests/strata/commands/test_commands_deploy.py))
invokes `CliRunner` with `execute` **patched out** — it proves flags parse and
reach the command, not what the command produces. Driving `deploy show`
end-to-end for real would need a full workspace, profile and environment fixture,
which is very likely *why* no test module exists for it today. So:

- **flag wiring** is tested at the CLI level (patch `execute`, assert the flag
  reaches the constructor, assert exit codes)
- **output content** — JSON keys, markers, scoping — stays at the `_collect()`
  level, where it can actually be asserted

The loop still closes; it just closes at two levels rather than one. Claiming
CLI-level coverage of the JSON payload would be a promise this repo's test
infrastructure cannot currently keep.

| Change                                                                | Detail                                                                                   |
| --------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `test_commands_deploy_show.py`                                        | `CliRunner` wiring tests for `--stage`/`--scope` (`execute` patched)                     |
| same module                                                           | `_collect()`-level assertions for the new JSON keys                                      |
| [commands.md L1534](platform/commands.md)                             | replace the "Filter secrets visibility…" help; document `--scope`; add the new JSON keys |
| [ADR-0020 L551](decisions/0020-cli-parameter-consistency-standard.md) | add `--stage`/`--scope` to the `deploy show` spec so the standard matches reality        |
| `cli_deploy.py`                                                       | help text matches the docs                                                               |

**Exit criteria:** every flag documented in `commands.md` is exercised by a wiring
test, and every documented JSON key by a `_collect()` test.

A separate step rather than a tail on Step 3 because the doc bug is the lesson:
`--stage` was *documented as working* long enough that nobody noticed it did not.
Aligning `commands.md` and ADR-0020 with the code is the part that stops it
recurring.

### Decision point

Resolved — see [Decided: list out-of-scope secrets](#decided-list-out-of-scope-secrets-do-not-omit-them)
above. Nothing else needs settling before implementation starts.

## Tests

Landing with the step that introduces each behaviour:

**Step 1 — baseline**

- characterisation: stage rows, remotes, and environment detail keep their shape
- `_resolve_values` called exactly once
- no workspace service is loaded (ADR-0026 constraint still holds)

**Step 2 — disclosure**

- disabled stage gains `would_skip`/`skip_reason`; enabled stage does not
- marker names the flag and its resolved value
- a literal `enabled: false` is disclosed as well as an expression

**Step 3 — selection**

- `--stage` scopes the stage list *and* the secrets to that stage
- `--stage` naming a **disabled** stage still shows it, marked
- out-of-scope secret is listed with `in_scope: false` and `value: null`, not omitted
- `--stage` with `secrets: ['*']` shows all secrets in scope
- `--scope` behaves as it does on `run`
- neither flag → every stage, every declared secret (today's behaviour)
- unknown `--stage` errors rather than silently showing everything

**Step 4 — wiring and docs**

- `CliRunner` with `--stage`, `--scope`, and neither — `execute` patched; asserts
  the flag reaches the constructor and the exit code is right
- `_collect()`-level: the JSON payload carries `would_skip`/`skip_reason`/`in_scope`
- unknown `--stage` exits non-zero
- every flag documented in `commands.md` has a wiring test; every documented JSON
  key has a `_collect()` test

