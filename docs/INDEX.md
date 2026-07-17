# Documentation Index

This is a comprehensive guide to all Strata documentation and resources. Use this index to find what you need quickly.

---

## 🚀 **Getting Started**

| Resource                                               | Purpose                                     | For Whom                |
| ------------------------------------------------------ | ------------------------------------------- | ----------------------- |
| [README.md](../README.md)                              | Project overview, quick start, key features | Everyone                |
| [Getting Started Guide](./platform/getting-started.md) | Step-by-step setup and first deployment     | New users               |
| [Features Overview](./guides/features.md)              | Practical rundown of what strata does       | Users evaluating strata |
| [VS Code Extension README](../src/vscode/README.md)    | Extension features, installation, usage     | VS Code users           |

---

## � **Reference**

| Resource                     | Purpose                                              |
| ---------------------------- | ---------------------------------------------------- |
| [GLOSSARY.md](./GLOSSARY.md) | Terminology and core concepts used throughout strata |

---

## �📖 **User Guides**

### Core Concepts

| Guide                                                                            | Purpose                               |
| -------------------------------------------------------------------------------- | ------------------------------------- |
| [Workspace Model](./config/workspace.md)                                         | Understanding the workspace structure |
| [Deployment Configuration](./config/deployment.md)                               | Defining deployments                  |
| [Environments](./config/environment.md)                                          | Managing environment-specific configs |
| [Providers & Remote Infrastructure](./config/provider.md)                        | Setting up cloud providers            |
| [Networking & Firewalls](./config/network.md) / [Firewall](./config/firewall.md) | Network configuration                 |
| [DNS & Routing](./config/dns.md)                                                 | DNS setup and management              |
| [Modules & Reusability](./config/module.md)                                      | Creating reusable components          |
| [Namespaces & Multi-Tenancy](./config/namespace.md)                              | Isolating deployments                 |
| [Resources](./config/resource.md)                                                | Defining infrastructure resources     |
| [Workflow Orchestration](./config/workflow.md)                                   | Defining deployment workflows         |

### Operational Guides

| Guide                                                 | Purpose                                   |
| ----------------------------------------------------- | ----------------------------------------- |
| [Commands Reference](./platform/commands.md)          | Complete CLI command documentation        |
| [Validation & Error Handling](./guides/validation.md) | How validation works and error resolution |
| [Building Deployments](./guides/building.md)          | Pre-deployment artifact generation        |
| [Deploying Infrastructure](./guides/deploying.md)     | Deployment process and strategies         |
| [Managing Environments](./guides/environments.md)     | Environment composition and overrides     |
| [Secret Management](./guides/secrets.md)              | Handling sensitive data                   |
| [Audit & Compliance](./guides/audit.md)               | Tracking changes and compliance           |
| [Troubleshooting](./guides/troubleshooting.md)        | Common issues and solutions               |

### Advanced Topics

| Guide                                                      | Purpose                                                   |
| ---------------------------------------------------------- | --------------------------------------------------------- |
| [Scaffolding Templates](./guides/scaffolding-templates.md) | Creating files, bundles, and multi-tenant fleet scaffolds |
| [Policy Engine](./guides/policies.md)                      | Built-in policies and custom rules                        |
| [Lifecycle Hooks](./guides/lifecycle-hooks.md)             | Pre/post deployment scripts                               |
| [Multi-Repository Setup](./guides/multi-repo.md)           | Working with multiple configuration repos                 |
| [SBOM & Supply Chain](./guides/sbom.md)                    | Bill of materials generation                              |
| [Drift Detection](./guides/drift.md)                       | Finding configuration drift (Post-v1.0)                   |

---

## 🏗️ **Architecture & Design Decisions**

### Decision Records (ADRs)

Every significant architectural decision is documented as an ADR. Status indicators:
- ✅ **Accepted** — Approved and implemented
- 🔄 **Proposed** — Under review
- ⏸️ **Deferred** — Planned but not yet implemented
- ❌ **Deprecated** — Superseded by newer decisions

