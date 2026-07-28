# strata — Global Review: Items to Look At

Pass 1 of a top-to-bottom audit (concept, design, implementation, use) — same shape as
the from-scratch redesign review referenced in chat. This pass only **collects** items
worth looking at. Nothing here has been judged yet.

Next pass: go through each row and fill in **Verdict** with 🟢 Good / 🟡 Meh / 🔴 Ugly,
plus a one-line reason. Update this file in place as we go — don't create a second file.

Legend: 🟢 Good · 🟡 Meh · 🔴 Ugly · ⏳ Not yet reviewed

---

## Concept

| # | Item | Verdict | Notes |
|---|------|---------|-------|
| C1 | 27 of 50 indexed ADRs (54%) are still "Proposed" — design velocity is outpacing implementation. Is that fine (a backlog of ideas) or a problem (nothing ever ships)? | ⏳ | |
| C2 | Four mechanisms all gate/block a deploy: ADR-0057 (gates/WorkItem), ADR-0032 (approval workflows), the policy engine (ADR-0006), and the newly-flagged `spec.requires` idea. Do these compose cleanly or overlap awkwardly? | ⏳ | |
| C3 | ADR-0018 (audit traceability) was found to be only "partial" in practice — the audit enrichment/push/SIEM pipeline isn't fully wired end-to-end despite the ADR's status. Pattern: ADRs describing more than what's actually built. | ⏳ | |
| C4 | At 58 ADRs and a growing `kind:` catalog (deployment, workspace, configuration, environment, module, resource, provider, firewall, network, dns, tenant, ...) — is "everything is YAML + Pydantic + one ADR per feature" still a coherent mental model, or has the schema surface sprawled past what's holdable in one head? | ⏳ | |
| C5 | ADR-0044's own gap-analysis table flags dependency graph/parallel execution and drift detection as "High" priority gaps vs. competitors (Terragrunt, Spacelift, Atmos). Still the top two, or have priorities shifted? | ⏳ | |
| C6 | Two overlapping "is it deployed" surfaces exist simultaneously: `strata deploy status` (deprecated) and `strata env status`/`env output` (the real one). Confusing for docs and for users — should the deprecated one be removed outright? | ⏳ | |

---

## Design (architecture & consistency)

| # | Item | Verdict | Notes |
|---|------|---------|-------|
| D1 | `deploy_log_path` has zero remote-backend support, while locks (ADR-0007) reuse the Terraform backend config and work items (ADR-0057) have pluggable S3/azblob/gcs backends. Inconsistent "remote-capable" maturity across subsystems that conceptually should behave alike. | ⏳ | |
| D2 | Subprocess execution is duplicated across at least three call sites — `run_command()`, `script_deployer.py`'s direct `subprocess.run`, and the `format=script` builders. Timeout/error handling isn't centralized, so each site can drift independently. | ⏳ | |
| D3 | `run_command()` has no stdin-injection support today — any feature needing to pipe input to a subprocess (e.g. a secret-transform hook) has no clean path without adding it. | ⏳ | |
| D4 | Most integrations follow a consistent `BaseIntegration`/`StoreIntegration` + `capabilities.py` Protocol pattern, but a few (`opa.py`, `cve_scanner.py`, `infracost.py`) sit directly under `BaseIntegration` with scanner/cost-specific protocols instead. Intentional split, or drift? | ⏳ | |
| D5 | Exit-code contract (`has_lock_conflict` / `has_hand_off_required`) is checked via `hasattr` duck-typing in `handle_command_exit`, not a shared Protocol/ABC. Only `deploy run`/`deploy destroy` implement it today — does any other command silently need exit 4/5 but lack the hook? | ⏳ | |
| D6 | `commands → controllers → services` layering (ADR-0003): 31 top-level `cli_*.py` files + ~29 command subpackages vs. only 24 files in `controllers/`. Spot-check whether business logic has leaked into commands that should live in a controller/service. | ⏳ | |
| D7 | Mixed command implementation styles within one CLI group — e.g. `cli_secret.py` mixes plain-function commands (`generate`, `mask`) with `BaseCommand`-subclass commands (`get`, `put`, `rotate`, ...). Legacy-migration leftover, or deliberate? Worth checking other `cli_*.py` files for the same split. | ⏳ | |

---

## Implementation

