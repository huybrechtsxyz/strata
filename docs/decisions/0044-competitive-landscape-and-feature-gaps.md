# Competitive Landscape and Feature Gaps

- Status: proposed
- Date: 2026-07-15

## Context and Problem Statement

Strata is not the first tool to tackle infrastructure orchestration above Terraform and
Ansible. Multiple open-source and commercial products address overlapping concerns. This
ADR catalogues those tools, identifies what they do well (or better), and assesses whether
each capability is relevant for strata to adopt.

This is an inventory — individual features that warrant implementation will get their own
ADRs.

## Competitor Tools

| Tool               | Type              | Primary Strength                                                       |
| ------------------ | ----------------- | ---------------------------------------------------------------------- |
| Terragrunt         | OSS (Gruntwork)   | DRY Terraform configs, dependency orchestration, environment promotion |
| Terramate          | OSS               | Stacks, change detection, code generation, orchestration               |
| Atmos              | OSS (Cloud Posse) | YAML-driven stacks, components, vendoring, environment inheritance     |
| Spacelift          | SaaS              | Policy-as-code, drift detection, approval workflows, VCS integration   |
| Env0               | SaaS              | Self-service environments, cost guardrails, TTL environments           |
| Scalr              | SaaS              | Hierarchical workspaces, OPA policies, RBAC, cost tracking             |
| Pulumi Deployments | SaaS + OSS        | Real-language IaC, deployment automation, environments                 |
| Crossplane         | OSS (CNCF)        | Kubernetes-native infra provisioning, compositions, XRDs               |
| Humanitec / Score  | SaaS + OSS        | Platform engineering, workload spec abstraction                        |
| ArgoCD / Flux      | OSS (CNCF)        | GitOps reconciliation for Kubernetes workloads                         |

## Feature Comparison

Legend:
- **Strata status**: ✅ exists | 🔨 ADR proposed | ❌ not present
- **Useful for strata?**: yes / maybe / no — with rationale
- **Ref**: existing ADR if applicable

### 1. DRY Configuration & Inheritance

|                         |                                                                                                                                              |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Terragrunt (`include` blocks), Atmos (stack inheritance), Terramate (globals)                                                                |
| **What they do**        | Eliminate repetition across environments via layered inheritance, includes, and variable cascading                                           |
| **Better than strata?** | Terragrunt's `include` is more flexible. Atmos has deep inheritance with mixins. Strata's environment composition is functional but simpler. |
| **Strata status**       | ✅ Environment composition exists                                                                                                             |
| **Useful for strata?**  | Maybe — current flat-merge works (ADR 0024) but mixin/include patterns could reduce duplication in large repos                               |
| **Ref**                 | [ADR 0024](0024-environment-composition-flat-merge-fix.md)                                                                                   |

### 2. Dependency Graph / Execution Order

|                         |                                                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Terragrunt (`dependency` blocks), Spacelift (stack dependencies), Atmos (component deps)                         |
| **What they do**        | Automatically determine deployment order from declared dependencies. Parallel where safe, serial where required. |
| **Better than strata?** | Yes — strata stages are linear and manually ordered. No automatic parallelism within a deployment.               |
| **Strata status**       | 🔨 Partially proposed                                                                                             |
| **Useful for strata?**  | Yes — would enable parallel stage execution and cross-deployment dependency resolution                           |
| **Ref**                 | [ADR 0015](0015-flow-command-dependency-graph.md)                                                                |

### 3. Drift Detection

|                         |                                                                                                   |
| ----------------------- | ------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Spacelift (scheduled drift runs), Env0 (drift alerts), Crossplane (continuous reconciliation)     |
| **What they do**        | Periodically compare actual infrastructure state against declared state. Alert or auto-remediate. |
| **Better than strata?** | Yes — strata has no scheduled drift detection today.                                              |
| **Strata status**       | 🔨 ADR proposed                                                                                    |
| **Useful for strata?**  | Yes — critical for production governance. Could run as `strata deploy health` extension.          |
| **Ref**                 | [ADR 0008](0008-infrastructure-drift-detection.md)                                                |

### 4. Policy Engine / Guardrails

|                         |                                                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------------------ |
| **Who does it well**    | Spacelift (OPA + built-in), Scalr (OPA), Env0 (custom policies), Terramate (checks)              |
| **What they do**        | Evaluate policies before/after plan — block deploys that violate cost, security, or naming rules |
| **Better than strata?** | Yes — strata has the ADR but no implementation. Spacelift's is production-hardened.              |
| **Strata status**       | 🔨 ADR proposed                                                                                   |
| **Useful for strata?**  | Yes — especially for enterprise/multi-tenant scenarios                                           |
| **Ref**                 | [ADR 0006](0006-policy-engine-for-deployment-guardrails.md)                                      |

### 5. Cost Estimation

