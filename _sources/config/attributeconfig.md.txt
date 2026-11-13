# AttributeConfig (`kind: attributeconfig`)

Declares a real [JSON Schema](https://json-schema.org/) (2020-12) fragment that must validate
against a targeted `properties`/`custom`/`configuration` block of every document matching
`spec.target` — a platform-authored shape guardrail for the loosely-typed passthrough blobs
every kind carries, enforced automatically against every matching document the moment it ships
(no opt-in required on the target document's side).

See [docs/design/dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)
for the full design rationale, and
[How To: model your own structured data in `properties`](../how-to/structured-properties-with-attributeconfig.md)
for a worked example.

## Schema

- `spec.target.kind` — which [`PlatformKind`](../GLOSSARY.md) this schema targets (e.g. `resource`)
- `spec.target.block` — which blob field on that kind to validate: `properties`, `custom`,
  `configuration`, `variables`, `features` (whichever the target kind actually has), or `composite`
  (all four merged blocks together; `view: effective` only). `values` is a
  [parameter set](parameterset.md)'s own values (`view: document`); `params` is the merged
  parameter values a document receives (`view: effective`, see below). Never `secrets`.
- `spec.target.view` — required: `document` validates each matching document's own block in
  isolation; `effective` validates each deployable deployment's merged result (`target.kind:
  deployment` only), or with `block: params`, what a deployment, environment or tenant receives
  from its parameter sets
- `spec.target.selector` — optional `field.path: required-value` equality map narrowing which
  documents of `target.kind` this schema applies to. Omitted means every document of that kind.
  Equality only — no wildcards or regex. A path walks into a map by key: `meta.labels.role:
  spoke` selects on a label.

## Parameter sets: two views

Values are layered, so they are checked in two places:

- **One set at a time** (`kind: parameterset`, `block: values`, `view: document`): the shape of
  what one file holds. A set does not know its role, so select on an optional label:

  ```yaml
  spec:
    target: { kind: parameterset, block: values, view: document, selector: { meta.labels.role: spoke } }
    schema:
      type: object
      required: [name, hub, tenant]
      properties:
        name: { type: string, pattern: "^[a-z][a-z0-9]*-[0-9]{3}$" }
  ```

- **What a document receives** (`block: params`, `view: effective`, for `kind: deployment`,
  `environment` or `tenant`): the values its chain's sets give it once merged per role (root
  first, leaf last), with the roles as top-level keys. "Everything a base needs is there" can
  only be checked here: a hub's set does not fill the `spoke` role.

  ```yaml
  spec:
    target: { kind: deployment, block: params, view: effective, selector: { spec.workspace: bootstrap } }
    schema:
      required: [spoke, hub]
      properties:
        spoke: { required: [name, hub, tenant] }
        hub: { properties: { provider: { enum: [azure-westeurope, azure-westus2] } } }
  ```

  A deployment is checked when it is deployable, with its selector evaluated on the resolved
  deployment; an environment or tenant when it is not `partial: true`. A document that names no
  set receives `{}`, so narrow the selector to the documents that use the base. Findings are
  located at `meta.params.<role>.<key>` on the document, and name it.

A schema cannot check that a value names an existing tenant or provider; the reference check on
the resolved document does.
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
    view: document
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
