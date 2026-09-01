# Embedded String-Prefix Syntax — Inventory and Creep Prevention

- Status: proposed
- Date: 2026-09-01
- Related: [ADR 0070-Helm OCI Repositories and Value Substitution](./0070-helm-oci-repositories-and-value-substitution.md), [ADR 0072-Clarify spec.layers, spec.layering(s), and spec.paths](./0072-clarify-layering-vs-path-convention.md)

## Context and Problem Statement

While researching ADR 0072's `rules:` mechanism (`spec.<field>[*].<attr>` vs. a file-existence
path template, distinguished purely by regex-matching the rule string's own shape — no
`type:`/`kind:` discriminator field), a broader pattern became visible: strata has
accumulated a growing number of **independent, uncoordinated conventions where a plain
YAML string's own shape — a prefix, a delimiter, a small embedded expression — decides how
it gets interpreted**, each invented separately for one specific feature.

None of these are individually wrong — each solves a real, narrow problem, and several
(`@repo_name/...`, `strata://`) are already documented, deliberate, load-bearing
conventions. The concern is the *trend*: every new feature is one uncoordinated decision
away from inventing its own embedded mini-syntax instead of asking "does an existing
convention already cover this, and if not, should this really be a string-shape trick at
all?" Two concrete costs already surfaced in this codebase from that lack of coordination:

1. **Cognitive load compounds silently.** A DevOps author authoring strata YAML doesn't
   read one schema — they implicitly need to know that `@` means cross-repo, `strata://`
   means a structural URI, `${VAR}` means a Helm secret/variable/feature substitution
   token, `spec.x[*].y` means a config membership lookup, and so on — each learned
   ad hoc, from a different doc page or from trial and error, with no single place that
   says "these are all the special string shapes strata recognizes."
