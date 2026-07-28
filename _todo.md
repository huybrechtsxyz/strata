# strata — Fix-it Backlog

Concrete, actionable fixes surfaced while working through `_lesson.md`. Plain
checkboxes, no ADR needed for these — small, mechanical, low-risk changes.
Work through `_lesson.md` first; come back and knock these out afterward.

- [ ] **Generate "valid kinds" lists from `PlatformKind` instead of hand-copying them.**
  (from `_lesson.md` C4) Four docs each hand-maintain their own copy of the
  `kind:` catalog and three of them are wrong: `docs/platform/commands.md` is
  missing `tenant` and wrongly includes internal-only `platform_model`;
  `.squad/templates/platform.instructions.md` lists a `datacenter` kind that
  doesn't exist anywhere in the codebase; `docs/GLOSSARY.md` lists a `workflow`
  kind that doesn't exist either (it's unbuilt ADR-0049). Only
  `.github/copilot-instructions.md` and `.github/instructions/strata.instructions.md`
  match reality. Fix: derive these lists from `PlatformKind`
  (`src/strata/models/common_models.py`) at doc-build time (or at minimum, a
  script/check that fails CI if a doc's hand-typed kind list drifts from the
  enum) instead of re-typing them by hand in N places.

- [ ] **Document the three audit/status delivery mechanisms and when to use which.**
  (from `_lesson.md` D1) Strata has three separate, undocumented-as-a-set
  mechanisms for getting deployment audit/status data out: (1) the local
  deploy-log (`.strata/deploy-log/`) — the full record, but only queryable by
  `strata audit list`/`audit diff`/`deploy history` on the machine that produced
  it; (2) SIEM/webhook forwarding (`forward_to_siem()`) — write-only broadcast
  to external systems, whatever fields you chose to forward, never read back by
  strata itself; (3) the gitops deployment manifest (`manifest: { type: gitops,
  push_manifest: true }`) — only carries `spec.status`, but is the one
  mechanism actually designed to be pulled and read from another machine.
  None of the three gives you the *full* audit record from a different
  machine — that's a real, if narrow, gap. Fix (docs only, no code needed
  unless someone actually hits the full-detail-cross-machine case): add a
  short section — likely in `docs/decisions/0018-deployment-audit-traceability.md`
  or a guide page — laying out plainly which of the three to reach for and
  why, so the split reads as intentional rather than confusing.
