# Deploy status deprecation and env command clarity

- Status: proposed
- Date: 2026-07-28

## Context and Problem Statement

During the global project review (`_lesson.md`, item C6), strata's "what's happening
with my deployment" CLI surface was found to have drifted into **four** overlapping/
confusing commands — not the two originally suspected:

1. `strata deploy status` (`status_deploy_command.py`) — deprecated, but its OWN
   deprecation warnings point to **two different replacements** depending on flags:
   default (live outputs) mode says *"Use `strata env output -f FILE` instead"*;
   `--plan` mode says *"Use `strata deploy plan -f FILE` instead."* Neither message
   mentions `env status`.
2. `strata env output` (`cli_env.py`, `OutputEnvCommand`) — "Show live Terraform
   outputs for a deployment."
3. `strata env status` (`cli_env.py`, `StatusEnvCommand`) — "Show the live
   infrastructure status for a deployment" — queries the real Terraform backend
   directly (confirmed during the cross-deployment-dependency-gating discussion,
   [ADR-0058](0058-cross-deployment-dependency-gating.md), as the most reliable
   cross-machine "is it deployed" signal).
4. `strata env show` (`cli_env.py`, `ShowEnvCommand`) — "Show the full resolved
   environment for a deployment" — a different concern entirely (resolved
   variables/secrets/overrides, not infrastructure state).

Additionally confirmed: **`strata deploy status`'s Click registration has no
`hidden=True`** — it is fully visible in `strata deploy --help` today, competing
equally for a new user's attention against the commands it's deprecated in favor of.
Also note (already flagged separately as `_lesson.md` item I7): stale doc
cross-references near `output_deploy_command.py` still point users toward
`deploy status` as a valid companion to `deploy run`.

This ADR records the recommended direction only. It does not implement anything.

## Decision Drivers

- Don't break existing scripts/pipelines that still call `strata deploy status` —
  needs a grace period, not an immediate hard removal.
- New users should not be able to stumble onto a deprecated command with equal
  visibility to its replacements.
- `env output` / `env status` / `env show` answer three genuinely different
  questions (raw outputs / live infra health / resolved config) — the fix is NOT to
  merge them into one mega-command, it's to make each one's distinct purpose
  unmistakable and stop `deploy status` from competing with all three
  simultaneously.
- Minimize churn — prefer additive/hiding changes over renames that would break more
  scripts.

## Considered Options

### Option 1 — Do nothing

- Con: real, demonstrated UX confusion (deprecated command as visible as its
  replacements, deprecation message splitting across two different targets, three
  similarly-named `env` commands with no doc distinguishing them).

**Rejected.**

### Option 2 — Remove `strata deploy status` entirely, immediately

- Con: breaking change with no grace period; scripts/pipelines still calling it
  would hard-fail with no warning period.

**Rejected.**

### Option 3 — Hide `deploy status` from `--help` now, remove it in a future major version (RECOMMENDED)

Add `hidden=True` to `deploy status`'s Click registration now, keep it functional
with its existing deprecation warnings, and remove it entirely in a future major
version bump (the next version after 1.5.0 that includes breaking changes).
Combine with fixing the stale doc cross-references (I7) and adding a short doc note
distinguishing `env output` / `env status` / `env show` by their actual purpose (raw
outputs vs. live health vs. resolved config), so the three real commands stop
sounding interchangeable.

- Pro: zero breaking change today — existing scripts keep working, deprecation
  warnings still fire.
- Pro: immediately stops the deprecated command from competing visually with its
  replacements in `--help`.
- Pro: cheap — a single decorator argument, plus doc fixes already tracked (I7) or
  trivial to add.
- Con: the command still exists in the codebase until the future removal — some
  ongoing (small) maintenance burden persists until Phase 3.

**This is the winning option.**

### Option 4 — Consolidate `env output`/`env status`/`env show` into a single command with mode flags

E.g. `env status --outputs`, `env status --resolved`.

- Con: bigger breaking change — renames real, non-deprecated commands — for a
  marginal UX gain over Option 3's cheaper fix (just clarifying docs/help text).

**Rejected for now.** Revisit only if Option 3 alone proves insufficient in
practice — recorded here so it isn't re-proposed without new evidence.

## Decision Outcome