| # | Item | Verdict | Notes |
|---|------|---------|-------|
| I1 | Near-duplicate collector functions bug (fixed 2026-07-28): `_collect_available_templates` and `_collect_templates_with_descriptions` in `run_new_command.py` each independently forgot about solution-level templates. General code smell — worth a sweep for other "almost the same" function pairs across `commands/`, `controllers/`, `services/`. | ⏳ | Fixed instance: `strata new --list` bug (commit `5f221155`) |
| I2 | Same "not valid on builtin store" validator logic is repeated independently 4 times in `store_models.py` (`validate_generate_not_on_builtin`, `validate_rotate_not_on_builtin`, and the Variable/Feature `validate_default_not_on_builtin` equivalents) — candidate for a shared reusable validator. | ⏳ | |
| I3 | `ctx.exit(0)` is called directly in `cli_promote.py` instead of going through the shared `handle_command_exit` helper — an inconsistent exit-path pattern; worth checking for other bypasses. | ⏳ | |
| I4 | ADR-0020 (CLI parameter consistency across 80+ subcommands) — worth spot-checking a random sample of `cli_*.py` files for option-order/name drift since that standard was written. | ⏳ | |
| I5 | Exit code discipline (ADR-0004: 0/1/2/3, +4 lock conflict, +5 hand-off) — only the `deploy` command family appears wired for 4/5. Confirm every other command consistently returns only 0/1/2/3 and doesn't silently need the extended codes. | ⏳ | |
| I6 | `redact_argv()` exists and is wired into `base_command.py` and `cli.py` to mask sensitive option values (`--value`, `--password`, etc.) before audit-log/console echo — worth confirming coverage is complete across *all* argv-echoing call sites (e.g. lifecycle script logging), not just the three known spots. | ⏳ | Related fix already shipped this session — this item is about coverage completeness beyond it |
| I7 | Doc cross-references (e.g. in `output_deploy_command.py`) still point users toward `deploy status` as a valid companion to `deploy run`, even though it's deprecated — stale doc pointer alongside the deprecation. | ⏳ | |

---

## Testing & Quality

| # | Item | Verdict | Notes |
|---|------|---------|-------|
| T1 | `tests/strata/commands/secret/` has **zero** dedicated test files. Of the flat `test_commands_secret.py`/`test_cli_secret.py`, only `generate` and `mask` are covered — `put`, `get`, `rotate`, `status`, and `list` have no direct unit test references at all. Sensitive functionality (secrets) with a real coverage gap. | ⏳ | |
| T2 | No `pytest`/coverage step exists in `scripts/Check.ps1` at all — only ruff lint/format, mypy, a CLI smoke test, docs-index coverage, and a Sphinx build. The full test suite isn't gated in CI, so gaps like T1 can persist invisibly. | ⏳ | |
| T3 | `src/strata/commands/` has ~35 subdirectories/CLI groups vs. a flatter `tests/strata/commands/` layout — structural parity between src and tests can't be assumed; needs a file-by-file audit (secret, ref, promote, vars, guide, mcp flagged as thin). | ⏳ | |
| T4 | 14+ policy validator test files are entirely skipped via `pytest.mark.skipif(IMPL_MISSING, ...)` for policies "not yet implemented" — a large block of intentionally-inert tests; worth reconciling against actual implementation status in `validators/policies/`. | ⏳ | |
| T5 | Mypy's `--check-untyped-defs` note recurs across several test files AND one real policy file (`cve_max_severity_policy.py`) — untyped function bodies are silently unchecked, hiding potential type bugs even outside of tests. | ⏳ | |

---

## Docs & Usability

| # | Item | Verdict | Notes |
|---|------|---------|-------|
| X1 | `docs/decisions/README.md`'s ADR index table is stale — ends at ADR 0048, missing rows for 0049–0058 (10 ADRs), despite being the doc whose entire job is to be the canonical index. | ⏳ | |
| X2 | `docs/guides/at-scale.md`'s "Status: Design draft — not yet implemented" is a single blockquote line at the top with no visual distinction from fully-shipped guides — easy to skim past and mistake for a real, usable feature. Unclear how many of the other 29 guides need the same marker but don't have it. | ⏳ | |
| X3 | 30 files in `docs/guides/` vs. 58 in `docs/decisions/` cover overlapping ground with no cross-linking convention (e.g. `deployment-manifests.md` guide vs. ADR-0021; `how-deployment-locking-works.md` guide vs. ADR-0007) — unclear which is "current truth" for a given topic. | ⏳ | |
| X4 | `docs/INDEX.md` and `docs/index.rst` are two separately hand-maintained lists of "what docs exist" (curated index vs. Sphinx toctree) with no automated check tying them together — can silently drift apart. | ⏳ | |
| X5 | Onboarding entry point is fragmented — README.md / `docs/INDEX.md` / `docs/platform/getting-started.md` cross-link each other, but `docs/skills/strata-onboarding.md` exists in **three** separate repo locations (`docs/skills/`, `.github/skills/`, `src/strata/templates/solution/dot.github/skills/`) and isn't referenced from that main chain at all. | ⏳ | |

---

## Already fixed / notable this session (context, not open items)

- Audit log was leaking unredacted secret values via full `sys.argv` logging — fixed same-day (2026-07-28) via `redact_argv()`. See I6 above for the "is coverage complete everywhere" follow-up.
- `strata new --list` didn't show solution-level templates — fixed same-day (commit `5f221155`). See I1 above for the general code-smell follow-up.
- ADR-0058 (cross-deployment dependency gating) filed as a proposed design, not yet implemented.
