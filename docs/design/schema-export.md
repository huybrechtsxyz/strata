# Schema export for AI and editors - Design

- Status: current
- Last updated: 2026-10-30

## Overview

`strata sln update` writes one JSON Schema per document kind, plus a `strata.json` umbrella that dispatches on
`kind:`, to `.strata/schemas/`. Two readers use them: the YAML editor (autocomplete, hover, inline errors) and an
AI asked "how do I model this?". Both are served by the same files. They are generated from the Pydantic models,
which stay the only source of truth: nothing in a schema is written by hand.

What a reader gets from a schema, beyond field names and types:

- the root `description` says what the kind is for, and `examples` holds working documents;
- a model's `description` can end with `Rules:`, the cross-field rules `strata validate` enforces;
- `x-strata-references: <kind>` marks a field holding the name of a document of that kind, and
  `x-strata-remote: true` one holding the name of a remote declared in `strata.yaml`;
- a field accepted but not used yet says so, so nobody builds on it;
- free-form blocks say what reads them and that an `attributeconfig` can give them a shape.

The agent skill (`.claude/skills/strata/`, [ADR-0046](../decisions/0046-strata-agent-skills-with-generated-marker.md))
draws on the same material: its kind table is generated from the root descriptions.

## Current Design

### Where each part comes from

| In the schema                            | Comes from                                                                                                                                                                                      |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Field descriptions, kind summaries       | The model's docstring and `Field(description=...)`.                                                                                                                                             |
| `x-strata-references`, `x-strata-remote` | `References`/`RemoteReference` in `models/reference_fields.py`, via `__get_pydantic_json_schema__`. Because they are the annotations the reference checker already walks, the two cannot drift. |
| `Rules:`                                 | The model's `schema_rules: ClassVar[tuple[str, ...]]`, one plain sentence per rule.                                                                                                             |
| `examples`                               | `models/schema_examples.py`, a registry from kind to valid documents. Python data, because the installed wheel does not ship `config/`.                                                         |
| What is dropped                          | A paragraph starting `Design notes:`, every `title`, and every `default: null`.                                                                                                                 |

The export is `models/schema_export.py`: `StrataJsonSchema` (a `GenerateJsonSchema` subclass), `render_schema()` for
a model, and `export_schema()` for a kind (adds `examples`). `ScaffoldController._export_schemas()` writes the files.
Nullable `anyOf` is kept: dropping it would make the editor flag a key with no value.

### Writing for the schema

- **Description:** user-facing, about the document the user writes. No ADR numbers, phases, v1 history, `docs/`
  paths, class names or source file names. Put maintainer rationale in the same docstring under a
  `Design notes:` paragraph; the exporter strips it, so nothing is lost.
- **Rules:** a model with an `after` validator that can raise lists each rule in `schema_rules`. Text that depends
  on code data can be computed in the class body, as `AttributeConfigTargetModel` does from `BLOCKS_BY_KIND`, so it
  cannot drift.
- **Free-form fields:** point at an `attributeconfig` targeting that `(kind, block)` where `BLOCKS_BY_KIND` allows
  it, and say where the value goes (for example the Terraform path `strata.resources.<name>.configuration`).
- **Claims about behaviour:** check a consumer exists before writing one. Where a field is accepted and validated
  but nothing reads it, the description says "does not use it yet". Find them with
  `grep -rn "yet" src/strata/models`. Today: `lifecycle` on provider, resource, module, namespace, workspace,
  deployment and integration; `ResourceSpecModel.dependencies`; `WorkspaceResourceModel.depends_on`;
  `ModuleSpecModel.files` and `.compose_file`; `ModuleReferenceModel.configuration`; `NamespaceType`; on a
  deployment, `locking`, `configurations` and every `stages[]` field except `timeouts`. Whoever builds one of these
  must update its description.

### What the editor shows

Verified by driving the installed YAML language server (not by reading its code):

- Hover shows the **field's** description. A referenced model's description is never shown when the field has its
  own. So a field that references a model with `Rules:` repeats them (`_repeat_rules_at_references`), through a
  direct reference, a nullable one and list items.
- Hover is headed by the nearest `title`. Generated titles are class names, so all are dropped, the umbrella's
  included.
- `description` survives next to a `$ref` in every draft the server supports.
- A model that also accepts a bare string (`scalar_shorthand_schema`) is wrapped in `anyOf`, which moves its
  docstring off the model. The exporter moves it back so the description and rules still show.

