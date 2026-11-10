# Artifact gap analysis - Work

- Status: in-progress
- Last updated: 2026-11-10
- Related: [artifact.md](../config/artifact.md) (the reference),
  [ADR-0026](../decisions/0026-pinnable-artifact-references.md) (pinnable artifact references),
  [artifact-references.md](../archive/artifact-references.md) (the original design, archived),
  [helm-image-delivery.md](helm-image-delivery.md) (which image a Helm module runs),
  [deployment-tokens.md](../design/deployment-tokens.md) (the built-in token this doc proposes to copy)

## Overview

A review of how a `kind: artifact` can be used in configuration, looking for what is wrong, missing or awkward. It
reads the code (`models/artifact_model.py`, `controllers/value_controller.py`, `controllers/workload_controller.py`,
`controllers/effective_images.py`, `utils/value_tokens.py`) against [artifact.md](../config/artifact.md) and
ADR-0026, and checks the example solution in `config/`.

An artifact is a named, pinnable container image reference: `image_name`, an optional `image_tag` that a
`kind: version` pin (`pins.artifacts`) overrides, a documentation-only `registry`, a reserved `integration`, and
free-form `properties` / `configuration` / `custom`. Strata never fetches the image.

## Current Design / Progress

### How an artifact is used today

| Way                         | Written as                                                                  | Reaches                                                                                          | Pins applied |
| --------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ------------ |
| `store: artifact` variable  | `{key: API_IMAGE_TAG, store: artifact, value: example-api, field: image_tag}` | `strata.variables`, `TF_VAR_<key>`, and `${var:KEY}` anywhere strata resolves tokens             | yes          |
| Module service `artifact`   | `services: [{name: api, artifact: example-api}]`                            | Resolved into `service.image` (`image_ref`) before rendering: the Compose file's `image:`         | yes          |
| `${value:artifact.N.<path>}` | `${value:artifact.example-api.spec.image_name}`                             | Any field, resolved once per solution                                                            | **no**       |

`${value:artifact.N.spec.image_tag}` is rejected because it would read the unpinned tag. Every other path is
allowed, including `spec.image_name` and `spec.configuration.*`.

What reads the result: `strata deploy images`, `image_tag_policy`, the SBOM image collector, the CVE and image gates
(through the SBOM), the audit manifest, `deploy report`'s images, and `version new` / `version update` (which offer
artifact pins).

### Findings

Ranked by impact. "Verified" means checked in the code or the example solution; "by reading" means it follows
from the code but was not run end to end.

| #  | Gap                                                                                 | Kind          | Impact | Evidence   |
| -- | ----------------------------------------------------------------------------------- | ------------- | ------ | ---------- |
| 1  | An artifact can only be read through a variable declared for it                     | usability     | high   | verified   |
| 2  | A Helm service `artifact` never reaches the chart                                   | correctness   | high   | verified   |
| 3  | Images reached through `${value:artifact...}` are invisible and unpinned            | correctness   | medium | by reading |
| 4  | A tag plus a digest produces an invalid image reference                             | correctness   | medium | by reading |
| 5  | `registry` is not part of the image reference                                       | usability     | medium | verified   |
| 6  | `integration` is reserved: no registry credentials                                  | feature       | medium | verified   |
| 7  | A declared but unused `store: artifact` variable counts the artifact as deployed    | accuracy      | low    | by reading |
| 8  | An artifact nothing references gets no warning                                      | validation    | low    | verified   |
| 9  | The example solution shows only one of the two ways                                 | docs          | low    | verified   |
| 10 | About 20 references to `artifact-references.md` point where it no longer is         | docs          | low    | verified   |

#### 1. An artifact can only be read through a variable declared for it

To use an artifact's tag in a Terraform payload, a Helm value, a script argument or a Compose environment value,
an environment must first declare a `store: artifact` variable whose only job is to forward one field of the
artifact, and the consumer then reads `${var:KEY}`. In `config/environments/prd.yaml`, `API_IMAGE_TAG` exists only
for that. The variable has to be declared in an environment every consuming deployment reaches, once per field
used (`image_name`, `image_tag`, `image_ref`), so one artifact used as name and tag costs two declarations. The
knowledge is already in strata: the artifact is a document with an identity, and the pin comes from the
deployment's version.

**Proposal: `${artifact:NAME.FIELD}`**, a built-in token like `${deployment:KEY}`
([deployment-tokens.md](../design/deployment-tokens.md)):

| Token                          | Value                                                                    |
| ------------------------------ | ------------------------------------------------------------------------ |
| `${artifact:NAME.image_name}`  | `spec.image_name`, never pinned                                          |
| `${artifact:NAME.image_tag}`   | the effective tag: the deployment's pin, else `spec.image_tag`, else an error |
| `${artifact:NAME.image_ref}`   | `compose_image_ref()` of the two, as `field: image_ref` gives today      |

