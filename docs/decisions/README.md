# Architectural Decision Records

This directory contains the Architectural Decision Records (ADRs) for strata,
using the [MADR](https://adr.github.io/madr/) format.

An ADR captures a significant design choice — what was decided, what alternatives
were considered, and why. They exist so the rationale survives beyond the author.

## Index

| #                                                                   | Title                                                                          | Status    |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------ | --------- |
| [0001](0001-kubernetes-style-yaml-schema.md)                        | Kubernetes-style YAML schema for config documents                              | Accepted  |
| [0002](0002-python-click-not-compiled-cli.md)                       | Python + Click for the CLI, not a compiled binary                              | Accepted  |
| [0003](0003-layered-architecture.md)                                | Strict layered architecture (commands → controllers → services)                | Accepted  |
| [0004](0004-exit-code-convention.md)                                | Four exit codes: 0 success, 1 system, 2 usage, 3 validation                    | Accepted  |
| [0005](0005-secret-resolution-at-build-time.md)                     | Resolve secrets at build time, not deploy time                                 | Accepted  |
| [0006](0006-policy-engine-for-deployment-guardrails.md)             | Policy engine for deployment guardrails                                        | Proposed  |
| [0007](0007-deployment-state-locking.md)                            | Deployment state locking                                                       | Proposed  |
| [0008](0008-infrastructure-drift-detection.md)                      | Infrastructure drift detection                                                 | Proposed  |
| [0009](0009-sbom-extended-sources-and-inventory.md)                 | SBOM extended sources and inventory                                            | Proposed  |
| [0010](0010-rename-configuration-repositories-to-remotes.md)        | Rename configuration spec.repositories to spec.remotes                         | Accepted  |
| [0011](0011-promotion-strategies-for-version-progression.md)        | Promotion strategies for version progression across environments               | Proposed  |
| [0012](0012-rename-customer-to-tenant.md)                           | Rename CustomerModel to TenantModel                                            | Completed |
| [0013](0013-auto-generated-secrets.md)                              | Auto-generated store values (secrets, variables, features)                     | Completed |
| [0014](0014-onboarding-experience.md)                               | Guided onboarding and cold-start experience                                    | Completed |
| [0015](0015-flow-command-dependency-graph.md)                       | `strata validate graph` — Workspace Dependency Graph                           | Completed |
| [0016](0016-console-interactive-repl.md)                            | `strata console` — Interactive Workspace Console                               | Cancelled |
| [0017](0017-jinja2-template-engine.md)                              | Consolidate Templating on Jinja2                                               | Completed |
| [0018](0018-deployment-audit-traceability.md)                       | Deployment audit and traceability for compliance                               | Proposed  |
| [0019](0019-configurable-terraform-build-output.md)                 | Configurable Terraform build output                                            | Completed |
| [0020](0020-cli-parameter-consistency-standard.md)                  | CLI Parameter Consistency Standard for all 80+ subcommands                     | Completed |
| [0021](0021-deployment-manifests-as-first-class-build-artifacts.md) | Deployment Manifests as First-Class Build Artifacts                            | Completed |
| [0022](0022-siem-integration-splunk-hec-cef.md)                     | SIEM Integration: Splunk HEC + CEF Format                                      | Completed |
| [0023](0023-pluggable-provisioner-framework.md)                     | Pluggable provisioner framework                                                | Completed |
| [0024](0024-environment-composition-flat-merge-fix.md)              | Environment composition — complete flat-merge and provenance                   | Completed |
| [0025](0025-ai-agent-integration-for-build-and-deploy.md)           | AI agent integration for build and deploy workflows                            | Proposed  |
| [0026](0026-resolved-model-cache.md)                                | Resolved-model cache for fleet-wide command performance                        | Proposed  |
| [0027](0027-command-timeout-for-long-running-operations.md)         | Command Timeout for Long-Running Operations                                    | Proposed  |
| [0028](0028-sigterm-graceful-shutdown-and-lock-release.md)          | SIGTERM Graceful Shutdown and Deployment Lock Release                          | Proposed  |
| [0029](0029-realtime-progress-streaming-ndjson.md)                  | Real-Time Progress Streaming via ndjson (`--output ndjson`)                    | Completed |
| [0030](0030-command-lifecycle-explicitness-and-thin-overrides.md)   | Explicit Command Lifecycle: ABC-Enforced Phases and Thin Overrides             | Completed |
| [0031](0031-cost-estimation-and-visibility.md)                      | Cost estimation and visibility                                                 | Proposed  |
| [0032](0032-approval-workflows-and-gates.md)                        | Approval workflows and gates                                                   | Proposed  |
| [0033](0033-github-pull-request-integration.md)                     | GitHub pull request integration                                                | Proposed  |
| [0034](0034-diagram-visualization-in-vscode-extension.md)           | Diagram visualization in VS Code extension                                     | Proposed  |
| [0035](0035-enterprise-store.md)                                    | Enterprise store — private organization-level content registry                 | Proposed  |
| [0036](0036-workspace-provider-environment-overrides.md)            | Workspace, provider, and environment-level provider overrides                  | Accepted  |
| [0037](0037-mass-wave-deployment.md)                                | Fleet operations and mass wave deployment                                      | Proposed  |
| [0038](0038-multi-tenant-fleet-management-patterns.md)              | Multi-tenant fleet management patterns and gaps                                | Accepted  |
| [0039](0039-deployment-templates.md)                                | Deployment templates                                                           | Proposed  |
| [0040](0040-tenant-onboarding-scaffolding.md)                       | Tenant onboarding scaffolding                                                  | Proposed  |
| [0041](0041-gitops-controller-integration.md)                       | GitOps controller integration                                                  | Proposed  |
| [0042](0042-deep-validation-layer-consistency.md)                   | Deep validation and layer consistency                                          | Proposed  |
| [0043](0043-tenant-offboarding.md)                                  | Tenant offboarding                                                             | Proposed  |
| [0044](0044-competitive-landscape-and-feature-gaps.md)              | Competitive landscape and feature gaps                                         | Proposed  |
| [0045](0045-datetime-format-and-handling-standard.md)               | Date / Time Format and Handling Standard                                       | Accepted  |
| [0046](0046-bicep-provisioner-as-terraform-alternative.md)          | Bicep Provisioner as Azure-Native Terraform Alternative                        | Proposed  |
| [0047](0047-pulumi-provisioner-code-first-iac.md)                   | Pulumi Provisioner — Code-First Multi-Cloud IaC                                | Proposed  |
| [0048](0048-cdk-provisioner-on-popular-demand.md)                   | CDK Provisioner — Cloud Development Kit                                        | Proposed  |
| [0049](0049-workflow-as-executable-runbook.md)                      | Workflow file as executable project runbook                                    | Proposed  |
| [0058](0058-cross-deployment-dependency-gating.md)                  | Cross-deployment dependency gating (layered tenant/landscape/zone hierarchies) | Proposed  |

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