Ship **Option 3**. `hidden=True` on `deploy status`'s Click command registration is
the concrete, low-risk, immediate action. Fixing stale doc pointers (already tracked
as I7) and adding the three-command clarity note are bundled into the same pass
since they're cheap and directly related. Option 4 is explicitly rejected/deferred —
recorded so it isn't re-proposed without new evidence that Option 3 wasn't enough.

### Consequences

- Good: a new user browsing `strata deploy --help` no longer sees the deprecated
  command presented as equally valid alongside its replacements.
- Good: no breaking change — existing scripts/pipelines calling `strata deploy
  status` directly by name continue to work, including their existing deprecation
  warnings.
- Good: stale doc pointers (I7) get fixed as part of the same pass, rather than
  lingering as a separate untracked inconsistency.
- Good: `env output` / `env status` / `env show` each keep their existing, correct
  behavior — no renames, no migration required for users of those commands.
- Neutral: the underlying "two different replacement targets" split in `deploy
  status`'s own deprecation messages (`env output` vs. `deploy plan` depending on
  `--plan`) is not changed by this decision — it's accurate today, just not
  discoverable via `--help`.
- Bad (accepted): `strata deploy status` and `status_deploy_command.py` remain in
  the codebase until Phase 3's future breaking-change removal — this is deliberate,
  to preserve the grace period.

## Detailed Design

- Add `hidden=True` to the `@deploy_group.command(name="status", ...)` decorator in
  `cli_deploy.py` (or wherever `status_deploy_command`'s Click registration lives —
  check `cli_deploy.py`). The command still runs and still prints its existing
  deprecation warnings when invoked directly by name or by scripts — `hidden=True`
  only removes it from the `--help` listing, it does not remove functionality.
- Fix the stale `deploy status` cross-reference(s) found near
  `output_deploy_command.py` (I7) to point at the correct replacement commands
  (`env output` for live outputs, `deploy plan` for the `--plan` case).
- Add a short clarifying doc note (exact location TBD by whoever implements — likely
  a guide page or `docs/platform/commands.md`) distinguishing:
  - `env output` — raw Terraform output values.
  - `env status` — live infrastructure reachability/health (queries the real
    backend).
  - `env show` — resolved configuration (variables, secrets, overrides) — not
    infrastructure state at all.
- Removal timeline: `strata deploy status` should be fully removed in the next
  breaking-change/major version after `1.5.0` — this ADR intentionally does not pick
  or hardcode a specific target version number; the release process decides the
  actual version.

## Implementation Phases

### Phase 1 (small, low-risk, could ship soon)

- `hidden=True` on `deploy status`'s Click registration.
- Fix stale doc cross-references (I7). Concrete list, expanded during the
  `_lesson.md` review after grepping beyond the original single docstring:
  - `output_deploy_command.py` docstring (*"...or `deploy status`"*, no
    deprecation note)
  - `docs/help/deployment.md` (usage example recommending it as current)
  - `docs/guides/deploying.md` (same pattern)
  - `docs/platform/provisioner-plugin-api.md` (tells plugin authors to
    support it as if current)
  - (ADRs referencing it as historical design context are lower priority —
    they describe the command as it existed at the time, not live guidance)

### Phase 2 (docs)

- Add the `env output` / `env status` / `env show` clarity note.

### Phase 3 (future, breaking change, needs its own release-planning decision)

- Fully remove `strata deploy status` and its command file
  (`status_deploy_command.py`).

## References

- `_lesson.md`, item C6 — the finding that seeded this ADR (four overlapping
  "is it deployed" commands, not two; `deploy status` visible in `--help` with no
  `hidden=True`).
- `_lesson.md`, item I7 — the stale doc cross-reference near
  `output_deploy_command.py` that this ADR's Phase 1 fixes.
- [ADR-0020: CLI parameter consistency standard](0020-cli-parameter-consistency-standard.md) —
  related cross-cutting CLI consistency standard.
- [ADR-0058: Cross-deployment dependency gating](0058-cross-deployment-dependency-gating.md) —
  confirmed `strata env status` as the most reliable cross-machine "is it deployed"
  signal, informing this ADR's recommendation to keep `env status` distinct and
  well-documented rather than folding it into a consolidated command.