2. **Collision risk is real, not theoretical.** `${VAR_NAME}` (ADR 0070's Helm value
   substitution, [`helm_deployer.py`](../../src/strata/deployers/helm_deployer.py#L99))
   deliberately avoids `{{ VAR_NAME }}` specifically *because* `{{ }}` is already Helm's
   own Go-template/Sprig delimiter, and off-the-shelf charts can legitimately contain
   literal `{{ ... }}` text in their default values (see the code comment added directly
   above `_TOKEN_RE` as a result of this research). That near-collision was caught this
   time. Nothing currently prevents the next one.

Also directly related: the `@repo_name/...` convention itself — while a legitimate,
deliberate, documented convention — is independently re-detected via raw
`str.startswith("@")` at roughly 15 call sites across the codebase instead of one shared
predicate. That is tracked as a separate bug/tech-debt item (not this ADR — see
[Remaining Work](#remaining-work)), but it is the same underlying symptom: a string-shape
convention that exists without one canonical, discoverable definition.

**This ADR does not propose fixing or unifying any of this now.** It exists so the
inventory and the concern are written down before they're forgotten, and so any future
feature that's tempted to add "just one more" prefix has something to check against
first.

## Inventory — every embedded string-shape convention found in the codebase today

**Reference markers** — the string names an external thing to resolve:

| Shape                                                | Meaning                                                         | Where                                                                                                                                                              |
| ---------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `@repo_name/...`                                     | Cross-repo file reference                                       | [`resolve_path()`](../../src/strata/utils/system.py#L203) + ~15 independent re-detections (separate tech-debt item)                                                |
| `strata://<kind>/<name>[/<child-kind>/<child-name>]` | Durable structural URI to a workspace object (ADR-0034)         | [`strata_uri.py`](../../src/strata/utils/strata_uri.py)                                                                                                            |
| `file://...`                                         | Local-file-based Helm chart repo vs. a remote repo URL          | [`helm_chart_file_collector.py`](../../src/strata/builders/sbom/helm_chart_file_collector.py#L129)                                                                 |
| `git@...` / `scheme://...`                           | Git remote URL vs. local filesystem path                        | [`add_repo_solution_command.py`](../../src/strata/commands/repo/add_repo_solution_command.py#L15)                                                                  |
| `./` / `../`                                         | Local relative module source vs. registry/git-hosted reference  | [`sbom_utils.py`](../../src/strata/utils/sbom_utils.py#L146), [`terraform_module_collector.py`](../../src/strata/builders/sbom/terraform_module_collector.py#L127) |
| `spec.<field>[*].<attr>`                             | Config-model membership lookup vs. file-existence path template | [`path_convention.py`](../../src/strata/utils/path_convention.py#L73) — the mechanism ADR 0072 documents                                                           |

**Embedded mini-expression syntax** — a small language inside the string:

| Shape                                   | Meaning                                                                  | Where                                                                                                                                      |
| --------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `${VAR_NAME}`                           | Helm values substitution token (secrets/variables/features)              | [`helm_deployer.py`](../../src/strata/deployers/helm_deployer.py#L99)                                                                      |
| `{{ var }}`                             | Jinja2 variable (unrelated domain — `strata new --template` scaffolding) | [`template_resolver.py`](../../src/strata/services/template_resolver.py#L24)                                                               |
| `>=`, `<=`, `==`, `!=`, `>`, `<` prefix | Comparison expression (cost/threshold gates)                             | [`gate_controller.py`](../../src/strata/controllers/gate_controller.py#L57)                                                                |
| `field op value`                        | Diagram conditional expression                                           | [`diagram_expressions.py`](../../src/strata/utils/diagram_expressions.py#L24)                                                              |
| `{segment}`                             | Path-convention placeholder                                              | [`path_convention.py`](../../src/strata/utils/path_convention.py#L126)                                                                     |
| version-string shape                    | semver / git SHA / OCI digest detection                                  | [`sbom_utils.py`](../../src/strata/utils/sbom_utils.py#L18), [`version_service.py`](../../src/strata/services/version_service.py#L322-323) |

**Not strata's own convention** (foreign file formats — excluded from concern here):
`ref:` in [`manifest_artifact_collector.py`](../../src/strata/services/manifest_artifact_collector.py#L82)
reads real git plumbing (`.git/HEAD` symbolic-ref format), not a strata-authored marker.

## Goal

Not to unify or redesign any of the above right now. Only:

1. **One place these are all written down**, so nobody has to rediscover the full list by
   grepping the codebase the way this research did.
2. **A principle for future additions**: before inventing a new embedded string-shape
   convention for a narrow use case, check (a) whether an existing convention above
   already covers the need, and (b) whether a real schema field (an explicit `type:`/
   `kind:` discriminator) would serve better than another shape-sniffed string — a schema
   field is discoverable, validated, and documented by the model itself; a string-shape
   convention is not.
3. **A trigger to actually research consolidation later** — see
   [Remaining Work](#remaining-work) — rather than letting this fade back into "13
   independent tricks nobody wrote down."

## Decision Outcome

No mechanism change is adopted by this ADR. Existing conventions are grandfathered
as-is — none of them are required to change because of this document. This ADR's only
concrete output is the inventory above and the open research questions below, so the
concern is tracked instead of forgotten.

## Remaining Work

<!-- Required while Status is proposed / in-progress / partially-implemented.
     Remove this section once Status becomes implemented. -->

- Decide whether new embedded string-shape conventions should require an explicit
  justification (e.g. a short section in the introducing ADR/PR) that checks this
  inventory first — not yet decided whether to formalize this as a written rule anywhere
  (CONTRIBUTING, an instructions file, or just convention).
- Research whether any of the existing conventions above could be consolidated or made
  more consistent (e.g. `rules:`'s spec-vs-file dispatch in `path_convention.py` — see
  ADR 0072 — reusing the same "no explicit discriminator" shape as several others listed
  here). Not decided; purely a future research question.
- Track and resolve the `@repo_name/...` duplication (~15 independent
  `str.startswith("@")` sites instead of one shared predicate) as its own bug/tech-debt
  item — related to this ADR's concern but intentionally scoped out of it.
- No implementation has started; this ADR is inventory + a flagged concern, not a design.
