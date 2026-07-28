# Session Log — Global Good/Meh/Ugly Review: Kickoff (Pass 1)

**Date:** 2026-07-28
**Who worked:** Danny (Lead/Architect), Basher (DevOps Integrations), Linus (Python/CLI Dev), Livingston (Tester/QA), Reuben (Docs/Technical Writer)

## What happened

Vincent kicked off a full top-to-bottom good/meh/ugly audit of strata — concept,
design, implementation, and usability — the same shape as a from-scratch redesign
review he'd previously run on another project. Danny, Basher, Linus, Livingston,
and Reuben each scanned their own domain in parallel (background mode) for
high-level items "worth reviewing."

The coordinator compiled all five domain lists into a single new root-level
tracking file, `_lesson.md`: 23 items across 5 categories (Concept, Design,
Implementation, Testing & Quality, Docs & Usability), plus a short "already
fixed / notable this session" context section.

This round is **pass 1 (collection) only** — every item is tagged ⏳ (not yet
reviewed). No verdicts (🟢 Good / 🟡 Meh / 🔴 Ugly) have been assigned. A follow-up
pass will go through `_lesson.md` row by row and fill in the Verdict column
in place.

## Outcome

- `_lesson.md` created at the repo root with 23 collected items across 5
  categories, all pending review.
- Five orchestration-log entries written (one per agent) in
  `.squad/orchestration-log/`.
- Each contributing agent's `history.md` updated with a short learnings note.

## Decisions

None — this is a working tracker file, not a decision. Nothing added to
`.squad/decisions.md` this round.

## Next steps

Pass 2: review each row in `_lesson.md` and fill in Verdict + a one-line
reason, updating the file in place.

## C1/C2 reviewed

- **C1 verdict: 🟢** — The "27 of 50 ADRs (54%) still Proposed" stat was itself based on
  a stale index. Re-checked against the actual ADR files directly: real tally is
  ~70% completed/implemented, ~20% still proposed, with a small partial/deferred
  remainder. `docs/decisions/README.md`'s hand-maintained index table (stale since
  ADR 0048, with wrong statuses baked in) was the source of the bad figure and has
  been removed as a result, replaced by a pointer to the directory listing and each
  file's own `- Status:` line.
- **C2 verdict: 🟡** — Confirmed three overlapping concepts all called "approval":
  `spec.approvals` (audit-only metadata, no enforcement), ADR-0032 (proposed,
  unbuilt approval workflows), and ADR-0057 (implemented gates/`WorkItem`
  framework, of which `type: approval` is one gate type). Architecturally coherent
  (no double-blocking found), but a real naming/clarity gap. `docs/decisions/0059-approval-metadata-and-gate-streamlining.md`
  filed (Status: proposed) to record the decision: ship a docs-only clarification
  now, reject outright removal/rename of `spec.approvals` (real usage confirmed),
  flag an opt-in `enforce:` bridge field as the future streamlining direction, not
  yet scheduled.

## C4/C5/C6 reviewed

- **C4 verdict: 🟡** — The code itself is fine (`PlatformKind` enum in
  `common_models.py` is the single source of truth, 17 kinds cleanly organized),
  but the docs have sprawled: four separate hand-maintained "valid kinds" lists
  found, three of them wrong (`docs/platform/commands.md` missing `tenant` and
  wrongly including internal-only `platform_model`; `.squad/templates/platform.instructions.md`
  listing a nonexistent `datacenter` kind; `docs/GLOSSARY.md` listing an unbuilt
  `workflow` kind from ADR-0049 as if it existed). Added to `_todo.md` as a
  mechanical fix: generate these lists from `PlatformKind` instead of hand-copying.
- **C5 verdict: 🟢** — Re-verified ADR-0044's gap-analysis table against current
  code, not just ADR titles. Dependency graph/parallel execution and drift
  detection gaps are still accurate: no parallel execution scheduler and no drift
  detection scheduler exist anywhere in the codebase today. The two flagged gaps
  remain the top two; no priority shift found.
- **C6 verdict: 🔴** — Worse than the original framing: not 2 overlapping "is it
  deployed" surfaces, but 4. `strata deploy status` is deprecated with deprecation
  messages that themselves split guidance across two different replacements
  (`env output` vs. `deploy plan` depending on `--plan`), and it has no
  `hidden=True` on its Click registration — fully visible in `--help` competing
  with `env output`, `env status`, and `env show`. Filed
  `docs/decisions/0060-deploy-status-deprecation-and-env-command-clarity.md`
  (Status: proposed) recommending `hidden=True` now (no functional removal),
  fixing stale doc cross-references (already tracked as I7), and adding a doc
  clarity note distinguishing the three real `env` commands. Full removal
  deferred to a future breaking-change release. Consolidating the three `env`
  commands into a single mega-command explicitly rejected.

## D1/D2 reviewed

- **D1 verdict: 🟡** — `deploy_log_path` genuinely has no remote-backend support
  (confirmed: `ConfigurationService.get_deploy_log_path()` is pure filesystem
  path resolution, no S3/Blob/GCS client anywhere in that path), but the real
  needs it would serve are already covered elsewhere: SIEM/webhook forwarding
  (`forward_to_siem()`) already gets the same audit event off-machine, and the
  gitops deployment manifest (`push_manifest: true`) already covers
  cross-machine "did this deployment succeed" checks. Real but low-priority
  gap — not worth a code fix. Doc-only follow-up added to `_todo.md`
  (document the three audit/status delivery mechanisms and when to use which).
- **D2 verdict: 🔴** — Worse than the original framing: not 3 but **4**
  independent subprocess execution implementations (`run_command()`'s buffered
  path, `run_command()`'s streaming path, `script_deployer.py`'s direct
  `subprocess.run`, and `terraform_builder.py`'s `format=script` builder).
  Confirmed the `format=script` builder has **no `timeout` parameter at
  all** — a live hang risk for `strata build run` today. Also confirmed
  `run_command()`'s own default/buffered path (the common case) lacks the
  `register_process`/`deregister_process` SIGTERM registration that only its
  less-used streaming path has, meaning ADR-0028's graceful-shutdown coverage
  is narrower than advertised. Filed
  `docs/decisions/0061-subprocess-execution-consolidation.md` (Status:
  proposed): fix `run_command()`'s buffered path first (Popen +
  registration), then migrate the other two call sites onto it, with the
  `format=script` timeout fix shippable independently and immediately as the
  most urgent piece.
