# strata — Changelog

Concise, user-facing summary of each release. Full implementation detail (per-phase notes, file/method names, bug-fix specifics) lives in [HISTORY.md](./HISTORY.md). Design rationale lives in the ADRs under [docs/decisions/](../docs/decisions/).

This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

v2 is a ground-up rebuild of strata, evidence-driven from real v1 consumer usage rather than a straight port - see [docs/decisions/README.md](../docs/decisions/README.md) for the ADR index and [docs/design/README.md](../docs/design/README.md) for living design docs. Nothing has shipped yet; entries start accumulating here once the first release cuts.

### Added

- Security gates: `release_gate_policy` and `image_gate_policy` on the workspace, checked by `strata deploy run` before any step, backed by any `release_gate` / `image_findings` integration (Aikido Security first). New `strata policy list | check | findings` and `strata integration check` commands. `strata policy check` also evaluates `cve_policy` against the build's `sbom.json` and `checkov_policy` against its rendered sources; `strata policy findings` lists each CVE (allowlisted ones marked) or failed Checkov check (unrated ones marked). See [docs/how-to/security-gates.md](../docs/how-to/security-gates.md).
- Integration readiness: `strata build run` and every `strata deploy` command first check each enabled Integration (tool on PATH, `spec.version`); a `required: true` one that is not ready stops the run, others warn. `spec.version` is enforced (Terraform's `~>` accepted). New `strata integration list [--deployment D]`; `strata integration check` reports local readiness before the connection check and accepts a type name. `git`, `trivy`, `grype` and `checkov` are declarable integration types, so those tools can be required and version-pinned too.

### Changed

- `checkov_policy` now counts a failed check with no severity (what open-source Checkov reports without an API key) at any `severity_gate`. Before, such findings were never counted, so the gate could not fail; a `deny` policy with failed checks now fails `strata build run` and `strata policy check`. Use `enforcement: warn`, `skip_checks` or `include_checks` to relax it.
