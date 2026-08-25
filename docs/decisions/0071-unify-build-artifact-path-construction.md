# Unify Build Artifact Path Construction Across Builder/Deployer Pairs

- Status: proposed
- Date: 2026-08-25

## Context and Problem Statement

Every builder/deployer pair independently re-derives the same per-namespace/per-module
build output path shape, with no shared helper enforcing agreement between the two
sides. Found while writing [ADR 0070](./0070-helm-oci-repositories-and-value-substitution.md):

- Helm builder ([src/strata/builders/helm_builder.py](../../src/strata/builders/helm_builder.py#L230)):
  `deployment_build_path / namespace_name / module_name / "values.yaml"` (+ `"meta.yaml"`)
- Helm deployer ([src/strata/deployers/helm_deployer.py](../../src/strata/deployers/helm_deployer.py#L185)):
  same shape, re-typed independently (plus a third variant at
  [line 212](../../src/strata/deployers/helm_deployer.py#L212) for local chart refs)
- Compose builder ([src/strata/builders/compose_builder.py](../../src/strata/builders/compose_builder.py#L296)):
  `deployment_build_path / namespace_name / "docker-compose.yml"`
- Compose deployer ([src/strata/deployers/compose_deployer.py](../../src/strata/deployers/compose_deployer.py#L114)):
  same shape, re-typed independently

Nothing breaks today — both sides happen to agree — but the agreement is by convention
only. A future change to one side's path shape (e.g. adding a subdirectory) would not
be caught until the other side fails to find its file at runtime.

## Considered Options

- **A. Status quo.** Leave each builder/deployer pair to independently hardcode the
  path shape it needs.
- **B. Shared path-construction helper(s).** e.g. a `module_build_path(build_path, ns,
  module, filename=None)` function (per deployer type or generic) imported by both the
  builder and deployer side of each pair, so the shape is defined once.

## Decision Outcome

Not yet decided — deliberately deferred. Out of scope for ADR 0070's bug fixes (neither
bug touches this path-agreement logic), and unifying it properly means touching every
builder/deployer pair (helm, compose, terraform, sync), which is a larger, independent
refactor.

## Remaining Work

<!-- Required while Status is proposed / in-progress / partially-implemented.
     Remove this section once Status becomes implemented. -->

- Not started. Revisit Option B, decide scope (Helm+Compose only vs. all pairs), and
  design the shared helper's signature/location before implementing.