|                         |                                                                                                    |
| ----------------------- | -------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Env0 (Infracost integration), Spacelift (cost policies), Scalr (cost dashboard)                    |
| **What they do**        | Estimate cloud costs from Terraform plan output before apply. Set budgets and alert on thresholds. |
| **Better than strata?** | Yes — strata has no cost estimation today.                                                         |
| **Strata status**       | 🔨 ADR proposed                                                                                     |
| **Useful for strata?**  | Yes — especially pre-deploy cost visibility in `strata build plan` output                          |
| **Ref**                 | [ADR 0031](0031-cost-estimation-and-visibility.md)                                                 |

### 6. Approval Workflows / Gates

|                         |                                                                                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Spacelift (approval policies), Env0 (environment policies), Scalr (run approval)                              |
| **What they do**        | Require human or automated approval before production deploys. Role-based, time-windowed, environment-scoped. |
| **Better than strata?** | Yes — strata has `--force` but no structured approval flow.                                                   |
| **Strata status**       | 🔨 ADR proposed                                                                                                |
| **Useful for strata?**  | Yes — mandatory for regulated environments                                                                    |
| **Ref**                 | [ADR 0032](0032-approval-workflows-and-gates.md)                                                              |

### 7. GitOps Reconciliation

|                         |                                                                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **Who does it well**    | ArgoCD, Flux, Crossplane                                                                                                                   |
| **What they do**        | Continuously reconcile desired state in git with actual cluster state. Self-healing.                                                       |
| **Better than strata?** | Different paradigm — strata is push-based (imperative deploy), GitOps is pull-based (declarative reconciliation). Not directly comparable. |
| **Strata status**       | 🔨 ADR proposed                                                                                                                             |
| **Useful for strata?**  | Maybe — strata could generate ArgoCD/Flux manifests as a deployment target rather than reimplementing reconciliation                       |
| **Ref**                 | [ADR 0041](0041-gitops-controller-integration.md)                                                                                          |

### 8. Self-Service Environments (Ephemeral / TTL)

|                         |                                                                                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Env0 (TTL environments), Spacelift (preview stacks), Humanitec (on-demand envs)                               |
| **What they do**        | Spin up short-lived environments for PR previews or testing, auto-destroy after TTL or merge.                 |
| **Better than strata?** | Yes — strata has no ephemeral environment concept.                                                            |
| **Strata status**       | ❌ Not present                                                                                                 |
| **Useful for strata?**  | Maybe — useful for dev/test workflows but adds complexity. Could be a deployment template pattern (ADR 0039). |
| **Ref**                 | [ADR 0039](0039-deployment-templates.md) (tangential)                                                         |

### 9. VCS Integration (PR comments, plan previews)

|                         |                                                                                        |
| ----------------------- | -------------------------------------------------------------------------------------- |
| **Who does it well**    | Spacelift (PR decoration), Env0 (PR plans), Atlantis (plan-on-PR)                      |
| **What they do**        | Post Terraform plan output as PR comments. Show cost diff. Gate merge on plan success. |
| **Better than strata?** | Yes — strata has the ADR but no PR decoration.                                         |
| **Strata status**       | 🔨 ADR proposed                                                                         |
| **Useful for strata?**  | Yes — high-value developer experience improvement                                      |
| **Ref**                 | [ADR 0033](0033-github-pull-request-integration.md)                                    |

### 10. State Locking / Concurrency Control

|                         |                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------ |
| **Who does it well**    | Terraform Cloud (native locking), Spacelift (run queuing), Terragrunt (state lock retries) |
| **What they do**        | Prevent concurrent applies to the same state. Queue or reject conflicting runs.            |
| **Better than strata?** | Terraform Cloud's locking is seamless. Strata has the concept proposed.                    |
| **Strata status**       | 🔨 ADR proposed                                                                             |
| **Useful for strata?**  | Yes — essential for team environments and CI pipelines                                     |
| **Ref**                 | [ADR 0007](0007-deployment-state-locking.md)                                               |

### 11. Module / Component Registry (Private)

|                         |                                                                                             |
| ----------------------- | ------------------------------------------------------------------------------------------- |
| **Who does it well**    | Terraform Cloud (private registry), Spacelift (module management), Scalr (module registry)  |
| **What they do**        | Host versioned, private Terraform modules with documentation, examples, and access control. |
| **Better than strata?** | Yes — strata has the enterprise store concept but no registry UX.                           |
| **Strata status**       | 🔨 ADR proposed                                                                              |
| **Useful for strata?**  | Yes — `strata store` for sharing modules/configs across teams                               |
| **Ref**                 | [ADR 0035](0035-enterprise-store.md)                                                        |

### 12. Change Detection (Only Deploy What Changed)

|                         |                                                                                                        |
| ----------------------- | ------------------------------------------------------------------------------------------------------ |
| **Who does it well**    | Terramate (change detection via git diff), Spacelift (affected stacks), Atmos (component filtering)    |
| **What they do**        | Detect which stacks/components changed in a commit and only plan/apply those. Saves time in monorepos. |
| **Better than strata?** | Yes — strata rebuilds everything. `strata diff show` exists but doesn't drive selective deploy.        |
| **Strata status**       | ✅ Partial (`strata diff show`)                                                                         |
| **Useful for strata?**  | Yes — critical at scale. Should drive `strata deploy run --changed-only`                               |
| **Ref**                 | None — candidate for new ADR                                                                           |

