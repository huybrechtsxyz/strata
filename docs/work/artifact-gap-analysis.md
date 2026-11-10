# Artifact gap analysis - Work

- Status: in-progress (all designs agreed; design 1 built, the rest not started)
- Last updated: 2026-11-10
- Related: [artifact.md](../config/artifact.md) (the reference),
  [ADR-0026](../decisions/0026-pinnable-artifact-references.md) (pinnable artifact references),
  [artifact-references.md](../archive/artifact-references.md) (the original design, archived),
  [helm-image-delivery.md](helm-image-delivery.md) (which image a Helm module runs),
  [deployment-tokens.md](../design/deployment-tokens.md) (the built-in token the main design copies),
  [image-tag-policy.md](../design/image-tag-policy.md) (the policy over effective images)

## Overview

A review of how a `kind: artifact` can be used in configuration, looking for what is wrong, missing or awkward, with
a design for each gap. It reads the code (`models/artifact_model.py`, `controllers/value_controller.py`,
`controllers/workload_controller.py`, `controllers/effective_images.py`, `controllers/value_references.py`,
`utils/value_tokens.py`) against [artifact.md](../config/artifact.md) and ADR-0026, and checks the example solution
in `config/`.

An artifact is a named, pinnable container image reference: `image_name`, an optional `image_tag` that a
`kind: version` pin (`pins.artifacts`) overrides, a documentation-only `registry`, a reserved `integration`, and
free-form `properties` / `configuration` / `custom`. Strata never fetches the image.

## Current Design / Progress

### How an artifact is used today

| Way                          | Written as                                                                    | Reaches                                                                                  | Pins applied |
| ---------------------------- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------ |
| `store: artifact` variable   | `{key: API_IMAGE_TAG, store: artifact, value: example-api, field: image_tag}` | `strata.variables`, `TF_VAR_<key>`, and `${var:KEY}` anywhere strata resolves tokens     | yes          |
| Module service `artifact`    | `services: [{name: api, artifact: example-api}]`                              | Resolved into `service.image` (`image_ref`) before rendering: the Compose file's `image:` | yes          |
| `${value:artifact.N.<path>}` | `${value:artifact.example-api.spec.image_name}`                               | Any field, resolved once per solution                                                    | **no**       |

`${value:artifact.N.spec.image_tag}` is rejected (`value_reference_pinnable_field`) because it would read the
unpinned tag. Every other path is allowed, including `spec.image_name` and `spec.configuration.*`.

What reads the result: `strata deploy images`, `image_tag_policy`, the SBOM image collector, the CVE and image gates
(through the SBOM), the audit manifest, `deploy report`'s images, `version new` / `version update` (which offer
artifact pins), and an `output.template` through `graph.images`.

### Findings

Ranked by impact. "Verified" means checked in the code or the example solution; "by reading" means it follows
from the code but was not run end to end.

| #  | Gap                                                                              | Kind        | Impact | Evidence   |
| -- | -------------------------------------------------------------------------------- | ----------- | ------ | ---------- |
| 1  | An artifact can only be read through a variable declared for it                  | usability   | high   | verified   |
| 2  | A Helm service `artifact` never reaches the chart                                | correctness | high   | verified   |
| 3  | Images reached through `${value:artifact...}` are invisible and unpinned         | correctness | medium | by reading |
| 4  | A tag plus a digest produces an invalid image reference                          | correctness | medium | by reading |
| 5  | `registry` is not part of the image reference, and can disagree with it          | usability   | medium | verified   |
| 6  | `integration` is reserved: no registry credentials                               | feature     | medium | verified   |
| 7  | A declared but unused `store: artifact` variable counts the artifact as deployed | accuracy    | low    | by reading |
| 8  | An artifact nothing references gets no warning                                   | validation  | low    | verified   |
| 9  | The example solution shows only one of the two ways                              | docs        | low    | verified   |
| 10 | About 20 references to `artifact-references.md` point where it no longer is      | docs        | low    | verified   |

## Designs

Each design states the problem, the decision, the changes and how it is checked. Diagnostic codes are new unless
marked otherwise.

### 1. `${artifact:NAME.FIELD}`: read an artifact without a variable

