# strata TODO — Feature Gaps & Implementation Items

## Overview
This document tracks identified gaps, missing features, and enhancement opportunities for strata. Each item below is formatted as a GitHub issue template ready for conversion.

---

## 🎯 User Experience & Onboarding

### Issue #1: Complete `strata console` Interactive REPL Implementation
**Status:** In-flight (ADR-0016)  
**Severity:** High  
**Related files:** `src/strata/commands/cli_console.py`, `src/strata/commands/console/`

**Description:**
Implement a fully functional interactive console session (`strata console`) that keeps workspace state in memory, offers command completion, and provides guided onboarding without requiring users to shell-hop between commands.

**Acceptance Criteria:**
- [ ] Console launches with `strata console` command
- [ ] Workspace state (solution, profiles, files) persists across commands within session
- [ ] Command completion available (tab autocomplete)
- [ ] `guide` checklist integrates into console flow
- [ ] Users can scaffold, validate, and explore without leaving session
- [ ] All existing commands callable from console context
- [ ] Session restarts cleanly and states are not lost between restart
- [ ] Works on Windows (PowerShell), macOS, and Linux shells

**Dependencies:**
- `prompt_toolkit` (already in consideration, verify in `pyproject.toml`)
- Refactor `SolutionController` to support stateful operations

**Implementation notes:**
- Based on ADR-0016, decision is to use `strata console` as a top-level command alongside `guide`
- `guide` remains single-shot for CI/scripting (no breaking changes)
- Console should compose existing CLI capabilities, not duplicate them

---

### Issue #2: Implement Dependency Graph Visualization (`strata validate graph`)
**Status:** Designed (ADR-0015)  
**Severity:** Medium  
**Related files:** `src/strata/validators/`, `docs/decisions/0015-flow-command-dependency-graph.md`

**Description:**
Add `strata validate graph` subcommand to generate Mermaid dependency diagrams of workspace structure and infrastructure topology.

**Acceptance Criteria:**
- [ ] `strata validate graph --mode files` generates file-level dependency diagram (YAML file nodes, cross-repo references as edges)
- [ ] `strata validate graph --mode resources` generates resource-level diagram (infrastructure topology, module dependencies, `depends_on` chains)
- [ ] Output format: Mermaid diagram (default), with optional JSON/GraphQL export
- [ ] Detects circular dependencies and reports them clearly
- [ ] Converts `validate` to a Click group with two subcommands: `validate run` (current behavior, default) and `validate graph`
- [ ] Works with multi-repo setups and cross-repo `@repo_name/path` references
- [ ] Diagrams include legend explaining node types and edge meanings

**Dependencies:**
- Mermaid library (or integrate via template strings)
- Refactor `validators/base_validator.py` to expose graph construction logic

**Testing:**
- Test with single-repo, multi-repo, and circular-reference scenarios

---

## 🔒 Compliance & Governance

### Issue #3: Complete Policy Engine Implementation (ADR-0006)
**Status:** Designed, partially implemented  
**Severity:** High (compliance-critical)  
**Related files:** `src/strata/validators/policies/`, `src/strata/models/policy_model.py`

**Description:**
Implement a complete native policy engine for enforcing guardrails across the strata lifecycle (validate, build, plan, deploy phases).

**Acceptance Criteria:**
- [ ] Policy evaluation at all lifecycle phases: validate, build, plan, deploy
- [ ] Built-in policies:
  - [ ] `customer_zones` — enforce provider zones against customer restrictions
  - [ ] `required_tags` — validate presence of mandatory tags
  - [ ] `naming_policy` — validate naming conventions (regex patterns)
  - [ ] `resource_type_restrictions` — deny/allow specific resource types by zone
  - [ ] `cost_threshold` — warn/deny on estimated cost overages
- [ ] `script` policy type for external tool delegation (OPA, Checkov, custom)
- [ ] Enforcement levels: `deny` (block), `warn` (log), `audit` (no-op, record only)
- [ ] Policy results included in build artifact and audit trail
- [ ] Phase-specific policy context (e.g., `plan` policies receive Terraform plan JSON)
- [ ] Composable policies (e.g., policy A triggers policy B)
- [ ] Escape hatch: call external tools via `script` type without subprocess overhead

**Dependencies:**
- Verify `policy_model.py` covers all enforcement levels and phase context
- Implement `BasePolicy` ABC with phase-aware evaluation

