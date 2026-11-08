# Helm 4 by default; Helm 3 only when the solution declares it

- Status: accepted
- Date: 2026-11-08
- Related: [ADR-0043](0043-helm-lint-as-the-container-check-phase.md) (the check phase; unaffected),
  [docs/work/helm-4-support.md](../work/helm-4-support.md) (the design),
  [docs/work/helm-gap-analysis.md](../work/helm-gap-analysis.md) (gap 10),
  [docs/work/integration-readiness.md](../work/integration-readiness.md) (where the version is read)

## Context and Problem Statement

Helm 4 deprecates flags strata passes to `helm upgrade` (`--atomic`, a bare `--dry-run`) and changes what a bare
`--wait` means. Helm 4's replacements do not exist in Helm 3: `--rollback-on-failure` and `--wait=<mode>` are unknown
there, and `--dry-run=client` needs 3.13. So one command cannot be correct for both majors.

Helm 3 is still what most OMP machines run (shared build agents on 3.5.1, a pipeline on 3.16, Coder workspaces on
3.22), and it is still maintained. Strata has never stated which Helm it supports, and today does not know which Helm it runs: it reads
`helm version` only when a `helm` integration declares `spec.version`, and records it nowhere.

## Considered Options

- **Helm 4 only.** The least code. Every Helm 3 user breaks, and finds out from "unknown flag" at `helm upgrade`, after
  earlier steps of the deploy have applied.
- **Detect the version at run time** and pass each major its own flags. Nobody breaks. But the command depends on the
  machine: one configuration deploys with different flags on different agents, and nothing in the solution says
  which Helm it expects.
- **Helm 4 by default; Helm 3 only when the solution declares it,** with the installed version checked before
  anything applies. Chosen.

## Decision Outcome

1. **The configuration chooses the command.** Strata passes Helm 4's flags (`--rollback-on-failure`,
   `--wait=watcher`, `--dry-run=client`) unless the Helm integration the step is bound to has a `spec.version` that
   excludes Helm 4 (for example `"<4"`). Then it passes Helm 3's flags, the command strata used before this
   decision, unchanged.
2. **The installed version is checked before anything applies.** The readiness check reads `helm version` whenever
   it assesses Helm. Helm 3 without the declaration is an error that names both fixes (install Helm 4, or declare
   `spec.version: "<4"`). Helm 3 with the declaration is a deprecation warning.
3. **The version that ran is recorded.** The deploy output names the Helm version and path, and the deployment
   manifest records them per provisioner.
4. **Helm 3 is deprecated, without a date.** Messages and docs say "deprecated", not when support ends. A later
   release removes the Helm 3 command, decided then from Helm 3's own support status; a date written into strata
   would go stale if the Helm project moves its own.

### Consequences

- Good: one configuration gives the same `helm upgrade` command on every machine, and the solution states which Helm
  it expects.
- Good: a mismatch between the declaration and the installed Helm fails before any step changes anything.
- Good: Helm 4 users lose the deprecation warnings and keep today's behaviour; Helm 4's bare `--wait` already meant
  `watcher`.
- Good: an audit can tell which Helm deployed a release.
- Bad: a breaking change for Helm 3 users who declare nothing. Until they install Helm 4 or add a four-line
  integration document, every deploy stops in the preflight.
- Bad: a Helm 3 user moving to Helm 4 gets `watcher`, which needs the `watch` verb on the chart's resources; a deploy
  identity limited to `get` and `list` fails the wait.
- Bad: one more `helm version` call per run, also for Helm 4 users.
- Neutral: Helm 4 keeps its default server-side apply (`auto`); a release upgraded alternately by Helm 3 and Helm 4 is
  untested. Declaring the major per solution makes that case a readiness error instead of a silent mix.
