# An artifact's image fields are read with a built-in `${artifact:NAME.FIELD}` token

- Status: accepted
- Date: 2026-11-10
- Related: [ADR-0026](0026-pinnable-artifact-references.md) (the artifact kind, `store: artifact` and a module
  service's `artifact`, which all stay), `artifact-gap-analysis.md` (work doc)
  (designs 1 and 3), [docs/design/deployment-tokens.md](../design/deployment-tokens.md) (the built-in token this one
  copies)

## Context and Problem Statement

ADR-0026 gave two ways to use an artifact. A module service's `artifact` sets that service's image. Anything else
(a Terraform payload, a Helm chart value, a script argument, a Compose environment value) reads an artifact through
a `store: artifact` variable: an environment declares `{key, store: artifact, value: NAME, field: FIELD}` and the
consumer writes `${var:KEY}`.

That variable exists only to forward one field. It must be declared in an environment every consuming deployment
reaches, once per field, so an artifact used as name and tag costs two declarations, and a deployment that misses
the environment fails on an undeclared key. Strata already knows both halves of the answer without it: the artifact
is a document with an identity, and the pin comes from the deployment's version.

`${value:artifact.NAME.spec.image_tag}` reads the document directly but was already rejected, because it resolves
once per solution, before a deployment is chosen, so it cannot see a pin. `${value:...spec.image_name}` was allowed,
and an image built from it was never pinned and never listed by `deploy images`, the SBOM or `image_tag_policy`.

## Considered Options

- **Keep the variable as the only way.** No change; the declaration overhead stays, and `${value:}` stays a way
  around every image check.
- **Let `${value:}` apply pins.** It resolves per solution, not per deployment, so it would need a deployment in
  scope everywhere it runs, which is the whole difference between `${value:}` and the deploy-time tokens.
- **A built-in token, `${artifact:NAME.FIELD}`, resolved at deploy like `${deployment:KEY}`.** No declaration; the
  deployment and its version are known where it resolves.
- **Fields on the module only** (an `image_values` mapping that tells strata where in a chart's values to write a
  service's image). Covers Helm, but not Terraform, scripts or Compose environment values, and adds a per-chart
  mapping schema.

## Decision Outcome

Chosen: **a built-in `${artifact:NAME.FIELD}` token**, because it needs no declaration, applies the deployment's pin
where a pin is known, and works in every field the deploy-time tokens already reach, through one mechanism that
already exists.

- `FIELD` is `image_name` (never pinned), `image_tag` (the deployment's pin, else the artifact's own tag) or
  `image_ref` (`name:tag`, `name@digest`, or the bare name when there is no tag), the same three a `store: artifact`
  variable's `field` takes, built from the same pin lookup so the two cannot disagree.
- It resolves at deploy time only, in the token map that reaches the Terraform backend and projection, Helm, Compose
  and script resolvers, keyed `artifact:NAME.FIELD` so it cannot collide with another kind. It is not added to the
  values that feed `TF_VAR_*`, secret redaction or the GitOps path, as `${deployment:}` is not.
- `${artifact:NAME.image_tag}` on an artifact with no tag and no pin is an error, never an empty string: `repo:` is
  not a reference any runtime accepts. `strata validate` judges every token per deployment.
- `store: artifact` stays: Terraform reads variables (`strata.variables`, `TF_VAR_<key>`), and a variable can be
  overridden per environment. A module service's `artifact` stays too.
- An artifact used through the token is an image the deployment runs: `deploy images`, the SBOM, the image gates and
  `image_tag_policy` list and judge it.
- `${value:}` no longer reads an artifact's image fields (`spec.image_name` joins `spec.image_tag`); its free-form
  fields stay readable.

### Consequences

- Good: no variable to declare per field and per environment; the pin is applied wherever the image is written,
  including a Helm chart's own `image.repository` / `image.tag` keys.
- Good: every way of using an artifact now goes through the pin lookup and is seen by the image checks.
- Good: one mechanism, copied point for point from `${deployment:}`, so validation and resolution share code.
- Bad: a 7th token kind for authors to learn, and two ways (the token and `store: artifact`) to reach the same value.
- Bad: `${value:artifact.NAME.spec.image_name}` stops validating, a breaking change for any solution that used it.
- Neutral: GitOps renders do not get the token; an `output.template` reads the pinned images through `graph.images`.
