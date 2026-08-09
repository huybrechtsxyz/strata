# 2026-08-06 — Configurable tenant path documentation coverage

**Documented feature:** Option B tenant path resolution (custom `spec.paths` convention with `resolves: tenant` field)

## Decision

Split documentation across four files rather than centralizing in one place:

1. **CHANGELOG.md** — Terse user-facing summary (1 line capability, 1 line fallback note)
2. **HISTORY.md** — Technical deep-dive (model, validators, utility functions, call sites, test count)
3. **configuration.md** — New subsection `Custom tenant file location` with YAML example and convention rules
4. **tenant.md** — Minimal cross-reference (2 lines pointing readers to configuration.md)

## Rationale

- **Audience split:** Users scanning CHANGELOG want "what changed"; engineers building deployments need the YAML pattern (configuration.md); tenant file readers just need to know the location is customizable without reading the full convention rules
- **DRY compliance:** Avoid duplicating the convention rules and example across tenant.md + configuration.md; tenant.md links out instead
- **Backward-compat clarity:** Both HISTORY.md and the docs explicitly state fallback behavior (default `tenants/{code}.yaml` when no convention declared)

## Files changed

- `.github/CHANGELOG.md` — Added 1 bullet to `### Added`
- `.github/HISTORY.md` — Added 1 detailed bullet to new `### Added` subsection (Breaking Changes left untouched)
- `docs/config/configuration.md` — Inserted "Custom tenant file location" subsection with example + rules
- `docs/config/tenant.md` — Added 1 clarifying sentence under `spec.code` field section

## Notes

- Conventions entry style matches existing bullet structure exactly
- No rewrites to either file; only targeted insertions
- Cross-reference pattern (tenant.md → configuration.md) is consistent with how other config docs reference each other
