### 2026-07-11: ADR 0011 naming — keep "promotion", drop "unpromote" as a CLI verb (Danny)

**By:** Danny (architecture review / naming gut-check)
**Requested by:** Vincent Huybrechts

**What:** Keep **"promotion" / `strata promote`** as the name for the ADR-0011 concept (advancing a version-lock through ordered rings dev→test→qas→prd). Do **not** re-litigate the noun — it is the correct industry-standard term.

**Why keep "promotion":**
- Dominant term across GitOps/CD tooling (Argo, Spinnaker, Octopus, GitLab environments all "promote"). Zero learning curve for ops.
- Verb form reads cleanly as a command: `strata promote start`, `strata promote status`.
- Aligns with the ring-based progression mental model and the version-lock "advance the lock" framing.
- No collision with existing strata nouns (build, deploy, release, ref, lock) — it earns its own command group.

**The one change to make — the reverse direction:**
- Keep **"unpromotion"** as the *conceptual noun* in the ADR prose (it's precise and unambiguous in writing).
- But the **CLI verb must be `strata promote rollback`**, NOT `strata promote unpromote`. `unpromote` is not an industry term, reads awkwardly, and `rollback` is already the reverse-vocabulary strata uses in the deploy surface. ADR 0011's own examples already say `strata promote rollback` — make that authoritative and ban `unpromote` from the CLI.
- Rule: reverse operations follow user-facing vocabulary, not linguistic symmetry with the forward verb.

**Rejected alternatives:** advance/advancement (weaker, generic), rollout (collides with k8s rolling-update meaning), propagate (unfamiliar in this context), release-progression (verbose, and "release" already means the ADR-0017 tagging lifecycle — reusing it here muddies two distinct concepts).

**Action for ADR owner:** No structural change. Ensure every CLI example uses `promote` (forward) and `promote rollback` (reverse); keep "unpromotion" only as descriptive prose, never as a command.
