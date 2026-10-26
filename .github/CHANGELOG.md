# strata — Changelog

Concise, user-facing summary of each release. Full implementation detail (per-phase notes, file/method names, bug-fix specifics) lives in [HISTORY.md](./HISTORY.md). Design rationale lives in the ADRs under [docs/decisions/](../docs/decisions/).

This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

v2 is a ground-up rebuild of strata, evidence-driven from real v1 consumer usage rather than a straight port - see [docs/decisions/README.md](../docs/decisions/README.md) for the ADR index and [docs/design/README.md](../docs/design/README.md) for living design docs. Nothing has shipped yet; entries start accumulating here once the first release cuts.

### Added

- Security gates: `release_gate_policy` and `image_gate_policy` on the workspace, checked by `strata deploy run` before any step, backed by any `release_gate` / `image_findings` integration (Aikido Security first). New `strata policy list | check | findings` and `strata integration check` commands. See [docs/how-to/security-gates.md](../docs/how-to/security-gates.md).
