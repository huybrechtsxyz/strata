# Remove `ProviderConfig.spec.version`: a field nothing reads

- Status: accepted
- Date: 2026-10-29
- Related: [ADR-0003](0003-provider-model-design-decisions.md) section 7 (made this field the sole
  home of the provisioner version constraint),
  [ADR-0050](0050-one-offline-integration-readiness-check.md) and
  [ADR-0051](0051-local-tools-as-declarable-integration-types.md) (where version constraints are
  enforced)

## Context and Problem Statement

`kind: providerconfig` carried an optional `spec.version`, "provider version constraint (e.g.
`~>3.0`)", ported unchanged from v1's `ConfigurationProviderModel.version`. ADR-0003 section 7
removed the duplicate on the per-instance `Provider` document and named this field "the sole source
of truth" for a provider's version constraint (Terraform's `required_providers`), on the argument
that a constraint is a type-level property.

Checked 2026-10-29: nothing reads it. v2's source and tests have no reader, the Terraform
projection does not carry it, and v1 never read it either (it was a model-only field there). The
string is not validated: `"this is not a constraint"` is accepted. Neither real consumer sets it,
and its only use was the example registry and the doc example. The documentation called it a
"provisioner" constraint while the model called it a "provider" one.

The risk is a silent one. `integration.spec.version` is now enforced by the readiness check, so a
reader would expect a version on a provider type to be enforced too, and setting
`version: "~>4.0"` would change nothing.

## Considered Options

- **Remove the field.** Nothing depends on it, and the schema stops promising a constraint it does
  not enforce.
- **Keep it as documentation and correct the wording.** Harmless, but a settable field that looks
  like a constraint invites the same wrong expectation, and it duplicates what the Terraform source
  already says.
- **Enforce it.** It is unclear what it would be compared against. A Terraform provider's version
  belongs in `required_providers` in the Terraform source, which strata already reads for the SBOM,
  and adding a second place to declare it would create the "which one wins" ambiguity ADR-0003
  removed from `Provider`.

## Decision Outcome

Chosen: **remove `ProviderConfig.spec.version`**, because it is unused, unvalidated and misleading,
and no consumer would notice.

- `ProviderConfigSpecModel` no longer has `version`. `extra="forbid"` means a document that still
  sets it fails validation, with the field named in the error.
- The example registry and the `providerconfig` documentation drop it.
- ADR-0003 is not edited. Its statement that this field was the sole source of truth was accurate
  when written; this ADR reverses that part.

### Consequences

- Good: one less field that looks enforced and is not.
- Good: version constraints now have one meaning in the schema, `integration.spec.version`, which is
  parsed, validated and checked against the installed tool.
- Bad: a solution that sets `version` on a `providerconfig` must delete the line. No known solution
  does.
- Neutral: if provider-version enforcement is ever needed, it should be designed against
  `required_providers` in the Terraform source rather than restored here.