### 13. RBAC / Multi-Tenancy Access Control

|                         |                                                                                                                            |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Scalr (hierarchical RBAC), Spacelift (spaces + policies), Env0 (org/project/env scoping)                                   |
| **What they do**        | Fine-grained access: who can plan, who can apply, per environment/tenant/project.                                          |
| **Better than strata?** | Yes — strata has no access control layer (relies on git permissions).                                                      |
| **Strata status**       | ❌ Not present                                                                                                              |
| **Useful for strata?**  | Maybe — relevant for enterprise/SaaS but may be out of scope for a CLI tool. Git branch protection + CI gates may suffice. |
| **Ref**                 | None                                                                                                                       |

### 14. Real-Time Streaming / Progress UI

|                         |                                                                                      |
| ----------------------- | ------------------------------------------------------------------------------------ |
| **Who does it well**    | Spacelift (live log streaming), Terraform Cloud (run UI), Pulumi (rich CLI output)   |
| **What they do**        | Stream plan/apply output in real-time with structured progress indicators.           |
| **Better than strata?** | Yes — strata currently buffers until completion.                                     |
| **Strata status**       | 🔨 ADR proposed                                                                       |
| **Useful for strata?**  | Yes — especially for long-running deploys. NDJSON streaming for tooling integration. |
| **Ref**                 | [ADR 0029](0029-realtime-progress-streaming-ndjson.md)                               |

### 15. Workload Abstraction (Score / Platform Spec)

|                         |                                                                                                                              |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Humanitec (Score), Backstage (templates), Crossplane (Compositions/XRDs)                                                     |
| **What they do**        | Developers describe workloads in a simplified spec; the platform translates to actual infrastructure resources.              |
| **Better than strata?** | Different layer — strata operates at the infrastructure level, not the workload/app level.                                   |
| **Strata status**       | ❌ Not present                                                                                                                |
| **Useful for strata?**  | No — strata's sweet spot is infrastructure orchestration. Workload abstraction is a separate concern that runs above strata. |
| **Ref**                 | None                                                                                                                         |

### 16. Multi-Provisioner Orchestration (Terraform + Ansible + Helm under one roof)

|                         |                                                                                                    |
| ----------------------- | -------------------------------------------------------------------------------------------------- |
| **Who does it well**    | Strata (native), Spacelift (Terraform + Pulumi + Ansible + CloudFormation)                         |
| **What they do**        | Orchestrate multiple IaC tools in a single deployment pipeline with shared context.                |
| **Better than strata?** | Spacelift supports more provisioners. Strata's pluggable framework is well-designed for extension. |
| **Strata status**       | ✅ Exists (Terraform + Ansible)                                                                     |
| **Useful for strata?**  | Already a differentiator — extend with more provisioners as needed (Helm, Pulumi)                  |
| **Ref**                 | [ADR 0023](0023-pluggable-provisioner-framework.md)                                                |

## Summary — Priority Assessment

| Priority | Feature                               | Status  | Rationale                                                      |
| -------- | ------------------------------------- | ------- | -------------------------------------------------------------- |
| High     | Dependency graph / parallel execution | 🔨       | Scale blocker — manual ordering breaks above ~10 stages        |
| High     | Drift detection                       | 🔨       | Table-stakes for production governance                         |
| High     | Change detection → selective deploy   | Partial | CI time savings compound at scale                              |
| High     | State locking                         | 🔨       | Team safety — concurrent apply is destructive                  |
| Medium   | Policy engine                         | 🔨       | Enterprise requirement, but `validate` covers some ground      |
| Medium   | Cost estimation                       | 🔨       | Developer experience — "what will this cost?" before apply     |
| Medium   | VCS / PR integration                  | 🔨       | Developer experience — plan-on-PR is expected                  |
| Medium   | Approval workflows                    | 🔨       | Compliance — regulated industries mandate it                   |
| Medium   | Progress streaming                    | 🔨       | UX — long deploys with no output feel broken                   |
| Low      | Ephemeral environments                | ❌       | Useful but complex; deployment templates may suffice           |
| Low      | Private module registry               | 🔨       | Enterprise store ADR covers this; not urgent for small teams   |
| Low      | GitOps integration                    | 🔨       | Generate manifests for ArgoCD rather than reimplement          |
| N/A      | RBAC                                  | ❌       | Out of scope — git permissions + CI gates cover this for a CLI |
| N/A      | Workload abstraction                  | ❌       | Wrong layer — strata is infra, not app platform                |

## Notes

- Strata's key differentiator remains: **single CLI, strict schema validation, multi-cloud
  multi-provisioner, SBOM built-in** — no competitor combines all four.
- The "High" items above are where competitors genuinely outperform strata today and where
  users would expect parity.
- Each feature row marked 🔨 already has a proposed ADR. Implementation priority should
  follow this table.
