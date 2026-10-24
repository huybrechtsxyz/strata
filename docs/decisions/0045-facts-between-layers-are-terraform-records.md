# Facts between deployment layers travel as Terraform-written records, not through strata

- Status: accepted
- Date: 2026-10-24
- Related: [ADR-0036](0036-cross-run-key-value-output-store.md) (`${output:}` across runs of one deployment),
  [docs/design/output-declarations.md](../design/output-declarations.md) (`${output:}` key checks),
  [ADR-0044](0044-environment-values-carry-no-metadata-fields.md) (no provenance fields),
  [docs/archive/environment-value-metadata.md](../archive/environment-value-metadata.md) (investigation)

## Context and Problem Statement

A consumer's layers (bootstrap, spoke, ingress, customer, ring) each need facts the layer above created: VNet and
subnet IDs, private DNS zone IDs, vault IDs, a perimeter profile. These were copied by hand into environment files as
constants, with "confirmed via `az ...`, <date>" and "re-read if X is recreated" beside them.

Each layer is a separate deployment. `${output:STEP.KEY}` resolves only within one deployment's step graph (and
ADR-0036's store keeps outputs per deployment), so it cannot carry a fact from the spoke deployment to the ring
deployment. Should strata gain a way to pass one deployment's outputs into another's inputs?

## Considered Options

- **Extend `${output:}` across deployments**, or add step-level input mapping in strata.
- **A strata snapshot/refresh command** that writes another layer's outputs into the leaf as constants.
- **Terraform-written layer records**: each layer's own Terraform writes one JSON secret, `<layer>-outputs`, into the
  per-subscription Terraform-state Key Vault in the same apply; the next layer reads it at plan time through a
  shared component that checks it. Strata only carries where the vault is.

## Decision Outcome

Chosen: **Terraform-written layer records**, as built by the consumer (cfg-int-deployment
`docs/design/bootstrap-config-layering.md`, iac-int-deployment `components/layer-record`). No strata capability.

A record is published if and only if the apply that created the facts succeeded, is versioned atomically, carries a
`schema` and `layer` the reader checks, and works for a root run without strata. A strata mechanism would sit outside
the apply and could offer none of that. It also settles the audit concern behind the hand-copied constants: the
record is read at plan time, so a re-applied upstream layer shows as a plan diff, and the plan is what is reviewed.
One writer per record means no compare-and-swap is needed, unlike ADR-0036's store.

### Consequences

- Good: no new strata schema or command; the facts never enter the config, so they cannot go stale there.
- Good: same split as subnet CIDR derivation: strata carries the request, Terraform moves the facts.
- Bad: only Terraform consumers can read a record this way. Reopen if a Helm, Compose or script consumer needs
  another deployment's facts, or if the record approach fails its first real run.