**Testing:**
- Unit tests for each built-in policy
- Integration tests with multi-stage deployments
- Compliance evidence export validation

---

### Issue #4: Deployment Manifest as First-Class Artifact
**Status:** Model exists, integration incomplete  
**Severity:** Medium  
**Related files:** `src/strata/models/deployment_manifest_model.py`, `src/strata/builders/`

**Description:**
Fully integrate deployment manifests into the build pipeline. Manifests capture exact infrastructure state at deploy time (Git commits, versions, timestamps, user, resource config) for compliance/audit evidence.

**Acceptance Criteria:**
- [ ] Deployment manifest automatically generated during `build run`
- [ ] Manifest includes:
  - [ ] Exact Git commit SHAs for all repositories
  - [ ] Version tags of all provisioners, modules, charts
  - [ ] Build timestamp, user identity, build environment
  - [ ] Full resource configuration (what was deployed)
  - [ ] Policy evaluation results
  - [ ] SBOM references
- [ ] Manifest stored alongside build artifacts (`.strata/build/<stage>/manifest.json`)
- [ ] `strata audit export` includes manifest in evidence package
- [ ] ISAE 3402 / NIS2 compliance template in manifest structure
- [ ] Manifest signed with optional GPG key (if configured)

**Testing:**
- Verify manifest completeness across single and multi-stage deployments
- Test compliance evidence export with real audit tools

---

## 🚀 Deployment & Infrastructure

### Issue #5: Implement Drift Detection (`strata diff show`)
**Status:** Model exists, CLI incomplete  
**Severity:** High  
**Related files:** `src/strata/commands/cli_status.py`, Terraform state integration

**Description:**
Implement `strata diff show` command to detect and report infrastructure drift (manual changes made outside strata).

**Acceptance Criteria:**
- [ ] `strata diff show --file deploy/deploy-prd.yaml` compares current Terraform state against last strata build
- [ ] Output shows:
  - [ ] Resources added manually (not in strata)
  - [ ] Resources removed (deleted outside strata)
  - [ ] Resources modified (field-level differences)
  - [ ] Configuration drift (strata config != actual state)
- [ ] Supports multiple provisioners (Terraform, Helm, Ansible)
- [ ] Optional: suggest remediation (e.g., "import into strata", "destroy manual resource")
- [ ] Integrates with policy engine (optional policy: `deny_drift`)
- [ ] Output formats: console (human), JSON (automation)

**Dependencies:**
- Terraform state parsing
- Helm release introspection
- Ansible inventory reconciliation

**Testing:**
- Test with manually-modified resources, deleted resources, updated configs

---

### Issue #6: Implement State Locking for Concurrent Deploy Prevention
**Status:** Code exists, enforcement incomplete  
**Severity:** Medium  
**Related files:** `src/strata/integrations/lock/`

**Description:**
Complete the state locking mechanism to prevent race conditions when multiple users/CI pipelines attempt to deploy to the same environment simultaneously.

**Acceptance Criteria:**
- [ ] Lock acquired at deploy start, released at deploy end (or error)
- [ ] Lock timeout configurable (default: 1 hour)
- [ ] Lock backends:
  - [ ] File-based (local, NFS)
  - [ ] Azure Blob Storage
  - [ ] HashiCorp Consul
  - [ ] etcd
- [ ] User receives clear error message if lock is held (includes lock holder info, age, timeout)
- [ ] Forced lock release with `strata deploy run --force-lock` (with confirmation)
- [ ] Audit trail records lock acquisitions/releases
- [ ] CI integration docs for GitHub Actions, Azure Pipelines

**Testing:**
- Concurrency tests with multiple processes
- Lock timeout tests
- Cross-backend compatibility tests

---

### Issue #7: Custom Script Provisioning Enhancement
**Status:** Code exists, documentation incomplete  
**Severity:** Low  
**Related files:** `src/strata/deployers/script_deployer.py`

**Description:**
Complete and document custom script provisioning for users who need provisioning outside Terraform/Helm/Ansible.

**Acceptance Criteria:**
- [ ] Script provisioner supports shell scripts, Python, Go, Node.js
- [ ] Environment variables injected at runtime:
  - [ ] `STRATA_PHASE` (e.g., `deploy_provision_before`)
  - [ ] `STRATA_WORKSPACE_PATH`, `STRATA_BUILD_PATH`, `STRATA_CONFIG_PATH`
  - [ ] All resolved secrets and variables
