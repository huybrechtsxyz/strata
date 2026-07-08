### 2026-06-23: ADR 0011 review findings (Danny)

**By:** Danny (architecture review)
**What:** Rubber-duck review of ADR 0011 — Promotion strategies for version progression.

**Verdict:** Design is architecturally sound and ready for implementation with 3 items requiring clarification before Phase 3 work begins.

**Required clarifications (non-blocking for Phase 1/2, blocking for Phase 3):**

1. **Promotion override file discovery:** The ADR proposes creating per-deployment override files (`environments/customers/{name}-promotion-override.yaml`) during wave 1, then removing them in the final wave. However, the existing deployment model requires explicit `spec.environments` file paths — files aren't auto-discovered. The ADR must specify: does `strata promote start` also PATCH the deployment YAML to append the override file to `spec.environments`? Or is there a glob/auto-include mechanism planned? This is the single biggest mechanical gap.

2. **`scope: customer` definition:** The ADR says `scope: customer` means "only customer-layer deployments are waved." But what makes a deployment "customer-layer" mechanically? Candidate definitions: `spec.customer != null`, a specific `spec.layers` value, or a label match. Must be machine-resolvable — the promotion controller needs a filter predicate.

3. **Percentage waves table is misleading:** Key Observations table shows `[10, 50, 100]` as "Percentage waves" — this implies auto-selection of N% of deployments. The resolved design (Open Question 2) explicitly says NO auto-selection. Either remove the row, rename it to "Multi-wave (3 iterations)", or add a note clarifying the numbers are wave COUNT not percentages.

**Non-blocking observations (advisory):**

4. The `strata promote log` subcommand reads the activity log from `.strata/promotions/`. Since this is gitignored and local-only, it won't work in CI or on a colleague's machine. This is fine for Phase 3 (stated as diagnostic) but worth a doc note.

5. The `--id prom-20260623-001` on `strata promote rollback` implies a naming scheme but the ADR doesn't define the ID generation algorithm. Minor — can be resolved during implementation.

**Approved for:** Phase 1 (read-only visibility) and Phase 2 (strategy model + validation) can proceed without these clarifications. Phase 3 automation is blocked on items 1 and 2.
