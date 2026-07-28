# Session Log — Secret post_generate/derive Design Discussion

**Date:** 2026-07-28T14:00:00Z
**Session type:** Rubber-duck / architecture discussion (no code changes)
**Agents:** Danny, Basher, Linus
**Requested by:** Vincent Huybrechts

## Summary

User raised a feature request: derive a secret from a previously-generated secret via some kind of transform (e.g. generate a random secret with `secret put --generate`, then derive a second value from it — a hash, a truncation, a formatted variant — without hand-copying plaintext). Danny, Basher, and Linus each assessed the request from their own angle in parallel, background mode. No files were modified and no direction was finalized — this was a pure discussion session.

## Options Considered

- **Option A — `post_generate` hook:** Run a script/subprocess immediately after `secret put --generate` to transform the freshly generated value. **Discouraged** by Danny (unsafe in unattended build/deploy paths — arbitrary subprocess execution at secret-generation time) and Linus (duplicated logic across ~3 call sites, ~15-18 tests needed, new attack surface with no offsetting benefit over Option C).
- **Option C — `derive:` spec on the secret store model:** A declarative field describing a transform of another resolved secret (e.g. `derive: { from: other_key, transform: sha256 }`), resolved through the existing build-time secret resolution path. Linus estimates ~1 new field on `SecretStoreModel`, 1 new resolution branch, cycle detection, ~9-11 new tests. Danny's recommended **fallback if the pattern recurs**.
- **Option D — documented CLI recipe:** No new code. Combine existing commands: `secret put --generate` (generate), `secret get --unmask` (read plaintext), `secret put --value <derived>` (store the derived value). Basher confirmed this is fully achievable today with zero core changes. Danny's **recommended direction now** — narrow use case does not yet justify new core surface area.

## Current Lean

- **Now:** Ship Option D as a documented recipe (docs-only, no code).
- **Later, if the pattern recurs:** Build Option C (`derive:` spec) as the durable, declarative solution.
- **Never (unless requirements change materially):** Option A — all three agents independently flagged it as the least safe path.
- **No decision was finalized.** The user has not yet chosen a direction; this remains open for a future session.

## Independent Finding — Audit Log Redaction Gap (flagged, not yet actioned)

While assessing Option D's execution mechanics, Basher discovered a **pre-existing, option-independent issue**: `src/strata/commands/base_command.py` (~line 565-570) emits an audit log entry for every CLI command invocation via:

```python
audit(
    f"command.{self.OPERATION}",
    outcome="success" if success else "failure",
    target=" ".join(sys.argv[1:]) if len(sys.argv) > 1 else self.OPERATION,
    ...
)
```

This logs the full, unredacted `argv`. Any invocation of `strata secret put KEY --value <plaintext>` today writes the plaintext secret value into `.strata/deploy-log/*.json`, and that plaintext could be forwarded further via `strata audit resend`. This is **not caused by** the secret-transform feature under discussion — it affects the existing `--value` flag today, independent of whether Option A, C, or D is ever built.

This finding has been filed as an open item in `.squad/decisions/inbox/basher-audit-log-redaction-gap.md` and merged into `.squad/decisions.md` as a flagged/open finding (not a finalized decision, since no fix has been actioned yet).

## Files Touched

| Area                  | Modified / New                                                                       |
| --------------------- | ------------------------------------------------------------------------------------ |
| Orchestration log     | `.squad/orchestration-log/2026-07-28T140000Z-danny.md` (new)                         |
| Orchestration log     | `.squad/orchestration-log/2026-07-28T140000Z-basher.md` (new)                        |
| Orchestration log     | `.squad/orchestration-log/2026-07-28T140000Z-linus.md` (new)                         |
| Session log           | `.squad/log/2026-07-28T140000Z-secret-post-generate-hook-design-discussion.md` (new) |
| Inbox                 | `.squad/decisions/inbox/basher-audit-log-redaction-gap.md` (filed and merged)        |
| Squad decision ledger | `.squad/decisions.md` (appended — flagged open finding)                              |
| Agent history         | `.squad/agents/danny/history.md` (appended)                                          |
| Agent history         | `.squad/agents/basher/history.md` (appended)                                         |
| Agent history         | `.squad/agents/linus/history.md` (appended)                                          |