- [ ] Scripts run with working directory set to stage directory
- [ ] Exit codes: 0 success, non-zero fails deployment
- [ ] Script output captured in audit trail
- [ ] Pre/post hooks: `pre_provision`, `post_provision`, `pre_deploy`, `post_deploy`
- [ ] Documentation with examples (deployment hooks, custom validation, manual steps)

**Testing:**
- Cross-platform script execution (Windows, Linux, macOS)
- Environment variable injection verification
- Exit code handling

---

## 🔍 Observability & Diagnostics

### Issue #8: Implement CVE/Vulnerability Scanning Integration
**Status:** Code exists, incomplete  
**Severity:** Medium  
**Related files:** `src/strata/integrations/cve_scanner.py`, build pipeline

**Description:**
Fully integrate CVE scanning into the build pipeline to detect vulnerabilities in container images and dependencies.

**Acceptance Criteria:**
- [ ] `strata build run --scan-cves` scans all container images referenced in deployment
- [ ] Backends:
  - [ ] Trivy (local scanning, no external calls)
  - [ ] Docker Scout (requires Docker Hub account)
  - [ ] Snyk (requires Snyk account)
  - [ ] HashiCorp Vault (for image signing verification)
- [ ] SBOM integration: CVE scan results linked to SBOM inventory
- [ ] Enforcement levels: `deny` (fail build), `warn` (log), `audit` (record only)
- [ ] Report formats: console, JSON, SPDX, CycloneDX
- [ ] Remediation suggestions (update image tag, patch dependency)
- [ ] Exclude allowlist (false positives, accepted risks)
- [ ] Policy integration: `cve_max_severity` policy type

**Dependencies:**
- Trivy, Docker Scout, Snyk CLIs (optional, conditional installation)

**Testing:**
- Test with known CVE-containing images
- Verify exclusion/allowlist behavior

---

### Issue #9: SIEM Integration for Audit Log Forwarding
**Status:** Code structure exists  
**Severity:** Low  
**Related files:** `src/strata/integrations/siem/`

**Description:**
Implement SIEM integration to forward deployment audit logs to external monitoring/compliance systems (Splunk, Datadog, Azure Sentinel, etc.).

**Acceptance Criteria:**
- [ ] `strata audit export --siem <type>` sends logs to configured SIEM
- [ ] Supported SIEM backends:
  - [ ] Splunk HTTP Event Collector (HEC)
  - [ ] Datadog
  - [ ] Azure Sentinel / Log Analytics
  - [ ] Sumo Logic
  - [ ] Generic syslog/CEF
- [ ] Configurable via `.strata/cli.yaml` or environment variables
- [ ] Log format includes:
  - [ ] Deployment metadata (user, timestamp, stage, status)
  - [ ] Resource changes (added, removed, modified)
  - [ ] Policy evaluation results
  - [ ] Audit trail (who changed what, when)
- [ ] Secure credential handling (API keys, credentials stored in secret backend)
- [ ] Retry logic for transient failures
- [ ] Verification command: `strata tools check --siem` validates connectivity

**Testing:**
- Mock SIEM endpoints for integration tests
- Verify log format compliance with each SIEM's schema

---

## 🤖 AI & Automation

### Issue #10: MCP (Model Context Protocol) Server Implementation
**Status:** CLI exists  
**Severity:** Low  
**Related files:** `src/strata/commands/cli_mcp.py`, `src/strata/mcp/`

**Description:**
Implement strata as an MCP server to enable AI agents and tools to invoke strata commands programmatically.

**Acceptance Criteria:**
- [ ] MCP server listens on `stdio` or TCP socket (configurable)
- [ ] Exposes strata commands as MCP resources/tools:
  - [ ] `strata/validate` — validate file
  - [ ] `strata/build` — generate artifacts
  - [ ] `strata/deploy` — trigger deployment
  - [ ] `strata/audit` — query audit logs
  - [ ] `strata/schema` — introspect YAML schemas
- [ ] Authentication: API key or OAuth token-based
- [ ] Rate limiting and quota enforcement
- [ ] Full JSON response envelope compliance
- [ ] Claude, ChatGPT, and other LLM integrations via MCP spec
- [ ] Example: use case guide for AI-assisted deployments

