# The exported JSON Schemas are the AI-readable modelling reference, generated from the models

- Status: accepted
- Date: 2026-10-30
- Related: [docs/design/schema-export.md](../design/schema-export.md) (how the export works),
  [ADR-0046](0046-strata-agent-skills-with-generated-marker.md) (the agent skill that draws on it),
  [ADR-0015](0015-solution-manifest-and-document-discovery.md) (references are `(kind, meta.name)`),
  [ADR-0040](0040-attributeconfig-standalone-kind.md) (how free-form blocks get a shape)

## Context and Problem Statement

`strata sln update` writes one JSON Schema per kind to `.strata/schemas/`, generated from the Pydantic models.
Two readers use them: the YAML editor, and an AI asked "how do I model this?". Audited 2026-10-30 across all 20
kinds (804 properties), the schemas served the editor adequately and the AI poorly:

- 98% of properties had a description, but nothing machine-readable said which kind a name field points at:
  `References(kind)` lives in the Python type and Pydantic drops it.
- About 98 cross-field validators enforced rules a schema reader could not see, for example `priority` only on
  MX and SRV DNS records, or a secret's `rotate` needing `generate`.
- No `examples`, and root descriptions read "Root model for a configuration file."
- 117 descriptions (14%) leaked maintainer detail: ADR numbers, "Phase 2", v1 history, `docs/` paths that do not
  exist in a user's repository, class names. An AI repeats these to users.
- Several descriptions were wrong. Checking each against the code found text that promised behaviour nothing
  implements (lifecycle hooks that never run, `compose_file` pass-through that raises, a health check mapped to a
  Kubernetes probe that Helm never renders, deployment stage fields nothing reads) and text naming mechanisms
  that no longer exist (`TF_VAR_custom`, a ProviderConfig schema that `attributeconfig` replaced).
- 89 free-form blocks said "not validated" without saying what reads them or that `attributeconfig` can type them.

The decision is how to close that gap without creating a second source of truth that drifts.

## Considered Options

- **Hand-write a modelling guide or a second, curated schema.** Readable, but it is a second copy of every kind's
  rules and goes stale with each model change, which is exactly the failure ADR-0046 documents for v1's skills.
- **Express cross-field rules as JSON Schema `if`/`then`.** Machine-checkable in principle, but fragile across
  editors, unable to state most of the rules (uniqueness, cross-document existence), and unreadable as guidance.
- **Scrape validator docstrings.** They hold maintainer rationale, not user guidance, and would leak it.
- **Keep the models the only source and enrich the export from them**, with declared annotations, a registry and
  tests that fail when a kind falls behind.

## Decision Outcome

Chosen: **enrich the export from the models**, because it keeps one source of truth and lets tests, not
discipline, hold the quality bar.

- **Reference targets** are emitted from the existing `References`/`RemoteReference` annotations as
  `x-strata-references: <kind>` and `x-strata-remote: true`, so they cannot drift from the reference checker.
- **Cross-field rules** are declared per model as `schema_rules`, one plain sentence each, and appended to the
  model's description under `Rules:`. A test requires them on every model with a raising `model_validator`.
- **Maintainer rationale** stays in the code, under a `Design notes:` paragraph the exporter strips. A test
  rejects internals (ADR numbers, phases, v1, `docs/` paths, class names) in any exported description.
- **Examples** live in one registry, `schema_examples.py`, exported as the root `examples`, and every example is
  validated against its model and its own schema.
- **Root descriptions are the kind catalogue.** The agent skill's kind table is generated from them, alongside a
  hand-written "where does it go" map and the five value-token kinds, both guarded by drift tests.
- **A description says when a field is accepted but not used yet**, so an AI does not recommend it.
- **Lists that restate code are tested against it**: the integration type catalogue against the registry, and the
  store types that resolve against the integrations' capabilities.
- **The editor is a first-class reader and was tested live**, driving the installed YAML language server. It
  showed the field's own description and never the referenced model's, so a model's `Rules:` were invisible on
  hover. A field that references a model with rules therefore repeats them. It also heads every hover with the
  nearest `title`, which here was a class name, so all generated titles are dropped.

### Consequences

- Good: an AI reading a schema gets what each kind is for, a working example, the rules `validate` enforces and
  which kind a name field points at, without anything the user cannot see.
- Good: a new model or validator cannot ship without a description, a stated rule and an example.
- Good: correcting the descriptions removed claims that would have sent an AI, and a person, to features that do
  not work.
- Bad: "not used yet" statements cannot be tested against behaviour. Whoever builds one of those features must
  update its description; the design doc lists them.
- Bad: repeating rules at each referencing field adds about 9 KB (4%) to the schemas.
- Bad: the integration `configuration` keys per type are listed from what each class reads and are not tested.
- Neutral: rules are prose, so `strata validate` remains the authority; the schema only explains it.
