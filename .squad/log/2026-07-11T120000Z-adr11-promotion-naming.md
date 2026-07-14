# Session Log — ADR-0011 Promotion Naming Gut-Check

**Date:** 2026-07-11T12:00:00Z
**Session type:** Naming / architecture review
**Agents:** Danny (architect)
**Requested by:** Vincent Huybrechts

## Summary

Danny performed a naming gut-check on ADR-0011 (promotion strategies for version progression). Verdict: keep "promotion" / `strata promote` — it is the dominant industry term across GitOps/CD tooling, reads cleanly as a command, aligns with the ring-based progression and version-lock mental model, and does not collide with existing strata nouns. The one change: drop `unpromote` as a CLI verb and standardize the reverse direction on `strata promote rollback`, which matches the vocabulary already used in the deploy surface. "Unpromotion" remains acceptable as descriptive ADR prose but must never appear as a command.

## Key Decisions

- Keep "promotion" as the concept name and `strata promote` as the command group (correct industry term, zero learning curve)
- Reverse direction CLI verb is `strata promote rollback`, not `strata promote unpromote`
- Reverse operations follow user-facing vocabulary, not linguistic symmetry with the forward verb
- Rejected alternatives: advance/advancement (generic), rollout (k8s collision), propagate (unfamiliar), release-progression (verbose, collides with ADR-0017 "release" tagging lifecycle)

## Files Touched

| Area          | Modified / New                                                 |
| ------------- | -------------------------------------------------------------- |
| Agent history | `.squad/agents/danny/history.md` (appended)                    |
| Decision      | `.squad/decisions/inbox/danny-adr11-promotion-naming.md` (new) |