| ADR                                                                      | Title                                | Status     | Purpose                                                   |
| ------------------------------------------------------------------------ | ------------------------------------ | ---------- | --------------------------------------------------------- |
| [0001](./decisions/0001-kubernetes-style-yaml-schema.md)                 | Kubernetes-style YAML schema         | ✅ Accepted | Model structure and validation                            |
| [0002](./decisions/0002-python-click-not-compiled-cli.md)                | Python + Click CLI (not compiled)    | ✅ Accepted | Tool implementation language                              |
| [0003](./decisions/0003-layered-architecture.md)                         | Layered architecture                 | ✅ Accepted | Code organization and dependencies                        |
| [0004](./decisions/0004-exit-code-convention.md)                         | Exit code convention                 | ✅ Accepted | Standard exit codes (0/1/2/3/4) with lock conflict signal |
| [0005](./decisions/0005-secret-resolution-at-build-time.md)              | Secret resolution at build time      | ✅ Accepted | When/how secrets are injected                             |
| [0006](./decisions/0006-policy-engine-for-deployment-guardrails.md)      | Policy engine                        | ✅ Accepted | Validation rules and enforcement                          |
| [0007](./decisions/0007-deployment-state-locking.md)                     | Deployment state locking             | ✅ Accepted | Concurrent deployment prevention                          |
| [0008](./decisions/0008-infrastructure-drift-detection.md)               | Infrastructure drift detection       | ⏸️ Deferred | Detecting config vs. state differences                    |
| [0009](./decisions/0009-sbom-extended-sources-and-inventory.md)          | SBOM extended sources                | ✅ Accepted | Supply chain / Bill of materials                          |
| [0010](./decisions/0010-rename-configuration-repositories-to-remotes.md) | Rename repositories → remotes        | ✅ Accepted | Terminology clarification                                 |
| [0011](./decisions/0011-promotion-strategies-for-version-progression.md) | Promotion strategies                 | ✅ Accepted | Cross-environment version progression                     |
| [0012](./decisions/0012-rename-customer-to-tenant.md)                    | Rename customer → tenant             | ✅ Accepted | Terminology update                                        |
| [0013](./decisions/0013-auto-generated-secrets.md)                       | Auto-generated secrets               | ✅ Accepted | Automatic secret creation & seeding                       |
| [0014](./decisions/0014-onboarding-experience.md)                        | Onboarding experience                | ✅ Accepted | Getting-started walkthrough                               |
| [0015](./decisions/0015-dependency-graph.md)                             | Dependency graph                     | ✅ Accepted | File reference visualization                              |
| [0016](./decisions/0016-console-repl.md)                                 | Console REPL                         | ✅ Accepted | Interactive shell                                         |
| [0017](./decisions/0017-jinja2-templates.md)                             | Jinja2 templates                     | ✅ Accepted | YAML templating                                           |
| [0017b](./decisions/0017b-tag-based-release-workflow.md)                 | Tag-based release workflow           | ⏸️ Deferred | Git tag conventions                                       |
| [0018](./decisions/0018-deployment-audit-traceability.md)                | Deployment audit traceability        | ✅ Accepted | Change tracking & SIEM                                    |
| [0019](./decisions/0019-terraform-build-output.md)                       | Terraform build output               | ✅ Accepted | Artifact generation                                       |
| [0020](./decisions/0020-lifecycle-phases.md)                             | Lifecycle phases                     | ✅ Accepted | Pre/post hooks (27 phases)                                |
| [0021](./decisions/0021-deployment-manifests.md)                         | Deployment manifests                 | ✅ Accepted | Build & deploy artifacts                                  |
| [0022](./decisions/0022-siem-integration-splunk-hec-cef.md)              | SIEM integration (Splunk HEC)        | ✅ Accepted | Audit log forwarding                                      |
| [0023](./decisions/0023-pluggable-provisioners.md)                       | Pluggable provisioners               | ✅ Accepted | Custom infrastructure provisioners                        |
| [0024](./decisions/0024-environment-composition-flat-merge-fix.md)       | Environment composition (flat merge) | ✅ Accepted | Multi-file environment merging                            |

---

## 💻 **Developer Documentation**

### Extension (VS Code)

| Resource                                                 | Purpose                                |
| -------------------------------------------------------- | -------------------------------------- |
| [VS Code Extension README](../src/vscode/README.md)      | User-facing extension documentation    |
| [Extension DEVELOPMENT.md](../src/vscode/DEVELOPMENT.md) | Development setup, building, debugging |
| [Extension Changelog](../src/vscode/CHANGELOG.md)        | Version history and features           |