**Dependencies:**
- MCP SDK (Python)
- Review existing `cli_mcp.py` for state of implementation

**Testing:**
- MCP compliance tests
- Integration with Claude via MCP plugin

---

## 📦 SBOM & Dependencies

### Issue #11: Custom SBOM Collector Plugins Documentation & Examples
**Status:** Code exists, docs minimal  
**Severity:** Low  
**Related files:** `src/strata/builders/sbom/`, `.strata/lockfile_parsers/`

**Description:**
Complete documentation and provide example implementations for custom SBOM collectors and lockfile parsers.

**Acceptance Criteria:**
- [ ] Documentation: "Extending strata SBOM — custom collectors and parsers"
- [ ] Example: custom Python lockfile parser (e.g., for private package manager)
- [ ] Example: custom collector for non-standard dependency file
- [ ] Drop-in location: `.strata/lockfile_parsers/*.py` (auto-registered, zero config)
- [ ] Drop-in location: `.strata/collectors/*.py` (declared in `collectors.yaml`)
- [ ] Base classes clearly documented: `BaseSbomCollector`, `LockfileParser`
- [ ] Lifecycle hooks: `pre_collect`, `post_collect`, filtering
- [ ] Testing patterns: how to test custom collectors locally

**Testing:**
- Verify auto-discovery of custom parsers
- Test end-to-end SBOM generation with custom collector

---

### Issue #12: SBOM Ignore Rules Implementation
**Status:** Model exists  
**Severity:** Low  
**Related files:** `.strata/sbom-ignore.yaml`

**Description:**
Implement ignore rules for SBOM dependency scanning (exclude false positives, accepted risks, dev dependencies).

**Acceptance Criteria:**
- [ ] `sbom-ignore.yaml` schema defined and validated
- [ ] Supported ignore rules:
  - [ ] Path patterns (glob)
  - [ ] Filename patterns (regex)
  - [ ] Package name patterns (glob)
  - [ ] CVE IDs (specific known CVEs accepted as risk)
  - [ ] Dependency type (dev, optional, etc.)
- [ ] Rules are documented in build artifact (what was scanned, what was ignored)
- [ ] Justification field for each rule (audit trail)
- [ ] Verification: `strata validate --sbom-ignore` checks for orphaned rules (package no longer found)
- [ ] Export: `strata audit export --siem` includes ignore rules in evidence

**Testing:**
- Test glob patterns, regex, CVE filtering
- Verify ignored dependencies are not in SBOM

---

## 🏗️ Architecture & Extensibility

### Issue #13: Pluggable Provisioner Framework
**Status:** Partial (Terraform, Helm, Ansible, Compose, Script exist)  
**Severity:** Low  
**Related files:** `src/strata/deployers/base_deployer.py`

**Description:**
Formalize and document the provisioner plugin architecture to allow third-party provisioners (Pulumi, CDK, ArgoCD, Flux, etc.).

**Acceptance Criteria:**
- [ ] `BaseProvisioner` ABC clearly documented with lifecycle methods
- [ ] Plugin discovery mechanism (auto-register from `.strata/provisioners/*.py`)
- [ ] Plugin manifest: `provisioner.yaml` (name, version, supported fields, health check command)
- [ ] Lifecycle hooks: `init`, `plan`, `apply`, `destroy`, `status`, `health`
- [ ] Context passed to provisioner: resolved values, secrets, stage metadata
- [ ] Example provisioner: Pulumi (IaC alternative to Terraform)
- [ ] Example provisioner: ArgoCD (GitOps alternative to Helm)
- [ ] Documentation: "Building a strata provisioner plugin"

**Testing:**
- Load and execute custom provisioner
- Plugin discovery and registration verification

---

### Issue #14: Enhanced Environment Composition & Inheritance
**Status:** Partial  
**Severity:** Low  
**Related files:** `src/strata/models/environment_model.py`, merge logic

**Description:**
Implement deeper environment composition with inheritance chains and conflict resolution.

**Acceptance Criteria:**
- [ ] Environments can extend other environments (`parent: base-env`)
- [ ] Layered merging: base → parent → environment → deployment overrides
- [ ] Conflict resolution strategy: explicit choice per field (last-wins, deep-merge, error)
- [ ] Array merging: append vs. replace semantics
- [ ] Merge documentation generated with `strata values list --trace` (show merge order)
- [ ] Validation: detect circular inheritance chains
- [ ] Performance: cache merged environments to avoid recomputation

