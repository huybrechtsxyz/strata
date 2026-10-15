# AttributeConfig (`kind: attributeconfig`)

Declares a real [JSON Schema](https://json-schema.org/) (2020-12) fragment that must validate
against a targeted `properties`/`custom`/`configuration` block of every document matching
`spec.target` — a platform-authored shape guardrail for the loosely-typed passthrough blobs
every kind carries, enforced automatically against every matching document the moment it ships
(no opt-in required on the target document's side).

See [docs/design/dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)
for the full design rationale.

## Schema

- `spec.target.kind` — which [`PlatformKind`](../GLOSSARY.md) this schema targets (e.g. `resource`)
- `spec.target.block` — which blob field on that kind to validate: `properties`, `custom`, or
  `configuration` (whichever the target kind actually has)
- `spec.target.selector` — optional `field.path: required-value` equality map narrowing which
  documents of `target.kind` this schema applies to. Omitted means every document of that kind.
  Equality only — no wildcards or regex.
- `spec.schema` — a verbatim JSON Schema fragment (`type`, `properties`, `required`,
  `additionalProperties`, `patternProperties`, `if`/`then`, ...), authored directly — no
  strata-invented vocabulary.

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: azure-storage-account-configuration
spec:
  target:
    kind: resource
    block: configuration
    selector:
      spec.provider_type: azure
      spec.resource_type: storage_account
  schema:
    type: object
    required: [account_tier]
    additionalProperties: false
    properties:
      account_tier:
        type: string
        pattern: "^(Standard|Premium)$"
```

## Notes

- **No merge.** Every `attributeconfig` document whose `target` and `selector` match a document
  is evaluated independently, and all must pass — adding a schema can only ever tighten
  validation, never loosen it. Two schemas declaring the same top-level `properties` key, with
  selectors that can match the same document, is an authoring mistake caught at load time
  (`attributeconfig_conflict`).
- **Applied-schema visibility.** Every `(document, schema)` application is logged under
  `--verbose`; a schema whose selector matches zero documents gets a warning
  (`attributeconfig_unused`) — a silently-inert guardrail is otherwise indistinguishable from one
  that always passes.
- **Security.** The evaluator never resolves a schema's `$ref` against a remote URL — an
  unresolvable `$ref` is reported as an invalid schema (`attributeconfig_invalid`), not fetched.
- **Relationship to [`providerconfig`](providerconfig.md).** `providerconfig` previously carried
  its own embedded, regex-only schema sub-feature for `resource.spec.configuration`. That
  sub-feature is retired — the same schemas are now ordinary `attributeconfig` documents, keyed
  by a `{spec.provider_type, spec.resource_type}` selector, with strictly more power (real JSON
  Schema types, nesting, cross-field `if`/`then`). `providerconfig`'s resource-type/region
  name-registry role (`additional_regions`/`additional_resources`) is unrelated and unaffected.
