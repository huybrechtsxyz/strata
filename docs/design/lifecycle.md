# Lifecycle Hooks — Design

- Status: draft — nothing implemented. v2 models lifecycle on seven kinds
  and executes it nowhere. This doc captures the current state, the real v1
  mechanism it would be ported from, and the open questions that must be
  answered first; it does **not** propose a design yet.
- Last updated: 2026-09-25

## Overview

A *lifecycle hook* is a user-supplied script strata runs at a named point in
a command's execution — v1's `config_fetch_before`, `build_run_after`,
`deploy_apply_before`, and so on. v1 uses them heavily; v2 has the schema for
them on seven kinds but no executor at all.

This doc exists because the question surfaced from the wrong direction. A
v1-vs-v2 `build run` comparison filed "build lifecycle hooks" as a
[build-command.md](build-command.md) parity gap (item 5), but checking it
against real code showed it is not a `build run` concern: v1 fires hooks from
roughly 30 sites across a dozen commands, and v2's gap is that **nothing,
anywhere, in any command, executes a lifecycle phase**. Adding a call in
`build_run()` would have nothing to read from and no executor to call it.
That makes this a cross-cutting subsystem needing its own design pass, not an
item to slip into build work.

## Current Design

### What v2 models today

Seven kinds carry a lifecycle field. All verified present in `models/`:

| Kind | Field | Model |
| --- | --- | --- |
| `deployment` | `spec.lifecycle` | `CommonLifecycleModel` |
| `workspace` | `spec.lifecycle` | `CommonLifecycleModel` |
| `namespace` | `spec.lifecycle` | `CommonLifecycleModel` |
| `module` | `spec.lifecycle` | `CommonLifecycleModel` |
| `resource` | `spec.lifecycle` | `CommonLifecycleModel` |
| `provider` | `spec.lifecycle` | `CommonLifecycleModel` |
| `integration` | `spec.lifecycle` | `IntegrationLifecycleModel` (ADR-0021 D11, `.py`-only) |

Plus `DeploymentStageModel.scripts: ScriptsModel` — a bare script list, not a
phase map, correctly so: a stage *is* already a phase, so there is nothing to
key by (ADR-0021 D11's own reasoning).

The shapes (`common_models.py`):

```python
class CommonLifecycleModel(RootModel[dict[str, CommonLifecyclePhaseModel]]):
    """Open map: phase name -> phase config. Any phase name is allowed."""

class CommonLifecyclePhaseModel(ScriptsModel):
    """description + scripts"""

class ScriptsModel(PlatformBaseModel):
    description: str | None
    scripts: list[str | ScriptPathModel] | None

class ScriptPathModel(PlatformBaseModel):
    file: str                    # validated: solution-relative, allowed extension
    scope: PlatformKind          # "how many times this runs"
    priority: int = 100          # 0-9999, lower first
    target: str | None           # glob filter, e.g. 'vm-*', 'azure-*'
    description: str | None
```

`validate_script_file()` enforces two things at load time: the path must not
escape the solution (`validate_relative_path()` — scripts are *executed*, so
they get the same containment rule as any other declared path), and it must
carry one of the seven `SCRIPT_EXTENSIONS` (`.sh`, `.bash`, `.py`, `.ps1`,
`.js`, `.mjs`, `.go`). Filesystem existence is deliberately not checked —
the file may live in a remote not yet materialised.

### What v2 does with it

**Nothing.** Grepping `controllers/`, `commands/`, `integrations/` and
`services/` for `lifecycle` returns exactly one hit, an unrelated docstring
in `commands/run.py` about the *command* lifecycle. Every field above is
inert: it validates, it round-trips, and no code path ever reads it.

Two consequences worth stating plainly:

- `ScriptPathModel.scope`/`priority`/`target` are modelled but have **no
  defined semantics** — nothing interprets them, and no ADR says what they
  should mean. They were ported from v1's shape, not from a decision.
- `ConfigurationSpecModel` has **no** `lifecycle` field, unlike v1. This
  matters more than it looks: v1's *build* hooks come from
  `Configuration.spec.lifecycle` specifically, so the v1 behaviour the parity
  review noticed has no v2 schema home at all.

### What v1 actually does (the thing a port would port)

`controllers/lifecycle_controller.py`, three entry points:

- `execute_configuration_phase(phase_name, work_path, context)` — reads
  `Configuration.spec.lifecycle` via
  `ConfigurationService.get_lifecycle_phase()`. This is what
  `BaseCommand._run_lifecycle_phase()` wraps, and therefore what every
  build/solution command uses.
- `execute_workspace_phase(base_service, phase_name, ..., add_config_model=False)`
  — reads the lifecycle off *any* service's model (workspace, namespace, …).
  Used by the deploy commands.
- `execute_phase(...)` — the shared core both delegate to.

Behaviour, verified:

- **A missing phase is a silent success.** No hooks defined for the phase
  returns `True` and logs at debug. Hooks are purely additive.
- **A failing hook aborts the command.** Every call site checks the return
  and bails with an error message.
- **Context is passed as `XYZ_*` environment variables.**
- **Interpreter dispatch is real and complete** (`_build_command()`):
  `.sh`/`.bash` → `bash` (Git bash on Windows, direct execution elsewhere);
  `.ps1` → `powershell -ExecutionPolicy Bypass -File`; `.bat`/`.cmd` →
  direct; `.py` → `sys.executable`; anything else → direct invocation,
  relying on a shebang or OS association.
- **Hierarchy is handled by an ad-hoc boolean.** `execute_workspace_phase()`
  takes `add_config_model: bool = False` — "if True, merge
  configuration-level phase scripts in", decided per call site. This is v1's
  entire answer to precedence, and it is exactly what schema-parity Issue 5
  flags as undocumented.

Scale: ~30 invocation sites across ~12 commands — `build run`/`plan`/`clean`,
`deploy run`/`destroy`/`drift`/`health`, `sln init`/`update`/`clean`.

### Correction to ADR-0021 D11's characterisation of v1

D11 states that *"nothing anywhere dispatches a script to an interpreter:
v1's only executor hardcodes `run_command(["python", str(script_path)], ...)`
in `terraform_builder.py`"*, and restricts `Integration` hooks to `.py` on
that basis.

The premise is inaccurate about v1: `LifecycleController._build_command()` is
a complete extension→interpreter map (quoted above), and it is v1's *actual*
lifecycle executor — `terraform_builder.py` is a separate, narrower call
site. D11's **conclusion still holds for v2** (v2 genuinely has no dispatch,
so accepting a `.ps1` hook v2 cannot run would be the worst outcome), but the
implied cost of widening is wrong: the map is ~20 lines of existing, working
v1 code to port, not new design. Worth revisiting D11's restriction once an
executor exists — via a new ADR, per `docs/decisions/README.md`'s
no-revision-in-place rule.