**Problem.** To use an artifact's tag in a Terraform payload, a Helm value, a script argument or a Compose
environment value, an environment must declare a `store: artifact` variable whose only job is to forward one field,
and the consumer reads `${var:KEY}`. `API_IMAGE_TAG` in `config/environments/prd.yaml` exists only for that. It must
be declared in an environment every consuming deployment reaches, once per field, so an artifact used as name and tag
costs two declarations. Strata already knows the artifact (a document with an identity) and the pin (the
deployment's version).

**Decision.** A built-in token, built the way `${deployment:KEY}` is:

| Token                         | Value                                                                       |
| ----------------------------- | --------------------------------------------------------------------------- |
| `${artifact:NAME.image_name}` | `spec.image_name`, never pinned                                             |
| `${artifact:NAME.image_tag}`  | the effective tag: the deployment's pin, else `spec.image_tag`              |
| `${artifact:NAME.image_ref}`  | `compose_image_ref()` of the name and effective tag (design 4 extends it)   |

- **Syntax.** `NAME` is a `PlatformName` (`^[a-z][a-z0-9_-]*$`, no dot), so the key splits at its first `.`. The
  existing key group (`[A-Za-z0-9_.-]+`) already accepts it; only the kind alternation gains `artifact`.
  `$${artifact:...}` is a literal, like every escaped token.
- **A missing tag is an error.** `image_tag` on an artifact with no tag and no pin does not resolve
  (`unavailable_artifact_token`), never an empty string: an empty tag in `repo:${...}` gives `repo:`, which a runtime
  rejects late or reads as `latest`. `image_ref` resolves to the bare name, as `field: image_ref` does today; an
  `image_tag_policy` with `require_tag` catches that case.
- **Deploy time only, like `${deployment:}`.** The deployment, and so its version, is chosen at deploy. The entries
  join the one `tokens` map `deploy_controller.py` builds (resolved values, earlier steps' outputs, deployment
  entries), which reaches the Terraform backend and projection, Helm, Compose and script resolvers. `build run`
  leaves the tokens in place, as it does every other token, and `strata validate` judges them.
- **Not in `ValueResolution.values`.** That map feeds `TF_VAR_*`, secret redaction and the GitOps path, and
  `${deployment:}` stays out of it for the same reasons. A GitOps `output.template` already reads the pinned images
  through `graph.images`; a Terraform module that wants a tag as a variable keeps a `store: artifact` variable.
- **Not in an environment store's own `value`** (the existing `store_value_token_unsupported`): a store value is
  resolved before a deployment is chosen.
- **`store: artifact` stays**, documented as the way to give Terraform an artifact field as a variable
  (`strata.variables`, `TF_VAR_<key>`), or to override it per environment. Everything else uses the token.

**Changes.**

- `utils/value_tokens.py`: `artifact` in `VALUE_TOKEN_KINDS` and both regexes; `ARTIFACT_TOKEN_FIELDS =
  ("image_name", "image_tag", "image_ref")`; `artifact_key(name, field)` returning `artifact:NAME.FIELD`, applied in
  `_lookup_key()` as for deployment entries (the colon keeps them apart from every other key);
  `referenced_artifact_keys(node) -> set[tuple[str, str]]` beside `referenced_output_keys()`.
- `controllers/value_controller.py`: `artifact_token_values(context, deployment) -> dict[str, str]`, one entry per
  artifact in the index and field, built with `resolve_artifact_field()` so the token and `store: artifact` cannot
  disagree; `image_tag` is left out when the effective tag is blank, so resolution reports it. All in memory: no
  store, no network.
- `controllers/deploy_controller.py`: add `**artifact_token_values(context, deployment)` to `tokens`, beside
  `deployment_token_values()`.
- `services/environment_service.py`: `unresolved_artifact_tokens(model, artifact_values)`, the counterpart of
  `unresolved_deployment_tokens()`: `unknown_artifact_token` for an unknown artifact or field (listing the fields),
  `unavailable_artifact_token` for `image_tag` with no tag (naming the artifact and the deployment's version).
  `unresolved_value_tokens()` skips the kind, as it skips `deployment` and `value`.
- `controllers/semantic_checks.py`: `_check_deployment_value_tokens()` runs it on the same documents, for every
  deployment that is not a `partial: true` base.
- `effective_images()` counts token uses (design 3).
- Docs: `artifact.md` (the token first, `store: artifact` for Terraform variables), `value-token-resolution.md`
  (the 7th kind), the agent skill's token table (a drift test requires the row), the JSON Schema descriptions that
  list token kinds.

**Implementation plan** (refines the changes above after reading the code):

1. `utils/value_tokens.py`: the kind, `ARTIFACT_TOKEN_FIELDS`, `artifact_key()`, `_lookup_key()`, an unresolved
   message that says what is missing, `referenced_artifact_keys()`.
2. A new `controllers/artifact_tokens.py`, index-based, holding `ArtifactTag`, `effective_artifact_tag()` (the pin
   lookup, moved out of `value_controller.resolve_artifact_tag()`, which now calls it) and
   `artifact_token_values(index, deployment, *, log=False)`. A separate module because `semantic_checks.py` has only
   an index and cannot import `value_controller.py` (`value_controller` -> `solution_context` -> `semantic_checks`).
   `compose_image_ref()` moves to `utils/sbom_utils.py` beside `parse_image_ref()`, re-exported where it was.
3. `deploy_controller.py`: the entries are built once per `deploy_run()`, before the step loop, with `log=True`, so
   each applied pin is logged once per run (ADR-0019 decision 7), not once per step.
4. `environment_service.py`: `unresolved_artifact_tokens()`; `unresolved_value_tokens()` skips the kind; `artifact`
   joins `_UNSUPPORTED_STORE_VALUE_TOKEN_KINDS`.
5. `semantic_checks.py`: `_check_deployment_value_tokens()` builds the entries per deployment (`log=False`) and
   runs the check where it runs the deployment-token check.
6. `script.py` unchanged: without `script.values` a script receives the whole map, artifact entries included; with
   it, only the listed keys and the `deployment:` entries reach `STRATA_VALUES`, and `args` still resolve against
   the whole map.
7. Docs, schema descriptions, the skill row, the changelog.

**Tests.** Resolution of each field with and without a pin and with `--pin`; a blank tag failing as
`unavailable_artifact_token` in validate and as a diagnostic in `deploy run` before any tool runs; an unknown name and
field; escaping; the token in a Terraform payload, a Helm value, a Compose environment value and a script argument;
the map key cannot collide with a variable, an output `artifact.x` or a deployment entry; the skill drift test.

### 2. Deliver a Helm service's artifact to the chart

**Problem.** A module service's `artifact` is resolved into `service.image`, and the Helm integration never writes
`service.image` into the chart values ([helm-image-delivery.md](helm-image-delivery.md)). For a Helm module,
`artifact:` and its pin change `deploy images`, the SBOM, the policies and the audit manifest, but not what runs.
`artifact.md` and `module.md` do not say so.

**Decision.** Deliver it through the chart's own values with the token from design 1. No new field: the
`image_values` mapping sketched as helm-image-delivery's option C is not needed, because the author writes the
chart's own key shape and strata substitutes the pinned values.

```yaml
kind: module
spec:
  type: helm
  services:
    - name: server
      artifact: immich          # declares the image: deploy images, SBOM, policies, version pins
  configuration:
    image:                      # delivers it, in this chart's own shape
      repository: "${artifact:immich.image_name}"
      tag: "${artifact:immich.image_tag}"
```

- Tokens are already resolved in a Helm module's values, and `check_configuration_keys` already checks `image.tag`
  against the chart's `values.yaml`, so a wrong path still fails at build.
- **A warning, not an error, when the artifact is not delivered:** a Helm module service that names an `artifact`
  while no `${artifact:<that name>.` token appears in the module's `configuration` or that service's
  `configuration` gets `artifact_not_delivered`: "declared as X; the chart decides which image runs". Existing
  modules keep validating; an error would fail every Helm module that declares its artifact for the SBOM only.
- The token is also enough without `services[].artifact`: design 3 counts it, so the image is listed either way.
  Declaring the service stays useful for its `env` and `persistence`.

**Changes.** `controllers/semantic_checks.py`: the `artifact_not_delivered` check over enabled modules with
`type: helm`, using `referenced_artifact_keys()`. Docs: `module.md` (what `image` and `artifact` mean for Helm),
`artifact.md` (the Helm example), and helm-image-delivery.md (option C is met by the token; its option B `delivered`
flag can read "a token for this artifact is in the values").

**Tests.** The warning with no token, no warning with the token in module or service `configuration`, no warning for
Compose; a rendered `strata-values.yaml` holding the pinned tag after `deploy run`.

### 3. Every artifact use is seen and pinned

**Problem.** `effective_images()` finds artifacts through `store: artifact` variables and service `artifact` fields
only. `${value:artifact.N.spec.image_name}` with a literal tag is allowed, never pinned and never listed, so `deploy
images`, `image_tag_policy`, the SBOM and the gates do not see that image. A `${artifact:}` token would have the same
hole.

**Decision.**

- `effective_images()` also scans every document the deployment reaches for `${artifact:}` tokens, with
  `referenced_artifact_keys()`, and adds each artifact found with `via` `token:<kind>/<name>`. The row's origin
  stays `artifact`, so `image_tag_policy.origins` needs no new value.
- `${value:artifact.N.spec.image_name}` and every other image field are rejected like `spec.image_tag`, with the
  existing code `value_reference_pinnable_field`, and the hint points to the token: "use
  `${artifact:N.image_name}`". The free-form fields (`spec.configuration.*`, `spec.properties.*`, `spec.custom.*`,
  `spec.registry`, `spec.description`) stay readable through `${value:}`: they are not an image.
- The hint for `spec.image_tag` changes from "use a `store: artifact` variable" to the token.

**Changes.**

- The document walk `_documents_reachable_from_workspace()` moves out of `semantic_checks.py` into a shared
  controller module (`controllers/reachable_documents.py`), used by the token checks and by `effective_images()`, so
  "what a deployment reaches" has one answer.
- `controllers/value_references.py`: `_pin_category()` becomes `_artifact_field_rule()` / `_pin_category()`, so
  `spec.image_name` is rejected with the token hint even though no pin overrides it.

**Tests.** A token in a Helm value, a Terraform payload and a script argument each listed by `deploy images` with its
`via`; `image_tag_policy` failing on a token-delivered image; the `${value:}` rejection and its hint; a free-form
field still readable.

**Compatibility.** A solution using `${value:artifact.N.spec.image_name}` today fails `strata validate` after this.
The changelog says so, with the one-line replacement.

### 4. Tag plus digest

**Problem.** `image_tag` holds a tag or a digest. `compose_image_ref()` treats a value with `:` as a digest and joins
it with `@`, so `1.4.2@sha256:…` becomes `example/api@1.4.2@sha256:…`, which no runtime accepts.

**Decision.** `image_tag` (and an `artifacts` pin) accepts three shapes, validated:

| Value               | `image_ref`                  |
| ------------------- | ---------------------------- |
| `1.4.2`             | `name:1.4.2`                 |
| `sha256:<hex>`      | `name@sha256:<hex>`          |
| `1.4.2@sha256:<hex>` | `name:1.4.2@sha256:<hex>`    |

No separate `image_digest` field: a pin is one string, and a version document moves both together, which is the
point of pinning a digest. A tag is Docker's `[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}`; a digest is `<algorithm>:<hex>`
with at least 32 hex characters.

- `compose_image_ref()` splits at `@`: tag part with `:`, digest part with `@`.
- `${artifact:N.image_tag}` and `field: image_tag` give the whole value (`1.4.2@sha256:…`), as today; it is what the
  author pinned. Two more token and `field` values give the parts: `image_tag_name` and `image_digest` (empty
  `image_digest` is an error, like an empty tag).
- `EffectiveImage` gets `image_digest`, and `image_tag` holds only the tag part, so `image_tag_policy`'s
  `tag_pattern` and `reject_tags` judge the tag, not the digest. A new `require_digest: bool` rule fails an image
  without one. `parse_image_ref()` already reads `name:tag@digest`.
- Validation: `ArtifactSpecModel.image_tag` checks the shape at load; `check_version_pins()` checks an `artifacts`
  pin the same way (`artifact_tag_invalid`, error).

**Tests.** The three shapes through `compose_image_ref()`, the token, `store: artifact`, a Compose service and `deploy
images`; an invalid shape at load and in a pin; `require_digest`; the SBOM purl for `tag@digest`.

### 5. `registry` and `image_name` agree

**Problem.** `spec.registry` is free text. `image_ref` is `image_name[:tag]`, so a private registry must be in
`image_name` too, and the two can disagree unchecked. In `config/artifacts/example-api.yaml`, `registry` is
`registry.example.com` and `image_name` is `example/api`: the delivered reference points at Docker Hub.

**Decision.** Check it now (option B); do not compose it.

- `strata validate` warns `artifact_registry_mismatch` when `registry` is set and the host part of `image_name`
  differs from it, including when `image_name` has none. The host part follows Docker's rule: the first path segment
  when it contains `.` or `:` or is `localhost`. The message says which registry the runtime will actually pull from.
- Composing `registry/image_name` (option A) changes the delivered reference of every artifact that sets both
  today, and a pipeline that already writes the host in `image_name` would get it twice. Revisit only for a consumer
  that wants one artifact across registries (a mirror per environment), which would rather be a per-environment
  override than a field.
- `artifact.md` says plainly that `image_name` is the full path the runtime pulls, registry included.
- `config/artifacts/example-api.yaml` becomes `image_name: registry.example.com/example/api`.

**Changes.** `services/artifact_service.py` gets its first Phase 2 check (it has none today), or a model-level
check in `ArtifactSpecModel` if it needs no index; the host rule as a helper next to `parse_image_ref()` in
`utils/sbom_utils.py`.

**Tests.** Match, mismatch, no host in `image_name`, `localhost:5000/x`, a registry with a port.

### 6. Registry credentials (`integration`)

**Problem.** `spec.integration` must name an existing integration, and nothing reads it. A pipeline copying images
before `deploy run` and a runtime pulling from a private registry get credentials elsewhere: for Helm an
`imagePullSecrets` the author writes, for Compose the `docker login` the runner already has (`docker stack deploy`
always passes `--with-registry-auth`). Strata never logs in.

**Decision.** In two steps, the second only on a real private-registry consumer.

1. **Now: say which integration, never the credential.** `deploy images` gets an `integration` column and
   `data.images[].integration`, from the artifact's `spec.integration`, so a copy pipeline knows which credentials to
   use. Nothing is resolved, so no secret can leak. `artifact.md` says strata never supplies pull credentials.
2. **On demand: log in for Compose.** Before a Compose deploy whose stack has an image from an artifact with an
   `integration`, run `docker login <registry> --password-stdin` with the integration's credentials, in an isolated
   `DOCKER_CONFIG`, as the Helm integration isolates its registry configuration. The integration needs a `registry`
   capability (host, username, password from a secret). Helm stays with `imagePullSecrets`: a cluster pull secret is
   cluster configuration, not something a deploy should create.

**Changes (step 1).** `EffectiveImage.integration`, `deploy images` table and JSON, `artifact.md`. Step 2 gets its
own design when triggered.

### 7. Count a `store: artifact` variable only where it is used

**Problem.** `effective_images()` reads the merged environment declarations, not the uses, so a `store: artifact`
variable nothing reads still lists its artifact, and `image_tag_policy` can fail a deployment on an image it does
not run.

**Decision.** A `store: artifact` variable counts when the deployment can receive it:

- a reached document references `${var:KEY}`; or
- the workspace has a step that receives every variable: Terraform or OpenTofu (`strata.variables`, `TF_VAR_<key>`),
  a script without `script.values` (`STRATA_VALUES`), or an `output.template` that reads `variables` dynamically.

Otherwise the artifact is not listed. Over-counting stays the rule wherever strata cannot tell, as for secrets
(`step_secrets.py`); a Terraform root that does not declare the variable still counts, because `effective_images()`
works offline from the solution and does not read `.tf` files.

**Changes.** `effective_images()`, with the token scan from design 3 and the step rule borrowed from
`step_secrets.py`'s `all_secrets` cases. **Tests:** a Helm-only workspace with an unused artifact variable lists
nothing; the same with a Terraform step lists it; a `${var:KEY}` use lists it.

### 8. Warn about an artifact nothing uses

**Problem.** An artifact no variable, service or token references is never reported, and `version update` still
offers to pin it.

**Decision.** `strata validate` warns `artifact_unused` for an artifact that no deployment's effective images list
(designs 3 and 7 make that set exact), naming the artifact. A warning, like `stale_pin`: harmless, but usually a
leftover or a typo in a reference. `version update` skips an unused artifact, with a note.

**Changes.** A solution-wide check in `semantic_checks.py` over `effective_images()` for every non-partial
deployment; `version_controller.py` skips the unused ones. **Tests:** used by one deployment only (no warning), used by
none, used through a token only.

### 9. Show both ways in the example solution

**Decision.** `config/` gains a second artifact, `portainer` (`image_name: portainer/portainer-ce`), used by the
Compose module `portainer` through `services[].artifact` instead of its literal `image`, with its pin moved from
`pins.images.portainer` to `pins.artifacts.portainer` in `versions/prd.yaml`. `example-api` keeps its
`store: artifact` variable (the Terraform case) and gains a token use once design 1 lands. `strata deploy images
prd-deployment` then shows both `via` kinds. The Helm module `authentik` stays as it is: it shows the chart-only
case, documented by design 2.

### 10. Fix the stale references

**Decision.** Every reference to `docs/work/artifact-references.md` or `docs/design/artifact-references.md` points
to `docs/archive/artifact-references.md`, and every one that describes current behaviour points to `artifact.md` or
this doc's successor design doc instead. About 20 places: docstrings in `artifact_model.py`, `artifact_service.py`,
`store_model.py`, `module_model.py`, `version_model.py`, `value_controller.py` (4), `workload_controller.py` (3),
`semantic_checks.py` and `environment_service.py`, five tests, and the docs listed by
`git grep artifact-references.md`. Done in the same change as design 1.

### Smaller notes

- **`pins.images` is keyed by service name alone** (by reading), so two modules with a service of the same name share
  one pin. No design: an artifact per image (design 9 shows how) avoids it. `version.md` gets one line saying so.
- **Only container images.** The model says "possibly a chart or other identifier later"; charts are pinned on the
  module (`pins.charts`). No consumer needs another artifact type, so no design.

## Related Decisions

- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) - the artifact kind, `store: artifact` and the
  service `artifact`