### Guards

| Test                                    | Fails when                                                                                                                                                                                                                   |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_schema_quality.py`, per kind      | A root description is a placeholder, a property has no description, a description mentions internals, a model with a raising validator has no `schema_rules` (exempt: `_NO_RULES`, with a reason), or a kind has no example. |
| `test_schema_export.py`                 | An example does not validate against its model or its own schema, a reference field is not marked, a title or `default: null` survives, notes are not stripped, or rules are not repeated at a referencing field.            |
| Drift tests in `test_schema_quality.py` | The audit event names, the provisioner `tool` list, the integration `type` catalogue and its capabilities, the capability list, or the store types that resolve through an integration disagree with the code.               |
| `test_scaffold_controller.py`           | The skill's kind table differs from the root descriptions, the "where does it go" map names a `kind.spec.field` that does not exist, a value-token kind is not explained, or an exported schema carries a title.             |

Not tested: the per-type `configuration` keys listed on the integration `configuration` description (webhook, otel,
sentinel, infisical, aikido), which come from what each class reads.

### The agent skill

`ScaffoldController._export_agent_skill()` fills `{{KIND_TABLE}}` in `templates/agent/strata/SKILL.md` with the first
sentence of each kind's root description (`first_sentence()`), so a root description must read well as one sentence.
The skill also holds a hand-written "where does it go" map, a "reading a schema" section and the five value-token
kinds (`var`, `secret`, `feature`, `output`, `value`); the map's field paths are checked against the models.

Out of scope: a `strata schema` command, JSON Schema `if`/`then` for rules, and field-level examples beyond the
roots; revisit if a real consumer needs one.

## Related Decisions

- [ADR-0053](../decisions/0053-schemas-are-the-ai-readable-modelling-reference.md) - the exported schemas are the
  AI-readable modelling reference, generated from the models
- [ADR-0046](../decisions/0046-strata-agent-skills-with-generated-marker.md) - the agent skill and its generated
  marker
- [ADR-0040](../decisions/0040-attributeconfig-standalone-kind.md) - how free-form blocks get a shape
- [solution-scaffolding.md](solution-scaffolding.md) - how `sln init` and `sln update` write these files

## History

- Rules are prose in `schema_rules`, not JSON Schema `if`/`then`. The conditional form is fragile across editors,
  cannot state uniqueness or cross-document existence, and reads badly as guidance. The schema explains; `strata
  validate` decides.
- Dropping generated titles and `default: null` saved 8% of the size, not the 16% first estimated, because the
  nullable `anyOf` has to stay. Size was never the main reason; the titles became a hover problem instead.
- Every description was checked against a consumer, and a large share was wrong: lifecycle hooks that never run, a
  `compose_file` pass-through that raises, a health check mapped to a Kubernetes probe the Helm renderer never
  emits, deployment stage fields nothing reads, `layers` called inert while it feeds the audit path, labels said to
  be applied that are only passed to Terraform, `TF_VAR_*` names after the projection moved to one `strata` object,
  and a ProviderConfig schema that `attributeconfig` had replaced. Describing from the code, not from the old text,
  is what made the schema trustworthy.
- A model's own description was assumed to show on hover. Testing the real language server showed it does not, which
  is why rules are repeated at each referencing field. The cost is about 9 KB (4%).
- The editor heads every hover with the nearest `title`; with class names as titles that read as internal noise and a
  doubled `X || X` on nullable fields. All titles are dropped, including the umbrella's.
- Scalar-shorthand models (`WorkspaceProviderModel`, `WorkspaceAssetModel`, `VersionPinModel`) lost their
  description to the `anyOf` wrapper and showed neither it nor their rules until the exporter moved it back.
- The first sentence of a root description feeds the skill's kind table, so it has to stand alone; the extractor
  skips full stops inside backticks (`strata.yaml`).
- The integration `type` description lists the 18 built-in types because an AI cannot discover the registry. A
  hand-kept list rots, so a test compares it with the registry and each class's capabilities.
- A test that mutated the examples registry and deleted a real kind's entry afterwards broke an unrelated test. Tests
  that touch the registry use `monkeypatch`.
- The quality bar was introduced as a per-kind ratchet, one kind at a time, so each phase could land with the suite
  green. With all 20 kinds passing the ratchet was deleted and the bar applies to every kind.