### CLI (Python)

| Resource                               | Purpose                                   |
| -------------------------------------- | ----------------------------------------- |
| [Code Architecture](./architecture.md) | Codebase structure and layers (if exists) |
| [Python Build System](./build.md)      | Building and packaging (if exists)        |

### Contributing

| Resource                                            | Purpose                              |
| --------------------------------------------------- | ------------------------------------ |
| [CONTRIBUTING.md](../CONTRIBUTING.md)               | How to contribute code/docs          |
| [GOVERNANCE.md](../.github/GOVERNANCE.md)           | Project governance & decision-making |
| [CODE_OF_CONDUCT.md](../.github/CODE_OF_CONDUCT.md) | Community expectations               |
| [SECURITY.md](../.github/SECURITY.md)               | Reporting security issues            |
| [SUPPORT.md](../.github/SUPPORT.md)                 | Getting support and reporting issues |

---

## 📚 **Examples & Tutorials**

| Resource                                              | Purpose                               |
| ----------------------------------------------------- | ------------------------------------- |
| [examples/](./examples/)                              | Working examples for common scenarios |
| [config/aws-eks/](../config/aws-eks/)                 | AWS EKS example deployment            |
| [config/azure-aks/](../config/azure-aks/)             | Azure AKS example deployment          |
| [config/gcp-gke/](../config/gcp-gke/)                 | Google GKE example deployment         |
| [config/hetzner-compose/](../config/hetzner-compose/) | Docker Compose example                |
| [config/kamatera-swarm/](../config/kamatera-swarm/)   | Docker Swarm example                  |

---

## 🔗 **External Resources**

### Related Technologies

| Tool       | Purpose                    | Link                  |
| ---------- | -------------------------- | --------------------- |
| Terraform  | Infrastructure as code     | https://terraform.io  |
| Helm       | Kubernetes package manager | https://helm.sh       |
| Kubernetes | Container orchestration    | https://kubernetes.io |
| Docker     | Container runtime          | https://docker.com    |
| Ansible    | Configuration management   | https://ansible.com   |

### Community & Support

| Resource           | Purpose                        | Link                                                |
| ------------------ | ------------------------------ | --------------------------------------------------- |
| GitHub Issues      | Bug reports & feature requests | https://github.com/huybrechtsxyz/strata/issues      |
| GitHub Discussions | Questions & community chat     | https://github.com/huybrechtsxyz/strata/discussions |
| GitHub Wiki        | Community-contributed content  | https://github.com/huybrechtsxyz/strata/wiki        |

---

## 📋 **Version-Specific Documentation**

### Current Version (v0.16.1)

- [Changelog](../CHANGELOG.md) — What's new in this version
- [v1.0 Roadmap](./v1-todo.md) — What's planned for v1.0 release

### Previous Versions