- Same mechanics as `${deployment:}`: add the kind to `VALUE_TOKEN_KINDS` and the regexes, store the entries in the
  flat token map under `artifact:NAME.FIELD` (the colon keeps them apart from every other key), and build them with
  `resolve_artifact_field()`, so the token and `store: artifact` cannot disagree.
- Resolved at deploy, where the deployment and its version are known, in the one `tokens` map that reaches the
  Terraform backend and projection, Helm, Compose and script resolvers.
- Only the artifacts the deployment actually references need entries, so the map is built from a scan for the
  token (as `referenced_output_keys()` scans for `${output:}`), not from every artifact in the solution.
- Validation per deployment, in every document it reaches, as `unresolved_deployment_tokens()` does:
  `unknown_artifact_token` for an artifact or field that does not exist, and an error for `image_tag` on an artifact
  with no tag and no pin. An empty tag is never substituted silently (the rule `${deployment:tenant}` set).
- `effective_images()` must count token uses (finding 3), with `via` `token:<document>/<path>`, or a token-delivered
  image escapes `deploy images`, the SBOM and the policies.
- The agent skill's token table needs the row (a drift test enforces it).

Open questions:

- **`store: artifact` after the token.** Keep it: Terraform reads `strata.variables` and `TF_VAR_<key>`, which need a
  variable key, and a variable can be overridden per environment. Document the token as the default and the variable
  for Terraform inputs. Deprecating it would break `config/` and haven-style solutions for no gain.
- **GitOps and `ValueResolution.values`.** `${deployment:}` is deliberately not in `ValueResolution.values`, which
  feeds `TF_VAR_*`, secret redaction and the GitOps path. An artifact tag is not a secret, and a GitOps render would
  want it. Decide whether the artifact entries go there too.
- **Build time.** `build run` writes `strata.variables` with the pinned tag. Should it also resolve the token where
  it renders (Compose, Helm values), or leave every token to deploy as today?
- **Environment store values** cannot use `${deployment:}` (`store_value_token_unsupported`), because a store value
  is resolved before a deployment is chosen. The same applies here.
- **More fields?** `registry` (finding 5) and `spec.configuration.*` (today reachable through `${value:}`) could be
  token fields too. Keep the first version to the three image fields.

#### 2. A Helm service `artifact` never reaches the chart

A module service's `artifact` is resolved into `service.image`, and the Helm integration never writes
`service.image` into the chart values (`integrations/helm.py` has no reference to `image`; see
[helm-image-delivery.md](helm-image-delivery.md)). So for a Helm module, `artifact:` and its `pins.artifacts` pin
change `deploy images`, the SBOM, the policies and the audit manifest, but not what runs. `artifact.md` and
`module.md` do not say so.

The token from finding 1 gives a direct way to deliver it, in the chart's own key shape, which is the problem
helm-image-delivery's option C needed a new `image_values` field for:

```yaml
spec:
  configuration:
    image:
      repository: "${artifact:immich.image_name}"
      tag: "${artifact:immich.image_tag}"
```

Tokens are already resolved in a Helm module's values, and `check_configuration_keys` already checks `image.tag`
against the chart's `values.yaml`. Proposal:

- Document it: for Helm, a service `artifact` is declared only; use the token in `configuration` to deliver it.
- Warn when a Helm module service names an `artifact` but no token for that artifact appears in the module's
  values (`artifact_not_delivered`), so the declared-only case is visible.
- With finding 3 done, a token in Helm values counts as a delivered use (`delivered: true` in helm-image-delivery's
  option B).

#### 3. Images reached through `${value:artifact...}` are invisible and unpinned

`effective_images()` finds artifacts through `store: artifact` variables and module service `artifact` fields only.
`${value:artifact.N.spec.image_name}` combined with a literal tag (or with `${var:}` from a constant) is allowed
today, never pinned, and never listed. So `deploy images`, `image_tag_policy`, the SBOM and the gates do not see that
image. Only `spec.image_tag` is rejected, with a message pointing to `store: artifact`.

Proposal: reject `${value:artifact.N.spec.image_name}` too, with the same message, pointing to the token. Count every
`${artifact:}` use in `effective_images()`. `spec.configuration.*` and the other free-form fields stay readable
through `${value:}`: they are not an image.

#### 4. A tag plus a digest produces an invalid image reference

`image_tag` holds either a tag or a digest. `compose_image_ref()` treats any value with a `:` as a digest and joins
it with `@`. A pinned reference with both, the form most registries and security tooling recommend
(`1.4.2@sha256:...`), contains a `:`, so it becomes `example/api@1.4.2@sha256:...`, which no runtime accepts.
`parse_image_ref()` (the SBOM) would then split it at the last `@`.

Proposal: an optional `image_digest` on the artifact (and a structured pin value), with `image_ref` as
`name:tag@digest` when both are set; or accept `tag@digest` in `image_tag` and compose it correctly. A digest-only pin
keeps working. `image_tag_policy` could then require a digest.

#### 5. `registry` is not part of the image reference

