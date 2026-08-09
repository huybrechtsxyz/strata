# Session Log — Changelog: Infracost Declaration Gating

**Timestamp:** 2026-08-06T15:00:00Z

Documented the cost-estimation gating change in the `## [Unreleased]` section of both
`.github/CHANGELOG.md` and `.github/HISTORY.md`: `strata cost show`, `strata cost diff`,
and the automatic post-plan cost diff in `deploy run --dry-run` now require an explicit
`infracost` entry under `spec.integrations` (with `capabilities: [cost]`, `enabled: true`)
instead of working off any `infracost` binary found on PATH. No backward compatibility —
an installed-but-undeclared binary now does nothing. `strata cost history` is unaffected.

Existing ADR-0063 `### Added` entries in `[Unreleased]` were left untouched. Reuben
(Docs/Technical Writer) did the work; Scribe merged the decision record and logged this
session.