- See [GitHub Releases](https://github.com/huybrechtsxyz/strata/releases) for historical documentation

---

## 🎯 **Quick Navigation by Use Case**

### "I'm new to strata"
1. Start: [README.md](../README.md)
2. Read: [Getting Started Guide](./platform/getting-started.md)
3. Try: [Examples](./examples/)
4. Reference: [Features Overview](./guides/features.md)

### "I'm using the CLI"
1. Reference: [Commands](./platform/commands.md)
2. Learn: [Validation & Errors](./guides/validation.md)
3. Configure: [Workspace](./config/workspace.md) and [Deployment](./config/deployment.md)
4. Troubleshoot: [Troubleshooting Guide](./guides/troubleshooting.md)

### "I'm using the VS Code Extension"
1. Install: [VS Code Extension README](../src/vscode/README.md)
2. Configure: Settings in the README
3. Use: Chat, commands, tree views
4. Troubleshoot: [VS Code Extension README — Troubleshooting](../src/vscode/README.md#troubleshooting)

### "I want to contribute"
1. Read: [CONTRIBUTING.md](../CONTRIBUTING.md)
2. Review: [CODE_OF_CONDUCT.md](../.github/CODE_OF_CONDUCT.md)
3. Develop: [Extension DEVELOPMENT.md](../src/vscode/DEVELOPMENT.md) (or CLI development)
4. Reference: [Architecture ADRs](./decisions/) for design context

### "I have a question or issue"
1. Check: [Troubleshooting](./guides/troubleshooting.md)
2. Search: [GitHub Issues](https://github.com/huybrechtsxyz/strata/issues)
3. Ask: [GitHub Discussions](https://github.com/huybrechtsxyz/strata/discussions)
4. Report: Use issue templates for bugs/features

### "I need to integrate with strata"
1. Learn: [Commands Reference](./platform/commands.md)
2. Understand: [Exit Codes](./decisions/0004-exit-code-convention.md)
3. Use: [JSON Output Format](./platform/json-api.md) (if exists)
4. Reference: [Policy Engine](./guides/policies.md) for validation

---

## 📄 **File Structure**

```
strata/
├── README.md                           # Project overview
├── CONTRIBUTING.md                     # Contribution guidelines
├── CHANGELOG.md                        # Release notes
├── docs/
│   ├── README.md                       # Documentation index
│   ├── config/                         # YAML model documentation
│   │   ├── workspace.md
│   │   ├── deployment.md
│   │   ├── environment.md
│   │   ├── provider.md
│   │   ├── network.md
│   │   ├── firewall.md
│   │   ├── dns.md
│   │   ├── module.md
│   │   ├── namespace.md
│   │   ├── resource.md
│   │   └── workflow.md
│   ├── guides/                         # How-to guides
│   │   ├── features.md
│   │   ├── validation.md
│   │   ├── building.md
│   │   ├── deploying.md
│   │   ├── environments.md
│   │   ├── secrets.md
│   │   ├── audit.md
│   │   ├── policies.md
│   │   ├── lifecycle-hooks.md
│   │   ├── multi-repo.md
│   │   ├── sbom.md
│   │   ├── drift.md
│   │   └── troubleshooting.md
│   ├── platform/                       # Platform reference
│   │   ├── getting-started.md
│   │   ├── commands.md
│   │   └── json-api.md
│   ├── decisions/                      # Architecture Decision Records
│   │   ├── 0001-kubernetes-style-yaml-schema.md
│   │   ├── 0002-python-click-not-compiled-cli.md
│   │   └── ... (23 more ADRs)
│   ├── examples/                       # Runnable examples
│   └── vscode/                         # Extension documentation
├── src/vscode/
│   ├── README.md                       # User documentation
│   ├── DEVELOPMENT.md                  # Developer guide
│   └── CHANGELOG.md                    # Extension changelog
├── .github/
│   ├── CONTRIBUTING.md                 # (alt) Contribution guidelines
│   ├── CODE_OF_CONDUCT.md
│   ├── GOVERNANCE.md
│   ├── SECURITY.md
│   ├── SUPPORT.md
│   ├── CONTRIBUTING.md
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   ├── feature_request.md
│   │   └── documentation.md
│   └── pull_request_template.md
└── config/                             # Example deployments
    ├── aws-eks/
    ├── azure-aks/
    ├── gcp-gke/
    ├── hetzner-compose/
    └── kamatera-swarm/
```

---

## 🔍 **Search Tips**

- **Looking for a specific command?** → [Commands Reference](./platform/commands.md)
- **Need to understand a model?** → [config/ folder](./config/)
- **Want to know "why" a decision was made?** → [Architecture Decisions](./decisions/)
- **Stuck on a problem?** → [Troubleshooting Guide](./guides/troubleshooting.md)
- **Want to contribute?** → [CONTRIBUTING.md](../CONTRIBUTING.md)

---

## 📞 **Support & Feedback**

- **Questions?** Ask in [GitHub Discussions](https://github.com/huybrechtsxyz/strata/discussions)
- **Found a bug?** Report in [GitHub Issues](https://github.com/huybrechtsxyz/strata/issues)
- **Have a feature idea?** Use the [feature request](../.github/ISSUE_TEMPLATE/feature_request.md) template
- **Security issue?** Email security@example.com (see [SECURITY.md](../.github/SECURITY.md))

---

**Last updated:** 2026-07-06  
**Strata Version:** 0.16.1+  
**Documentation Status:** 📖 Complete