**Testing:**
- Multi-level inheritance chains
- Conflict resolution correctness
- Circular dependency detection

---

## 📚 Documentation & Developer Experience

### Issue #15: Comprehensive Provisioner Orchestration Guide
**Status:** Not written  
**Severity:** Low  
**Related files:** `docs/`

**Description:**
Write a guide for orchestrating complex multi-provisioner deployments (Terraform → Helm → Ansible → custom script).

**Acceptance Criteria:**
- [ ] Tutorial: multi-stage deployment with dependency ordering
- [ ] Explain stage lifecycle: pre-hooks, provisioning, post-hooks
- [ ] Show how to pass artifacts between stages (e.g., Terraform outputs → Helm values)
- [ ] Troubleshooting multi-stage failures (which stage failed, logs, recovery)
- [ ] Example: 5-stage deployment (networking → compute → platform → apps → monitoring)
- [ ] Performance tips (parallel stages, caching, state backends)

**Related:**
- Deployment manifest format documentation
- Policy engine enforcement examples

---

### Issue #16: MCP Server Integration Guide for AI Agents
**Status:** Not written  
**Severity:** Low  
**Related files:** `docs/`

**Description:**
Write a guide for AI agents and LLMs on using strata via MCP for programmatic infrastructure management.

**Acceptance Criteria:**
- [ ] Explain MCP server setup and authentication
- [ ] Example: Claude deployment assistant (validate → build → deploy workflow)
- [ ] Example: GitHub Copilot extension for strata (inline validation in YAML)
- [ ] Security considerations (API keys, audit logging, approval workflows)
- [ ] Rate limits and quotas
- [ ] Use case: AI-assisted troubleshooting (AI analyzes drift, suggests fixes)

**Related:**
- MCP server implementation (Issue #10)

---

## 🧪 Testing & Quality

### Issue #17: Integration Test Suite for Multi-Repo Deployments
**Status:** Partial  
**Severity:** Medium  
**Related files:** `tests/strata/`

**Description:**
Expand integration tests to cover complex multi-repository scenarios with cross-repo references, drift, and failure recovery.

**Acceptance Criteria:**
- [ ] Test scenario: 3-repo setup (config, infrastructure, service configs)
- [ ] Test scenario: circular reference detection
- [ ] Test scenario: repo sync failures and recovery
- [ ] Test scenario: partial deployment failure and rollback
- [ ] Test scenario: concurrent deployments with state locking
- [ ] Test scenario: drift detection and remediation
- [ ] All tests include cleanup (no orphaned resources)
- [ ] CI integration (GitHub Actions, Azure Pipelines)

**Dependencies:**
- Mock or container-based test infrastructure
- Test fixture repositories

---

### Issue #18: Performance & Load Testing
**Status:** Not started  
**Severity:** Low  
**Related files:** `tests/`

**Description:**
Establish performance baselines and load tests for large-scale deployments.

**Acceptance Criteria:**
- [ ] Baseline: validate 100+ file workspace (target < 5s)
- [ ] Baseline: build artifact generation for 50-stage deployment (target < 30s)
- [ ] Load test: 10 concurrent deployments (verify state locking)
- [ ] Profile: memory usage for large SBOM scans
- [ ] Benchmark: secret resolution time across backends (Key Vault vs. local env vars)
- [ ] Report: performance regression detection in CI

**Testing:**
- `pytest-benchmark` or similar for benchmarking

---

## 📝 Summary

| Category                    | Count  | Priority |
| --------------------------- | ------ | -------- |
| User Experience             | 2      | High     |
| Compliance & Governance     | 3      | High     |
| Deployment & Infrastructure | 4      | High     |
| Observability               | 3      | Medium   |
| AI & Automation             | 1      | Low      |
| SBOM & Dependencies         | 2      | Low      |
| Architecture                | 2      | Low      |
| Documentation               | 2      | Low      |
| Testing                     | 2      | Medium   |
| **Total**                   | **21** | —        |

---

## Notes

- **High-priority items** are critical for compliance, user experience, and production readiness
- **Medium-priority items** enhance observability and testing
- **Low-priority items** extend ecosystem and documentation
- All items should be filed as separate GitHub issues with this document as context
- Consider grouping related issues for sprint planning (e.g., "Policy Engine Sprint", "Observability Sprint")