## Related Decisions

- [ADR-0021](../decisions/0021-integration-layer.md) D11 — `Integration.lifecycle`,
  `.py`-only restriction, "scripts are referenced, never discovered", and the
  note that an extension→interpreter map belongs beside `run_command()` in
  `transport.py`. The only real lifecycle design work done in v2 so far.
- [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) — defers
  `Configuration.spec.policies` (the *other* half of the original parity-gap
  item) with a stated "port alongside the command that needs it" convention.
- [build-command.md](build-command.md) — parity gap 5, where this was first
  filed and from which it was re-homed here.
- [v1-schema-parity-tracking.md](v1-schema-parity-tracking.md) —
  Architectural Issue 5 (lifecycle hierarchy precedence), still open, no ADR.

## Remaining Work / Open Questions

Ordered roughly as they'd need answering. The first one is a gate on all the
others.

1. **Does v2 want script-based lifecycle hooks at all?** Not rhetorical. They
   are an arbitrary-code-execution surface by construction, and no
   consumer-usage evidence has been gathered for them the way
   [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) did for
   commands. Check what haven and `cfg-int-deployment` actually declare
   before designing anything. A "no, or only for phase X" answer is a
   legitimate outcome and would close most of what follows.
2. **Hierarchy and precedence (schema-parity Issue 5).** Seven kinds can each
   declare the same phase name. Which run? In what order? Does a more
   specific scope override or append to a broader one? v1's answer is a
   per-call-site `add_config_model` boolean, which is not a design. This
   needs a decision before any executor is written, because the executor's
   shape depends on it.
3. **Does `Configuration` get a `lifecycle` field?** v1's build/solution hooks
   live there; v2 doesn't model it. Adding it is a schema change that should
   follow ADR-0020's "port alongside the feature that needs it" convention,
   so it is downstream of (1).
4. **What do `scope`/`priority`/`target` mean?** Modelled, never interpreted,
   no ADR. Either give them semantics or drop them — leaving executable
   fields with undefined behaviour in the schema is the worst option.
5. **Phase vocabulary: open map or closed set?** `CommonLifecycleModel`'s
   docstring says open ("each kind and provisioner type defines its own set")
   — which means a typo'd phase name is silently never fired. A per-command
   known-phase list would make that an authoring-time error instead.
6. **Interpreter dispatch.** Port v1's `_build_command()` map into
   `transport.py` beside `run_command()` (ADR-0021 D11 already nominates that
   location). Note v1's `.ps1` branch passes `-ExecutionPolicy Bypass`,
   which deliberately disables PowerShell's execution policy — worth an
   explicit decision rather than a silent port.
7. **Failure and dry-run semantics.** v1: missing phase = success, failing
   hook = abort the command. Both look right; they should be stated rather
   than inherited. Separately: what should a hook do under a future
   `build run --dry-run`?
8. **Context contract.** v1 passes context as `XYZ_*` env vars. What is
   guaranteed to a hook, and is it a stable interface? Secrets must be
   explicitly in or out (cf. [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md),
   which keeps resolved secrets out of build artifacts for the same reason).
9. **Relationship to policies.** v1's `phase: build` policies are a separate
   mechanism (`PolicyEngine`, deny/warn/audit) that happens to fire adjacent
   to hooks. Deferred by ADR-0020; not this doc's scope, but the two should
   be designed knowing about each other.

## Changelog

- 2026-09-25: Created. Split out of [build-command.md](build-command.md)'s
  parity gap 5 after verifying that the item was mis-scoped: lifecycle
  execution is missing across all of v2, not just `build run`, and the
  policies half was already covered by ADR-0020. Records v2's current
  (entirely inert) lifecycle schema, v1's real `LifecycleController`
  mechanism, a correction to ADR-0021 D11's characterisation of v1's
  interpreter dispatch, and nine open questions gating any implementation.
  No code written — this is a capture, not a design.
