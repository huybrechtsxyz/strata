# strata - Changelog

Concise, user-facing summary of each release. Design rationale lives in the ADRs under [docs/decisions/](../docs/decisions/); the current shape of each feature lives in [docs/design/](../docs/design/).

This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- `${deployment:name}` and `${deployment:tenant}` value tokens: the name and tenant of the deployment being run, so a
  workspace's Terraform backend can build every deployment's state key from one rule
  (`key: ${deployment:name}.tfstate`) instead of a per-deployment variable. `${deployment:tenant}` on a deployment
  without a tenant is an error, reported by `strata validate` and by `deploy run`.

## [2.0.0] - 2026-11-02

v2 is a ground-up rebuild of strata, not an upgrade of v1: documents use `apiVersion: strata.huybrechts.xyz/v2` and the CLI surface is new. Every feature was built from what v1 and real consumer pipelines actually do. See [docs/decisions/README.md](../docs/decisions/README.md) for the ADR index.

### Document model

- Twenty document kinds, each with an exported JSON Schema (`.strata/schemas/`) that editors and AI agents can read: `solution`, `configuration`, `provider`, `providerconfig`, `attributeconfig`, `topologyconfig`, `resource`, `network`, `firewall`, `dns`, `module`, `namespace`, `workspace`, `version`, `integration`, `tenant`, `deployment`, `environment`, `artifact` and `asset`.
- Documents reference each other by `(kind, name)`, never by file path. `strata.yaml` marks the solution root and documents are discovered from there.
- A deployment composes a workspace, an environment and a tenant, supports `extends` chains, and can rebind provider and asset slots per deployment.
- `attributeconfig` enforces a JSON Schema fragment against matching documents, or against a deployment's merged values.
- `${var:}`, `${secret:}` and `${feature:}` tokens resolve against the environment.

### Commands

- `strata validate`: schema checks plus cross-document checks (unresolved variables, missing modules, dangling pins, topology references).
- `strata build run`: renders a deployment's provisioners into Terraform, Compose or Helm output on disk. It executes nothing.
- `strata deploy run`: executes plan, apply, or an upgrade against the build output with fully resolved values; `deploy setup | check | plan | apply` run the phases on their own. `--dry-run` runs a real plan preview, `--smoke-test` checks wiring without contacting anything, and `--follow` streams the tool's output with secrets redacted (also as NDJSON).
- `strata deploy list` and `strata deploy images`: list deployments and the images each one runs.
- `strata version new | set | update`: pins for artifacts, remotes, images, charts and assets; `update` lists the candidates.
- `strata promote status | apply | view`: ring, order and wave rollout across versions.
- `strata policy list | check | findings` and `strata integration list | check`.
- `strata values`, `strata graph`, `strata path`, `strata audit` and `strata sln init | update`.
- Structured JSON output and stable exit codes on every command, so pipelines and agents can drive it.

### Guard rails

- Security gates: `release_gate_policy` and `image_gate_policy` on the workspace, checked by `strata deploy run` before any step, backed by any `release_gate` / `image_findings` integration (Aikido Security first). See [docs/how-to/security-gates.md](../docs/how-to/security-gates.md).
- `cve_policy` judges the build's `sbom.json` and `checkov_policy` judges the rendered sources, both through `strata policy check`. A failed Checkov check without a severity counts at any `severity_gate`.
- `image_tag_policy`: opt-in rules on the tag of every image a deployment runs (no tag, floating tag such as `latest`, tag pattern, required pin, per-ring rules, an allow list with expiry). It is judged offline by `build run`, `deploy run`, `deploy images` and `policy check`.
- Integration readiness: every command first checks each enabled integration (tool on PATH, `spec.version`); a `required: true` integration that is not ready stops the run. `git`, `trivy`, `grype` and `checkov` are declarable integration types.

### Integrations

- Terraform, Compose and Helm provisioners; private Helm and OCI chart registries through a `remote` integration.
- Artifacts (a pinnable container image reference) and assets (templated input files, optionally from git) flow into the rendered output.
- A VS Code extension, published on the Marketplace listing `huybrechts-xyz.xyz-strata`, with views for the document graph, integrations and policies.

### Known limits

- Helm modules do not receive the declared service image in the chart values, so `image_tag_policy` judges the declared image of a Helm module but cannot see an image that only the chart knows.
- Deployment stage `gates` are not available yet.

[Unreleased]: https://github.com/huybrechtsxyz/strata/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/huybrechtsxyz/strata/releases/tag/v2.0.0
