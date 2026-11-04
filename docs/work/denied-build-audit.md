# Policy denials and the audit trail - Work

- Status: draft
- Last updated: 2026-11-03

## Overview

When a `build run` is denied by a policy, what does the audit trail record? Today the answer depends on the policy,
and for most of them it is "nothing durable". The gap was first noted for `image_tag_policy`, whose design
([image-tag-policy.md](../design/image-tag-policy.md), History) records it as a cost of failing fast: "a denied
build records no result in `policy_results.json`". This doc checks that against the source, finds the question is
wider than one policy, and lays out what could be decided.

It is not in `docs/design/` because the main question is open: is a denied build supposed to be in the audit trail
at all?

## Current state

Verified against the source on 2026-11-03.

### What is recorded, and where

| Step                                           | Records a result in `policy_results.json` when it denies?                                                                                                      |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `build run`, `image_tag_policy`                | No. It runs before anything is cleaned or written and returns on a breach, before the file is written.                                                         |
| `build run`, `cve_policy` and `checkov_policy` | Yes. Both are evaluated after the SBOM, their results are written together at the end, even when a breach already failed the build. Skipped under `--dry-run`. |
| `deploy run`, `image_tag_policy`               | Yes. The result is appended to the sidecar before the run returns on a breach.                                                                                 |
| `strata policy check`                          | Yes, appended when the build path exists.                                                                                                                      |

`policy_results.json` is a local sidecar under the build path. `strata policy list` reads it back, so a denied
`cve_policy` build shows its last result locally; a denied `image_tag_policy` one shows none.

### How it reaches the audit trail

- Only `deploy run` writes an audit manifest. `finalize_and_distribute_deploy_audit()` runs after `deploy_run()`
  returns, whatever it returned, reads the sidecar into `policy_results` and writes and distributes the manifest
  (not under `--dry-run` or `--smoke-test`). A `deploy run` denied by the tag policy therefore does appear in the
  manifest, with a failed status.
- `DeploymentManifestModel.action` allows `build`, `deploy` and `destroy`; `deploy run`/`deploy apply` set `deploy` and
  `deploy destroy` sets `destroy`. Nothing writes a `build` manifest.
- So a denied `build run` never reaches any sink, for any policy: the sidecar holds the result locally for
  `cve_policy` and `checkov_policy`, and nothing at all for `image_tag_policy`. A pipeline that stops at the denied
  build produces no manifest, since no `deploy run` follows.

## Options

- **A. It is by design.** The audit trail records changes made to an environment; a denied build changed nothing,
  and the CI log is the record of the denial. Document it (in the policy design docs and the audit trail doc) and
  change nothing. Cheapest, and consistent with how `audit` was scoped around `deploy run`.
- **B. Record a build-action manifest on denial.** Use the existing `action: build` value: write and distribute a
  manifest when a build is denied, with the same sinks and `policy_results`. It answers "show me every time the
  gate stopped something", which a compliance reviewer might ask. It is a new producer, new status semantics and
  noise in the sink for every failed CI build.
- **C. Local consistency only.** Write the `image_tag_policy` result to the sidecar even when it denies, as the CVE
  and Checkov policies do. The cost is that `build run` would have to create the build path (or a result file
  somewhere else) before the "nothing changed on a breach" promise holds, since the design chose to fail before
  cleaning or writing. The value is only local (`policy list`, a following `policy check`), because nothing sends the
  sidecar anywhere without a deploy.

A and C are independent: C does not need B, and B makes C pointless for the audit use.

## Proposal

Take A unless a real consumer needs denied attempts, in line with how the audit trail was scoped (no confirmed
demand, so no producer). Write down the rule where a reader will look for it. Do C only if `policy list` showing no
row after a denied tag-policy build turns out to confuse someone: it changes a documented guarantee for a local
convenience.

## Related Decisions

- [image-tag-policy.md](../design/image-tag-policy.md): the policy and the trade-off this doc re-examines
- [security-gates.md](../design/security-gates.md): the policy results file and the audit manifest hook
- [audit-trail.md](audit-trail.md): the audit trail design, which is itself still a work doc

## Remaining Work / Open Questions

- [ ] Decide A, B or C. The deciding fact is whether anyone needs denied attempts recorded; nothing found so far
      says so.
- [ ] Confirm the exit state of a `deploy run` denied by `image_tag_policy`: the manifest status and that the
      appended result is read (the code path is straightforward but no test pins it for this policy).
- [ ] If A: one sentence each in `image-tag-policy.md` and `security-gates.md` stating that a denied build produces
      no manifest and where the result lives.
- [ ] If B: design the `build` manifest (what status means, which sinks, whether `--dry-run` and `policy check`
      write one) before any code.

## Changelog

- 2026-11-03: Created from the open item left after the image tag policy work. Verified the three build-phase
  policies and the deploy path against `build_controller.py`, `policy_results.py`, `audit_run.py` and
  `deploy_command.py`; the gap is general to `build run` denials, not specific to the tag policy.
