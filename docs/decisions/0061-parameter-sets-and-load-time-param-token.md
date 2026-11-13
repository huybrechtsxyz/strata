# Deployment bases read per-instance values from parameter sets with a load-time `${param:}` token

- Status: accepted
- Date: 2026-11-13
- Related: [ADR-0015](0015-solution-manifest-and-document-discovery.md) (identity references, which parameter sets
  use), [ADR-0024](0024-tenant-defaults-merge.md) (tenant defaults, folded in after substitution),
  [docs/design/value-token-resolution.md](../design/value-token-resolution.md) (the run-time tokens `${param:}` sits
  beside), [docs/design/parameter-sets.md](../design/parameter-sets.md) (the design),
  [docs/archive/base-parameters.md](../archive/base-parameters.md) (version 1, typed inputs on the base)

## Context and Problem Statement

A hub-and-spoke solution repeats the same deployment per spoke: seven documents per spoke whose only differences
are a name, a code, a sequence number and a subscription. `extends` shares structure, but every value that differs
per spoke, including the names in reference fields (`tenant`, `environments`), must still be written in each leaf.
The copies drift, and adding a spoke means copying a folder and editing it by hand.

A base needs to say where a per-instance value goes, and each instance needs one place to hold its values.

## Considered Options

- **Typed inputs on the base** ([version 1](../archive/base-parameters.md)): a base declares its inputs with types;
  a leaf binds them in `extends`. Strong checks, but every base declares every input, and two bases sharing values
  declare them twice.
- **Use the layer segments as the parameters**: segments are a path convention, not a values store; they cannot
  hold a subscription ID or a list.
- **Generate the files with a script**: works today, but the generated files are the source of truth nobody wrote,
  and strata cannot check the generator.
- **Overlays (Kustomize style)**: patches per instance; still one patch file per instance per document.
- **Parameter sets named per role, read with `${param:<role>.<key>}`, substituted when the solution loads.**

## Decision Outcome

Chosen: **parameter sets and a load-time `${param:}` token**, because one plain values file per instance, named by
the documents that use it, removes the copying while keeping every document and every value explicit and checked.

- **A new kind, `parameterset`.** `spec.values` is any YAML. Values are plain: no `${...}` token, so a set depends on
  nothing and a value can never insert a token (or a secret) into a document.
- **Roles are given where a set is used.** `meta.params` maps a role to a set or a list of sets
  (`params: { hub: hub-hub02, spoke: spoke-acme-001 }`). A set holds plain keys; bases read `${param:spoke.code}`,
  never a set's name, so two sets cannot collide and a base serves every instance.
- **Values merge per role along the `extends` chain**, root first, leaf last, maps per key: a base's sets are its
  defaults, the leaf's override them.
- **`${param:}` is a load-time token class**, beside the run-time tokens (`${var:}`, `${secret:}`, ...). It is
  substituted once, when deployment chains are resolved, before anything runs; substituted text is never scanned
  again; it is not one of the run-time token kinds, so it can never reach run time.
- **A deployment whose spec holds `${param:}` is a template**, validated per deployable deployment after
  substitution, so the token works in typed and reference fields. Findings name the template's field, the role,
  key, value and set, and the deployment.
- **Names, values files and chains stay static**: no `${param:}` in `meta`, `spec.extends` or `spec.partial`. An
  optional `meta.name_rule` checks that each deployment's plain name matches its values.
- **No implied values**: nothing comes from the folder, and a set never names the sets it builds on. Each leaf names
  every set it uses.
- Unused values are reported as information (`param_unused`), never as an error or warning.
- Deployments first. Environments and tenants get `extends`, `partial` and `meta.params` in a later phase, with the
  same rules.

### Consequences

- Good: a new spoke is one parameter set and one short leaf per stack; every real fact about the spoke sits in one
  file.
- Good: every resolved deployment is validated as an ordinary deployment, so a bad value is found per instance, on
  the file that holds it.
- Good: plain values and a single substitution pass keep secrets and run-time tokens out of the values.
- Bad: a second token class with its own timing (load, not run) for authors to learn, and a template's spec is only
  checked through the deployments that use it: a base no deployment extends is not checked at all.
- Bad: the exported JSON schema does not know templates, so the YAML editor may underline `${param:}` in a typed
  field that `strata validate` accepts.
- Bad: a base hard-codes the roles it reads; moving a key from the spoke's set to the hub's changes its path in
  every base that reads it (a missed one is an error, never a wrong value).
- Neutral: `${value:parameterset.<name>.spec.values.<key>}` resolves through the generic `${value:}` path; whether to
  forbid it, so `${param:}` stays the one way to read a set, is open.
