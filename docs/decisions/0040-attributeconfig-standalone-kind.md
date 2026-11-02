# attributeconfig: a standalone kind for JSON Schema validation of passthrough blobs

- Status: accepted
- Date: 2026-10-15
- Related: [ADR-0014](0014-provider-topology-config-standalone-kinds.md)
  (provider/topology config standalone kinds — same "promote to its own
  kind" precedent this follows), [ADR-0041](0041-retire-providerconfig-embedded-schema.md)
  (retires the one prior mechanism this generalizes),
  [docs/design/dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)
  (full design rationale and how it works today — this ADR records only
  the decision itself)

## Context and Problem Statement

Every kind carries one or more loosely-typed `properties`/`custom`/
`configuration` passthrough blobs (`dict[str, Any] | None` fields) that
bypass Pydantic entirely — Pydantic never recurses into `Any`-typed dict
contents. Before this decision, exactly one such blob was shape-checked at
all: `resource.spec.configuration`, via a narrow mechanism embedded in
`providerconfig` (a regex pattern + required flag per field, an
allow/deny-unlisted-fields switch). Every other `properties`/`custom` blob
across roughly eighteen other kinds was completely unchecked. Real evidence
from this repo's own consumer fixtures showed 45+ documents already
populating `properties` with no validation whatsoever — a malformed value
there is accepted silently by `strata validate` and only surfaces as a
runtime failure far downstream (e.g. a malformed subject accepted by Entra
ID and by Terraform, only failing when a pod tries to obtain a token).

## Considered Options

- **Extend `providerconfig`'s existing bespoke mechanism to cover every
  blob on every kind.** Rejected — would mean reinventing nested
  structure, cross-field constraints, and arbitrary-key maps as new
  bespoke vocabulary (a `children`/`entries`/`key_pattern` design was
  explored and dropped), badly duplicating most of what JSON Schema
  already does, as a one-off dialect nobody outside this codebase would
  recognize.
- **A strata-native schema language**, embedded directly in each kind's
  own spec model. Rejected — `jsonschema` is already a shipped production
  dependency (via `cyclonedx-python-lib[json-validation]`, already used
  for real validation in `sbom_controller.py`); a bespoke dialect would
  cost real design/implementation effort for strictly less power than the
  standard already provides, and would force every author to learn a
  vocabulary that exists nowhere else.
- **Standard JSON Schema (2020-12), evaluated via the already-shipped
  `jsonschema` library, as a new standalone kind** declaring
  `{target: {kind, block, selector}, schema: <fragment>}`, scanning the
  document index directly for every document whose `kind`/`selector`
  match (push), rather than requiring the target document to opt in
  (pull). Chosen.
- **Pull model**: the target document explicitly lists which schema(s)
  apply to it, defaulting to unrestricted. Rejected — breaks the one
  property that makes this feature worth building: automatic,
  un-opt-outable enforcement. Every one of the 45+ pre-existing
  `properties` documents would stay completely unchecked until someone
  remembered to opt in — exactly the "forgettable governance" gap this
  feature exists to close.

## Decision Outcome

Chosen: a new standalone `attributeconfig` kind (same "promote to its own
kind" precedent as ADR-0014's `providerconfig`/`topologyconfig`), scanning
the document index directly (push) for every document whose `kind` matches
`target.kind` and whose fields satisfy `target.selector` (an optional,
equality-only `field.path: value` map — no wildcards/regex), then
evaluating `target.block`'s real value (`properties`/`custom`/
`configuration`, whichever the target kind actually has) against
`target.schema`, a verbatim JSON Schema 2020-12 fragment, with
`jsonschema`'s `Draft202012Validator`.

Two requirements shape how evaluation runs:

- **Non-retrieving registry (hard security requirement).** The validator
  is always constructed with an empty `referencing.Registry()` — no
  retriever, no pre-registered resources. `spec.schema` is untrusted,
  author-supplied content; a validator that resolves an unrecognized
  `$ref` by fetching it would let that content make strata itself issue an
  outbound HTTP request to an attacker-chosen URL the moment someone
  else's document is validated against it. An external `$ref` instead
  fails closed (`referencing.exceptions.Unresolvable`), reported as an
  invalid schema, never fetched.
- **No merge, conjunctive evaluation.** Every `attributeconfig` document
  whose target/selector match a document is evaluated independently, and
  all must pass — adding a schema can only ever tighten validation, never
  loosen it (a merge, by contrast, could let one schema silently weaken
  another). A load-time pass catches two schemas declaring the same
  top-level attribute with selectors whose match-sets can overlap
  (`attributeconfig_conflict`) — an authoring mistake, not a case for
  silent precedence. Since correctness now depends on the engineer getting
  selectors right, every `(document, schema)` application is traced via
  `log.info()` under `--verbose`, and a schema matching zero documents
  warns (`attributeconfig_unused`) — a silently-inert guardrail is
  otherwise indistinguishable from one that always passes.

## Consequences

- Good: every `properties`/`custom`/`configuration` blob on every kind can
  now be governed the same way, with a real, standard, widely-understood
  schema language — not only the one resource-configuration pair that was
  previously the sole guarded blob.
- Good: push-based, automatic enforcement — a schema protects every
  matching document, including ones that predate it, the moment it ships,
  with zero action required from whoever authored the target document.
- Good: strictly more expressive than the mechanism it generalizes (real
  types, nesting, cross-field `if`/`then`, `enum`/`format` support) with no
  new dependency, since `jsonschema` was already shipped.
- Bad: introduces a new SSRF-shaped attack surface (an arbitrary,
  author-supplied `$ref`) that has to be designed against explicitly —
  mitigated structurally by never using `jsonschema`'s retrieving default
  registry.
- Bad: the load-time duplicate-attribute check is shape-only (top-level
  `properties` keys, not recursive) — a conflict nested deeper inside two
  schemas' own subtrees is not detected. Deliberate: a subtree's closure
  (`additionalProperties`) is that subtree's own concern, where exactly
  one document was ever meant to own it.
- Neutral: some real invariants (cross-entry/cross-document checks, and
  anything genuinely derived from a provisioner's own source such as
  Terraform `variables.tf`) remain out of reach of a shape-only schema and
  stay bespoke/future work — not a regression, since no prior mechanism in
  this codebase could express them either.