- [ADR-0019](../decisions/0019-version-pinning.md) - `kind: version` pins
- [value-token-resolution.md](../design/value-token-resolution.md) - the `${kind:KEY}` tokens and where they
  resolve
- A new ADR for design 1 (a 7th token kind, deploy time only, `store: artifact` kept for Terraform) when it is built

## Remaining Work / Open Questions

### Progress

- **Design 1, built 2026-11-10** (not released). As planned, with one difference: the `${artifact:}` entries hold
  every artifact in the solution, so a script without `script.values` sees them all in `STRATA_VALUES` (they are not
  secrets). Until design 3 lands, an image delivered only through the token is not in `deploy images`, the SBOM or
  `image_tag_policy`; release 1 must not ship without design 3.

### Implementation order

| Release | Designs                 | Notes                                                                                     |
| ------- | ----------------------- | ----------------------------------------------------------------------------------------- |
| 1       | 1, 3, 10                | The token, counting every use, rejecting `${value:}` on image fields. One breaking change (design 3). |
| 2       | 2, 9                    | Helm delivery through the token and `artifact_not_delivered`; the example solution.       |
| 3       | 5, 6 step 1, 7, 8       | Small checks and the `integration` column.                                                |
| 4       | 4                       | Tag plus digest, with `require_digest`.                                                   |
| later   | 6 step 2                | Compose `docker login`, on a real private-registry consumer.                              |

### Open questions

Decided here as recommendations, to confirm before building:

- Design 1: a blank `image_tag` is an error, not `""`. (`store: artifact` keeps returning `""`, unchanged.)
- Design 1: deploy time only, and not in `ValueResolution.values`; GitOps reads `graph.images`.
- Design 2: `artifact_not_delivered` is a warning.
- Design 3: rejecting `${value:artifact.N.spec.image_name}` is a breaking change in a minor release, accepted
  because the field it reads bypasses every image check.
- Design 4: one string for tag and digest, not an `image_digest` field.
- Design 5: check, do not compose.

## Changelog

- 2026-11-10: created from a review of artifact use in configuration.
- 2026-11-10: a design for each finding; the open questions from the review decided as recommendations.
- 2026-11-10: designs agreed. Design 1 implemented (`${artifact:NAME.FIELD}`), with its implementation plan.
