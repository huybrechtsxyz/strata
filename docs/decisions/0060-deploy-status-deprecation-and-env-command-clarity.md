# Deploy status deprecation and env command clarity

- Status: **superseded by [ADR-0062](0062-cli-consolidation-env-dissolves-into-deploy-rollout-sln.md)**
- Date: 2026-07-28

## Summary

This ADR proposed a minimal fix to CLI confusion around overlapping `deploy status` /
`env output` / `env status` / `env show` commands: hide `deploy status` from help text and
add clarifying documentation to distinguish the three `env` commands by their actual purpose.

One day after the decision was finalized, evidence emerged that the overlap was broader
than initially scoped — `deploy drift` and `env drift` are also duplicates, and the root
cause is that the `env` group itself lacks a coherent, distinct ownership. Rather than
continue patching symptoms, [ADR-0062](0062-cli-consolidation-env-dissolves-into-deploy-rollout-sln.md) 
supersedes this decision with a comprehensive consolidation: the `env` group dissolves entirely, 
with its six commands distributed to their natural homes in `deploy`, `rollout`, and `sln`.

See [ADR-0062](0062-cli-consolidation-env-dissolves-into-deploy-rollout-sln.md) for the new, complete design.

## Original Proposed Solution (now superseded)

This ADR originally proposed **Option 3**: hide `deploy status` from `--help` with
`hidden=True` on its Click registration, fix stale doc cross-references, and add a
short clarifying note to distinguish `env output` / `env status` / `env show` by
their actual purpose.

That approach was rejected one day later in favor of the comprehensive consolidation
now recorded in ADR-0062.


## Consequences (Historical Record)

The original Option 3 proposal would have had these consequences (now superseded by
ADR-0062):

- Good: a new user browsing `strata deploy --help` would no longer see the deprecated
  command presented as equally valid alongside its replacements.
- Good: no breaking change — existing scripts/pipelines calling `strata deploy
  status` directly by name would continue to work.
- Good: stale doc pointers would get fixed in the same pass.
- Good: `env output` / `env status` / `env show` would keep their existing behavior
  — no renames, no migration required.
- Neutral: the "two different replacement targets" split in deprecation messages
  would remain.
- Bad (accepted): the command would remain in the codebase until a future major
  version — to preserve a grace period.


## References

- [ADR-0062: CLI consolidation — `env` dissolves into `deploy`, `rollout`, and `sln`](0062-cli-consolidation-env-dissolves-into-deploy-rollout-sln.md) —
  the superseding decision, addressing the broader overlap that emerged one day
  after this ADR was finalized.
- [ADR-0058: Cross-deployment dependency gating](0058-cross-deployment-dependency-gating.md) —
  confirmed `strata env status` as the most reliable cross-machine "is it deployed"
  signal, which ADR-0062 preserves as `deploy status` with corrected behavior.
