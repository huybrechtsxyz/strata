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
| [0010](0010-rename-configuration-repositories-to-remotes.md) | Rename configuration spec.repositories to spec.remotes           | Accepted |
| [0011](0011-promotion-strategies-for-version-progression.md) | Promotion strategies for version progression across environments | Proposed |
| [0018](0018-deployment-audit-traceability.md)                | Deployment audit and traceability for compliance                 | Proposed |
| [0025](0025-ai-agent-integration-for-build-and-deploy.md)    | AI agent integration for build and deploy workflows              | Proposed |
| [0031](0031-cost-estimation-and-visibility.md)               | Cost estimation and visibility                                   | Proposed |
| [0032](0032-approval-workflows-and-gates.md)                 | Approval workflows and gates                                     | Proposed |
| [0033](0033-github-pull-request-integration.md)              | GitHub pull request integration                                  | Proposed |
| [0035](0035-enterprise-store.md)                             | Enterprise store — private organization-level content registry   | Proposed |
| [0036](0036-workspace-provider-environment-overrides.md)     | Workspace, provider, and environment-level provider overrides    | Accepted |
| [0037](0037-mass-wave-deployment.md)                         | Fleet operations and mass wave deployment                        | Proposed |
| [0038](0038-multi-tenant-fleet-management-patterns.md)       | Multi-tenant fleet management patterns and gaps                  | Accepted |
| [0039](0039-deployment-templates.md)                         | Deployment templates                                             | Proposed |
| [0040](0040-tenant-onboarding-scaffolding.md)                | Tenant onboarding scaffolding                                    | Proposed |
| [0041](0041-gitops-controller-integration.md)                | GitOps controller integration                                    | Proposed |
| [0042](0042-deep-validation-layer-consistency.md)            | Deep validation and layer consistency                            | Proposed |

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
