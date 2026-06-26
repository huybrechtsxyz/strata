# Architectural Decision Records

This directory contains the Architectural Decision Records (ADRs) for strata,
using the [MADR](https://adr.github.io/madr/) format.

An ADR captures a significant design choice — what was decided, what alternatives
were considered, and why. They exist so the rationale survives beyond the author.

## Index

| #                                                            | Title                                                            | Status   |
| ------------------------------------------------------------ | ---------------------------------------------------------------- | -------- |
| [0001](0001-kubernetes-style-yaml-schema.md)                 | Kubernetes-style YAML schema for config documents                | Accepted |
| [0002](0002-python-click-not-compiled-cli.md)                | Python + Click for the CLI, not a compiled binary                | Accepted |
| [0003](0003-layered-architecture.md)                         | Strict layered architecture (commands → controllers → services)  | Accepted |
| [0004](0004-exit-code-convention.md)                         | Four exit codes: 0 success, 1 system, 2 usage, 3 validation      | Accepted |
| [0005](0005-secret-resolution-at-build-time.md)              | Resolve secrets at build time, not deploy time                   | Accepted |
| [0006](0006-policy-engine-for-deployment-guardrails.md)      | Policy engine for deployment guardrails                          | Proposed |
| [0007](0007-deployment-state-locking.md)                     | Deployment state locking                                         | Proposed |
| [0008](0008-infrastructure-drift-detection.md)               | Infrastructure drift detection                                   | Proposed |
| [0009](0009-sbom-extended-sources-and-inventory.md)          | SBOM extended sources and inventory                              | Proposed |
| [0010](0010-rename-configuration-repositories-to-remotes.md) | Rename configuration spec.repositories to spec.remotes           | Proposed |
| [0011](0011-promotion-strategies-for-version-progression.md) | Promotion strategies for version progression across environments | Proposed |
| [0018](0018-deployment-audit-traceability.md)                | Deployment audit and traceability for compliance                 | Proposed |

## Adding a new ADR

1. Copy the template below into `docs/decisions/NNNN-title-with-dashes.md`.
2. Fill in the sections. Remove optional sections you don't need.
3. Add a row to the index table above.
4. Add the file to the `decisions/` toctree in `docs/index.rst`.

### Minimal template

```markdown
# {Short title — what was decided}

- Status: accepted
- Date: YYYY-MM-DD

## Context and Problem Statement

{What forced this decision?}

## Considered Options

- Option A
- Option B

## Decision Outcome

Chosen: **Option A**, because {one-line justification}.

### Consequences

- Good: {positive effect}
- Bad: {trade-off or cost}
```
