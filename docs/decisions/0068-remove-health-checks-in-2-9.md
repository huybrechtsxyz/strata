# `stages[].health_checks` is removed in 2.9, not deprecated for a release

- Status: accepted
- Date: 2026-11-23
- Related: [ADR-0065](0065-remove-version-spec-workspace-in-2-7.md) (the same choice for `Version.spec.workspace`),
  [ADR-0066](0066-post-deploy-verification-is-a-deploy-phase.md) (post-deploy verification, whose `http` and `tcp`
  checks replace it), [post-deploy-verification.md](../design/post-deploy-verification.md) (the design, D10)

## Context and Problem Statement

A deployment stage could declare `health_checks`: `http` and `tcp` probes that were validated but never ran. Post-
deploy verification added `verify.checks` with `type: http` and `type: tcp`, which do run. The work doc planned to
deprecate `health_checks` (still accepted, with a `strata validate` warning) and remove it one minor release later.

The deprecation has not been released yet. strata has two users, both reachable directly, and a document that
declares `health_checks` gets nothing from it. When should the field go?

## Considered Options

- **Deprecate in 2.9, remove in 2.10**, as the work doc planned.
- **Remove in 2.9**, together with the checks that replace it.

## Decision Outcome

Chosen: **remove in 2.9**, because keeping a field that never runs gives a deployment two ways to declare a probe
when only one of them does anything. The fix is mechanical, and the two users can make it in the same upgrade.

- A deployment whose stage still declares `health_checks` fails to load. The message names the removal and writes
  the `verify.checks` entries that replace the probes (`name`, `type`, `url` and `expect_status`, or `host` and
  `port`, and `timeout`), not pydantic's generic "extra inputs are not permitted".
- `health_checks` is not converted automatically: a probe that never ran would start running, and could fail
  deploys, without anyone choosing that.
- `health_checks_deprecated` is gone with the field, and so is the `extends` merge of `health_checks` by name.

### Consequences

- Good, because a stage has one way to declare a probe, and it runs.
- Good, because the error gives the replacement, so the fix is a copy and paste.
- Bad, because the change is breaking within a minor release: a deployment that still declares `health_checks`
  fails to load until it is changed. Acceptable with two known users; the CHANGELOG says so under `Removed`.
