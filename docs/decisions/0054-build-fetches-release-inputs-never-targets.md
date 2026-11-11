# `build run` may fetch what a release deploys, never where it deploys

- Status: accepted
- Date: 2026-11-07
- Related: [ADR-0022](0022-strata-build-run.md) (`build run` renders and never executes; D4 is unchanged),
  [ADR-0042](0042-private-chart-registries-authenticate-through-the-remote-integration.md) (registry credentials),
  [ADR-0043](0043-helm-lint-as-the-container-check-phase.md) (the check phase),
  [ADR-0019](0019-version-pinning.md) (version pins),
  `helm-gap-analysis.md` (work doc) (gap 8, the case that raised this),
  `sbom-generation.md` (work doc) (step 3, remote charts),
  [docs/design/sbom-generation.md](../design/sbom-generation.md) (out of scope: registry lookups)

## Context and Problem Statement

`build run` already uses the network: it clones git remotes into `.strata/remotes/<name>/<reference>`, with
credentials from the remote's integration. It does not contact a registry, and that line has been drawn case by case:

- A registry chart is only described in `meta.yaml` (`chartName`, `chartVersion`); `helm upgrade` fetches it at
  deploy. So the configuration key check and `helm lint` skip it, and the SBOM records the declared version, which may
  be unset or a range.
- The SBOM design rejected looking up an image tag's digest and running `terraform init` at build time, both because
  they need network access and credentials.
- The SBOM work doc left registry charts as a later opt-in for the same reason.

Each new case reopens the same question, and without a rule the answer drifts both ways: more fetches creep into
the build, or necessary ones are pushed to the deploy.

Pushing a fetch to the deploy has a cost of its own. The check phase runs per step, just before that step, so a chart
that cannot be fetched in step 3 fails after steps 1 and 2 have applied: a half-deployed environment caused by
something the build could have found out before anything changed. And a build output that names "chart X, whichever
version the registry serves" is a recipe, not a release: what the SBOM records and what deploys can differ.

## Considered Options

- **Keep the build registry-free; fetch at deploy only.** No new dependency for `build run`. But external inputs stay
  unknown until the deploy, the SBOM cannot record what deploys, and a missing chart fails mid-deploy.
- **Per-feature opt-ins** (`build run --chart-images`, a solution setting per fetch). Nothing changes by default. But
  every feature argues the question again, and an opt-in that is off by default leaves most SBOMs as they are.
- **A rule for what the build may fetch, on by default, with an offline switch.** Chosen.

## Decision Outcome

Chosen: **`build run` may fetch the inputs of a release, but never contact its targets**, under five conditions.

1. **Inputs only, never targets.** The build may read the sources of what it ships: git remotes, chart registries,
   and image registries (for digests). It never contacts a deployment target: a cluster, a Docker engine, a cloud
   API, a Terraform state backend. Contacting a target remains exclusive to `deploy`.
2. **Only what can be pinned.** A fetch is allowed only when its result can be pinned to an immutable identity: a git
   commit, a chart version with its archive digest, an image digest. The build output records that pin, and the
   deploy uses the pin, not the declaration.
3. **Cached under `.strata/`, keyed by the pin.** Each kind of input has its own directory (`remotes/`, `helm/`, ...).
   A cached input is not fetched again, so a rebuild with a warm cache and pinned versions makes no network call.
4. **A tool's own lock is not invented.** Where a tool keeps a lock file that people review (Terraform's
   `.terraform.lock.hcl`), strata reads it and does not produce one at build time. `terraform init` stays out of the
   build.
5. **The deploy verifies; it does not resolve again.** A deploy that fetches an input itself (for example in a CI job
   without the build's cache) compares it with the recorded pin and fails on a mismatch, before any step changes
   anything.

A failed fetch is a build error, as a failed git clone is today: the source answered, and the answer was no (no such
chart or version, credentials rejected, unreachable). Credentials for a fetch come from the remote's integration,
read the way the build already reads git credentials.

A fetch the machine cannot attempt is different. When the tool that fetches is not installed, or the credentials are
in a store a plain build does not read (`--resolve` not given), an input the build only needs to pin, not to render,
is left **unpinned**: the build warns, records the declaration as it is, and goes on. The deploy refuses an unpinned
input before any step runs. Whoever deploys an input has its tool and its credentials, so a developer who never
deploys it is not made to install either, and an unpinned build cannot reach a target. An input the build needs to
render (a git source) still fails the build, since there is nothing to render without it.

`build run --offline` attempts no fetch: a cached input is pinned from the cache, and any other is left unpinned.

This decision does not make anything fetch. Each input that starts fetching at build time (registry charts first)
is its own change, designed against these conditions.

### Consequences

- Good: one test for every future case ("an input, pinnable, cached, not a tool's own lock") instead of a fresh
  argument each time.
- Good: an unreachable or missing input fails the build, before a deploy has changed anything.
- Good: the build output and the SBOM name what deploys, not what was declared.
- Good: `ADR-0022` D4 holds: fetching an input is not executing anything.
- Bad: `build run` depends on the registries a release uses being reachable, and needs their credentials. A warm cache
  and `--offline` limit that, but a cold build in CI needs the network.
- Good: a machine without a fetching tool or its credentials still builds; only a deploy of an unpinned input
  fails, and it fails before anything changes.
- Bad: a CI build job without the tool produces a build that its deploy job refuses, so the mistake shows at deploy,
  not at build.
- Bad: the deploy and the build can run in different CI jobs, so the cache may be missing at deploy; condition 5
  then costs a second fetch.
- Bad: a git remote whose `reference` is a branch does not meet condition 2 today: the checkout is cached by the
  reference name and its commit is not recorded in the build output. Bringing git remotes under the rule is a
  follow-up, not part of this decision.
- Neutral: this supersedes the "network access at build time" reasons given in
  [docs/design/sbom-generation.md](../design/sbom-generation.md) for not looking up image digests, and the opt-in
  proposed for remote charts in `sbom-generation.md` (work doc) step 3. Its reason for
  not running `terraform init` stands, under condition 4.
