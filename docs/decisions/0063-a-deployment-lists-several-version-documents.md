# A deployment lists several version documents, and every pin has one owning document

- Status: accepted
- Date: 2026-11-14
- Related: [ADR-0019](0019-version-pinning.md) (`kind: version`, pins with `reason`, logged application),
  [ADR-0064](0064-deprecate-version-spec-workspace.md) (the version document's workspace, which this makes
  unnecessary), [version-composition.md](../design/version-composition.md) (the design)

## Context and Problem Statement

A deployment names exactly one `kind: version` document. A ring that runs several products (one workspace each)
that share something, such as the remote their infrastructure code is fetched from or a common image, then has
two choices:

- one version document per product, each repeating the shared pins. Nothing makes the copies agree, so one ring
  can run two versions of the shared part without anyone noticing;
- one version document for the whole ring. The pin an edit changes is then in a file every product runs, a
  product cannot be promoted on its own, and the document fails validation once two workspaces reference it
  (`version_workspace_mismatch`).

How should several products in one ring share pins without copying them, while each product keeps its own?

## Considered Options

- **A workspace list on the version document** (`spec.workspaces: [a, b]`): one file per ring.
- **No workspace limit** (`spec.workspace: "*"`).
- **Composition in the version document**: `spec.include` of other version documents, plus `spec.overrides`
  with a required `reason` as the only way to change an included value.
- **Composition at the deployment**: `Deployment.spec.version` is one name or a list, and the pins of the listed
  documents apply together.

## Decision Outcome

Chosen: **composition at the deployment**, because the deployment is where pins take effect, so it is where two
documents pinning the same target can be found, with one level of composition and nothing to nest.

- `spec.version` takes a name or a list. Through `extends` the lists append, base first, each document once: a
  partial base lists the shared `prd-core`, each product's deployment adds its own `prd-datahub`.
- The effective pins are the union of the listed documents' pins. The same target pinned by two of them is an
  error (`version_pin_conflict`), **even with equal values**: each pin has one owning document per deployment, so
  `version set`, `version update` and `promote apply` never choose between two owners, and the next bump of one
  copy cannot quietly split a deployment.
- `--pin` is repeatable and replaces the whole list for the run; leaving out a document that pins something the
  deployment uses is a warning (`version_pin_dropped`).
- Every consumer keeps receiving one `VersionModel`: one resolver composes the list, and pin logging names the
  document that owns each pin.
- A deployment's ring is the `spec.promotion` of the tagged documents it lists; untagged documents are shared and
  do not decide the ring. Promotion is read from the deployments' pins, and `promote apply` writes each pin to the
  document that owns it, skipping a document other workspaces list unless `--shared` is given.

### Consequences

- Good, because a shared pin is written once and a ring cannot run two values of it inside one deployment.
- Good, because a product keeps its own document and its own promotion, and a customer that differs lists a
  different document instead of a copy of the ring.
- Good, because a solution with one document per deployment works and validates as before.
- Bad, because no single file says what a ring runs: `promote status`, `strata version show` and the build's
  `version.yaml` answer it instead.
- Bad, because a customer that differs on one pin of a large product document must split that document or copy
  it; finer-grained overrides are left until a real case asks for them.
- Bad, because several checks need to know which targets a deployment uses (`pinnable_targets()`), and a use that
  function misses shows up as a false finding.

The workspace list was rejected because an edit to one product's pin is a change to a file every listed workspace
runs and one product cannot be promoted alone; `"*"` removes the limit and gives promotion nothing to group by.
Composition in the version document was the first design: it gives one file per ring, but needs nesting, cycle
detection, the workspaces of an include and stacked overrides, all to find conflicts the deployment shows directly.