`spec.registry` is free text for documentation. `image_ref` is `image_name[:tag]`, so a solution that pulls from a
private registry must repeat the host in `image_name` (`registry.example.com/example/api`), and `registry` can
disagree with it without any check. In `config/artifacts/example-api.yaml`, `registry` is
`registry.example.com` and `image_name` is `example/api`: the reference strata delivers points at Docker Hub.

Options:

- A. Compose it: when `registry` is set and `image_name` has no host part, `image_ref` is
  `registry/image_name[:tag]`. Changes the delivered reference for every artifact that sets both today, so it needs
  a changelog entry and probably a warning release first.
- B. Check it: warn when `registry` is set and `image_name` does not start with it (`artifact_registry_mismatch`).
  No behaviour change.
- C. Keep it as documentation and say plainly in `artifact.md` that `image_name` must hold the full path.

B now, A only if a consumer wants one artifact per image across registries (a mirror per environment).

#### 6. `integration` is reserved: no registry credentials

`spec.integration` must name an existing integration, and nothing reads it. A pipeline that copies the image to a
target registry before `deploy run` (the use `deploy images` was made for) and a runtime that pulls from a private
registry both get credentials elsewhere. For Helm that is an `imagePullSecrets` the author writes. For Compose,
`docker stack deploy` always passes `--with-registry-auth`, which forwards whatever `docker login` the deploy runner
already has; strata never logs in.

Trigger: a real consumer with a private registry. Until then, say in `artifact.md` that strata never supplies pull
credentials (it already says the field is reserved). Candidates once it is picked up: `deploy images` listing the
integration per image so a copy pipeline can authenticate, and a `docker login` from the integration before a
Compose deploy whose stack names one.

#### 7. A declared but unused `store: artifact` variable counts the artifact as deployed

`effective_images()` reads the merged environment declarations, not the uses: a `store: artifact` variable that no
document references still lists its artifact as an image the deployment runs. For Terraform this is right, because
every variable reaches `strata.variables` and `TF_VAR_<key>`. For a deployment without a Terraform step it
over-reports, so `image_tag_policy` can fail a deployment on an image it does not run. Low: over-reporting is the
safe direction. Revisit with finding 1, when most uses become tokens that are counted where they are written.

#### 8. An artifact nothing references gets no warning

The only artifact diagnostic is `undefined_artifact` (a `store: artifact` variable naming a missing artifact); a
missing service `artifact` fails as a reference. An artifact that no variable, service or token references is never
reported, and a `version update` still offers to pin it. Proposal: an info or warning `artifact_unused` in `strata
validate`, once finding 1 makes the use set exact.

#### 9. The example solution shows only one of the two ways

`config/` uses `store: artifact` (`API_IMAGE_TAG`) and no module service `artifact`. The Compose path, the one where a
service `artifact` actually changes what runs, has no example. Add one to a Compose module in `config/` (and the token
once it exists), so `strata deploy images` on the example shows both origins.

#### 10. About 20 references to `artifact-references.md` point where it no longer is

The design moved to [docs/archive/artifact-references.md](../archive/artifact-references.md). Docstrings in
`artifact_model.py`, `artifact_service.py`, `store_model.py`, `module_model.py`, `version_model.py`,
`value_controller.py` (4), `workload_controller.py` (3), `semantic_checks.py` and `environment_service.py`, five
tests, and several docs still cite `docs/work/` or `docs/design/artifact-references.md`. Fix them in the same change
as finding 1, which also changes `artifact.md`.

### Smaller notes

- `pins.images` is keyed by service name alone, so two modules with a service of the same name share one pin. An
  `artifact` per image avoids that; worth a line in `version.md` (by reading, not reproduced).
- The artifact kind covers container images only. The model says "possibly a chart or other identifier later";
  charts are pinned on the module (`pins.charts`). No consumer needs another artifact type yet.

## Related Decisions

- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) - the artifact kind, `store: artifact` and the
  service `artifact`
- [ADR-0019](../decisions/0019-version-pinning.md) - `kind: version` pins
- [value-token-resolution.md](../design/value-token-resolution.md) - the `${kind:KEY}` tokens and where they
  resolve

## Remaining Work / Open Questions

### Proposed order

1. Finding 1, the `${artifact:NAME.FIELD}` token, together with finding 3 (count token uses, reject
   `${value:artifact...image_name}`) and finding 10 (stale references). One release.
2. Finding 2: document Helm delivery through the token, and the `artifact_not_delivered` warning.
3. Finding 5 option B and finding 9 (example), small.
4. Finding 4 (digest), when a consumer pins by digest.
5. Findings 7 and 8, after 1.
6. Finding 6 on a real private-registry consumer.

### Open questions

- The four questions under finding 1 (keep `store: artifact`, GitOps, build time, store values).
- Finding 2: should a Helm service `artifact` become an error rather than a warning once the token exists?
- Finding 5: compose the registry (A) or only check it (B)?

## Changelog

- 2026-11-10: created from a review of artifact use in configuration.
