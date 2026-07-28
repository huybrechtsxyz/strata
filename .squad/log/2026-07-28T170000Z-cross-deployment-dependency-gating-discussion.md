# Session Log — Cross-Deployment Dependency Gating Discussion

**Date:** 2026-07-28T17:00:00Z
**Session type:** Rubber-duck / architecture discussion (no code changes)
**Agents:** Danny, Basher, Linus
**Requested by:** Vincent Huybrechts

## Summary

A partner team has a layered deployment hierarchy — tenant/landscape/zone/zone-tenant/zone-tenant-environment — where each layer is a separate `kind: deployment` file. They asked whether strata can gate a lower layer (e.g. zone) on an upper layer (e.g. landscape) having been successfully deployed first. Danny, Basher, and Linus each assessed the question from their own angle in parallel, background mode. No files were modified and no recipe was authored this round — this was a pure discussion session.

## Question

Can strata natively prevent a lower deployment layer from running unless an upper layer's deployment file has already succeeded, given that each layer is its own separate `kind: deployment` file (not stages within one file)?

## Assessments

- **Danny (Lead/Architect):** Confirmed no built-in mechanism exists today. The only dependency primitive is intra-file `stages[].depends_on`, which cannot reach across separate deployment files. The `spec.inputs.from` concept drafted in `docs/guides/at-scale.md` is unimplemented. Initially proposed extending ADR-0057's gate framework with a new `type: dependency` gate, but revised this after Linus pointed out the mismatch (see below) — concurred that a new `spec.requires` field is the better fit.
- **Basher (DevOps Integrations):** Verified a DIY lifecycle-script stopgap works today: hook at the `deploy_run_before` phase, before any stage touches infrastructure. Evaluated the three existing deploy-inspection commands as candidate signals: `strata deploy status` is deprecated/unreliable (just live terraform outputs, not a durable record); `strata deploy health` silently passes with `no_checks_defined` when no health checks are configured — a footgun for an unconfigured downstream layer. Recommended `strata deploy history`'s per-execution `success` boolean as the most reliable DIY signal, with the caveat that CI must persist/share `.strata/logs/` across ephemeral checkouts between the two layers' pipeline jobs.
- **Linus (Python/CLI Dev):** Found via direct code read that `DeploymentManifestModel.spec.status` (`success|partial|failed`, from ADR-0021 deployment manifests) is the authoritative, already-implemented state signal — cleaner than history/status/health because it's the manifest's own recorded outcome, not a derived CLI view. Recommended a new `spec.requires: Optional[List[str]]` field on `DeploymentModel` as a simple hard precondition check (no human approval, no `WorkItem`/exit-5 machinery), evaluated pre-flight in `deploy run` (and optionally `validate --deep`). Argued against Danny's gate-type approach: ADR-0057 gates are environment-scoped and built for human decisions — a mismatch for a deployment-file-layering concern that is a binary, disk-checkable precondition with no human decision involved.

## Final Synthesized Recommendation

No built-in mechanism exists today. Two-part recommendation given to the user:

1. **Immediate unblock:** Ship the lifecycle-script DIY recipe now, using `strata deploy history --output json`'s `success` field as the check signal (not `deploy status` or `deploy health`, both unreliable for this purpose per Basher's findings).
2. **Future first-class fix (flagged, not yet actioned):** A new `spec.requires: [<deployment-file>]` field on `DeploymentModel` (Linus's Approach 1), checked against `DeploymentManifestModel.spec.status` at deploy pre-flight — recommended if/when this becomes a recurring need across more teams. Explicitly **not** routed through the ADR-0057 gate/`WorkItem` framework, since there is no human decision involved.

## Scoped Out

The reverse-direction concern — preventing a zone from being destroyed while tenants still exist under it — was scoped out as a separate future concern. It depends on ADR-0038 Gap 3 (fleet-level visibility), which is not yet built.

## Files Touched

| Area                  | Modified / New                                                                         |
| --------------------- | -------------------------------------------------------------------------------------- |
| Orchestration log     | `.squad/orchestration-log/2026-07-28T170000Z-danny.md` (new)                           |
| Orchestration log     | `.squad/orchestration-log/2026-07-28T170000Z-basher.md` (new)                          |
| Orchestration log     | `.squad/orchestration-log/2026-07-28T170000Z-linus.md` (new)                           |
| Session log           | `.squad/log/2026-07-28T170000Z-cross-deployment-dependency-gating-discussion.md` (new) |
| Inbox                 | `.squad/decisions/inbox/danny-cross-deployment-dependency-gap.md` (filed and merged)   |
| Squad decision ledger | `.squad/decisions.md` (appended — flagged open finding)                                |
| Agent history         | `.squad/agents/danny/history.md` (appended)                                            |
| Agent history         | `.squad/agents/basher/history.md` (appended)                                           |
| Agent history         | `.squad/agents/linus/history.md` (appended)                                            |

## Outcome

Discussion only — no code or docs written this round. The lifecycle-script DIY recipe (using `deploy history`'s success field) has not yet been authored as a docs cookbook; the `spec.requires` field has not yet been implemented. Both remain open items for a future session, filed in `.squad/decisions/inbox/danny-cross-deployment-dependency-gap.md` and merged into `.squad/decisions.md`.

## Update — cross-machine verification

**Date:** 2026-07-28 (follow-up round)
**Agent:** Basher (background, follow-up verification)

The user asked whether pointing `deploy_log_path` at a shared/remote path is the only cross-machine-safe option for the dependency check discussed above. Basher verified in code:

1. `deploy_log_path` (`ConfigurationService.get_deploy_log_path`) is confirmed pure filesystem path resolution — no remote transport (no S3/Blob/GCS), unlike locks (ADR-0007) or work items (ADR-0057).
2. **New finding:** `ConfigurationManifestModel` already supports `manifest: { type: gitops, repository, branch, push_manifest: true }` — after every deploy, `push_to_remote()` does a real git commit+push of the deployment manifest (with `spec.status: success|partial|failed`) to a remote repo. This is genuinely remote today, no shared filesystem needed. Gap: no matching `pull_from_remote()` — a downstream consumer must `git pull` the state-repo itself first.
3. **Correction to this session log and the `decisions.md` inbox entry from this session:** "strata deploy status" does **not** exist as a registered command — that was a naming error carried over from stale docs. The actual live-Terraform-state-query command is `strata env status -f <file>` (`StatusEnvCommand`), which runs `terraform init` + `terraform show -json` against the upstream's real remote Terraform backend — inherently cross-machine since that's the point of a Terraform remote backend. Only `deploy lock status` exists under the `deploy` group.
4. **Ranked recommendation for cross-machine dependency checking (best to worst):** (1) `strata env status -f <upstream>` — queries live Terraform backend directly, no shared storage needed; (2) deployment manifest with `type: gitops` + `push_manifest: true` — genuinely remote via git, matches the earlier-proposed `spec.requires` design well, but needs an explicit `git pull` step downstream; (3) shared `deploy_log_path` on a mounted network share — works but zero strata-side remote mechanics, all on the operator; (4) webhook/ndjson audit sinks — not usable standalone, no query-side API today.

This updates/corrects the earlier flagged open finding (`danny-cross-deployment-dependency-gap`, already merged into `decisions.md`) — the recommended backing store for the future `spec.requires` field should now specifically call out the gitops-manifest mechanism (already implemented) as the preferred remote signal, not "the manifest" generically, and `env status` as a live-state fallback.

Details: `.squad/orchestration-log/2026-07-28T183000Z-basher.md`

## Outcome — ADR 0058 published

**Date:** 2026-07-28 (follow-up)
**Agent:** Danny

This discussion is now formally recorded as an ADR: [docs/decisions/0058-cross-deployment-dependency-gating.md](../../docs/decisions/0058-cross-deployment-dependency-gating.md)
(Status: Proposed). It documents the `spec.requires: Optional[List[str]]` field decision, backed
by the gitops-manifest status signal with `env status` as a live-state fallback, and records the
rejected alternatives (ADR-0057 gate extension, `inputs.from` overload). An index row for ADR
0058 has been added to `docs/decisions/README.md`.
