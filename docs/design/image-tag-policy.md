# Image tag policy - Design

- Status: current
- Last updated: 2026-11-01

## Overview

`spec.image_tag_policy` on a [workspace](../config/workspace.md) is an opt-in guard rail on the tag of every image
a deployment runs. It fails (or warns) when an image has no tag, a floating tag such as `latest`, a tag that does
not match a required pattern, or, optionally, a tag that does not come from a `kind: version` pin.

Without it strata resolves the tag of each image (`strata deploy images`, see
`effective-images.md` (work doc)) but never judges it: a tag-less artifact resolves to an empty
tag, and whatever consumes the value, for example a Terraform root that rejects it, fails late at `terraform plan`
without saying which image or pin is missing. The policy moves that failure to the first command, names the image
and says where to put the tag.

It follows the policy framework of [ADR-0049](../decisions/0049-strata-policy-command-group.md): one workspace
field, one evaluator, one row in the `strata policy` table, a result in the audit manifest. The user-facing
reference is "Image tag policy" in [workspace.md](../config/workspace.md).

## Current Design

### The policy

```yaml
kind: workspace
spec:
  image_tag_policy:
    enforcement: deny                # deny (default) | warn
    origins: [artifact]              # artifact | module-service | chart; default: all three
    require_tag: true                # default true
    reject_tags: [latest, main]      # default: the mutable alias set; [] rejects none
    tag_pattern: '^\d+\.\d+\.\d+$'   # a tag must match it (searched, so anchor it)
    require_pin: false               # the tag must come from a kind: version pin (or --pin)
    require_digest: false            # the image must carry a digest
    rings: [acc, prd]                # only deployments in these rings; default all
    allow:
      - name: redis                  # a row name (artifact, service, or REFERENCE/CONTAINER for a chart)
        reason: third-party image, covered by cve_policy
        expires: 2026-12-31          # optional ISO date, honoured through that day
```

`image_tag_policy: {}` is already useful: every image needs a tag and the mutable aliases are rejected. The model
is `ImageTagPolicyModel` in `models/image_tag_policy_model.py`. The default alias set is `MUTABLE_ALIAS_TAGS` in
`utils/sbom_utils.py` (`latest`, `main`, `master`, `dev`, `develop`, `staging`, `edge`, `nightly`, `canary`), and a
drift test ties the list in the `reject_tags` description to it.

### Rules

`evaluate_image_tag_policy(images, policy, ring)` in `controllers/image_tag_policy.py` is pure: it reads the rows
`effective_images()` returns, the policy and the ring, and returns `Diagnostics`. Per row whose `origin` is
selected and whose name is not allowed:

| Rule             | Fails when                                     | Code                   |
| ---------------- | ---------------------------------------------- | ---------------------- |
| `require_tag`    | `image_tag` and `image_digest` are both unset  | `image_without_tag`    |
| `reject_tags`    | the tag is in the list (case-insensitive)      | `image_floating_tag`   |
| `tag_pattern`    | the tag does not match the regular expression  | `image_tag_pattern`    |
| `require_pin`    | `tag_source` is not `pin`                      | `image_not_pinned`     |
| `require_digest` | `image_digest` is unset                        | `image_without_digest` |

- A row carries the tag part in `image_tag` and the digest part in `image_digest`
  (docs/work/artifact-gap-analysis.md, design 4). A digest (`sha256:...`) is immutable: a digest alone satisfies
  `require_tag`, and `reject_tags` and `tag_pattern` judge only a tag part. Every row still has to satisfy
  `require_pin`, except a `chart` row.
- **Chart rows.** Given a build, `check_image_tag_policy()` adds a row per container a rendered chart runs, read
  from `sbom.json` (`rendered_chart_images()`, `with_rendered_images()` in `controllers/effective_images.py`):
  origin `chart`, name `REFERENCE/CONTAINER`, `tag_source: chart`. `require_pin` does not apply to them, since
  strata cannot pin an image inside a chart, and the fix names the chart's values. A declared row with the same
  purl is judged once, as delivered, and a Helm module with a rendered row is no longer unknown. An SBOM that
  cannot be read is a warning (`image_tag_policy_sbom_unreadable`), and the solution is still judged. Design and
  history: `helm-image-delivery.md` (work doc), plan item 9.
- A tag that merely is not a semantic version, such as a build number (`env_sbx_20260907.11`), is not floating.
  `reject_tags` is an explicit list and `tag_pattern` is how to be stricter. This matches how the SBOM treats a
  pinned tag.
- An image with no tag gets one finding, not also `image_not_pinned`, unless `require_tag` is off.
- Each finding names the image, its origin and what uses it, and says where to put the tag:
  `spec.pins.artifacts.<name>` of a version document, or `spec.pins.images.<name>` for a module service.
- `deny` makes a finding an error, `warn` a warning. There is no `on_missing_data`: the rules read only the
  solution, so nothing can be missing.
- An `allow` entry exempts one image from every rule and needs a `reason`. Once `expires` has passed it no longer
  exempts the image, and the finding says so. An unquoted YAML date is accepted.
- A deployment whose version document has a ring outside `rings` is not judged and reports one info finding
  (`image_tag_policy_not_applied`). A deployment with no ring is always judged.

### Where it runs

| Command                | Behavior                                                                                                                                                                                                                                                                                                   |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `strata build run`     | At the end, right after `sbom.json` is written and before the CVE gate, so it also judges the images rendered charts run (`sbom.render_charts`). A `deny` fails the build with its output on disk, and the result is recorded. Under `--dry-run` it judges the declared images, since nothing is rendered. |
| `strata deploy run`    | Before any value is resolved, with the deployment's own images and the images the build's rendered charts run, read from its `sbom.json`. Also under `--smoke-test`, which records nothing. Replaces the build's result with a `deploy` phase one.                                                         |
| `strata deploy images` | The policy replaces the plain missing-tag warning, and `deny` exits 3, so an image copy step at the start of a pipeline fails first. It reads no build, so it judges the declared images only.                                                                                                             |
| `strata policy check`  | Evaluates it offline, phase `build`, honours `--pin`, and records the result. With a build at `--build-path`, it also judges that build's rendered chart images and says which `sbom.json` they came from. `policy list` shows it. There is no `policy findings` row.                                      |
| `strata validate`      | Not evaluated: the effective tag depends on the deployment and on `--pin`.                                                                                                                                                                                                                                 |

`check_image_tag_policy(context, deployment, workspace, images=..., build_path=...)` is the one entry point.
`build run` resolves `effective_images()` once and passes the rows, so they are not resolved twice. With
`build_path`, the rows `rendered_chart_images()` reads from the build's `sbom.json` are judged too (origin
`chart`), and an info finding, `image_tag_policy_rendered_images`, names that file and when it was written. The
result is a `ManifestPolicyResultModel` (`policy_name: image_tag_policy`, `policy_type: image_tag`) in
`policy_results.json`, and the audit manifest picks it up. `build run` writes it with the other build-phase
results at the end, a denied build's included.

### Version documents

`strata version update` offers an artifact with no `image_tag` and no pin as `needs_value` (finding `pin_needed`, a
warning, an error under `--check`), so the version document gate and this policy agree. It is never seeded: there
is nothing honest to seed it with.

## Related Decisions

- [ADR-0049](../decisions/0049-strata-policy-command-group.md) - `strata policy` and the dispatch table; adding a
  policy is one field, one evaluator, one row
- [ADR-0047](../decisions/0047-deploy-security-gates-as-vendor-neutral-capabilities.md) - the deploy-time gates and
  the `deny | warn` convention
- [ADR-0019](../decisions/0019-version-pinning.md), [ADR-0026](../decisions/0026-pinnable-artifact-references.md) -
  pins and artifacts
- `policy-engine-architecture.md` (work doc) - bespoke policies, no generic engine
- `effective-images.md` (work doc) - the resolver this policy reads

## History

- The opt-in is a workspace field, beside `cve_policy` and the gates, because that is where a guard rail already
  lives, it is per workspace, and it gets `policy list/check`, results and the audit record for free. Rejected: a
  flag on `kind: artifact` (misses module service images and scatters one rule over many documents),
  `Configuration.spec.security` (solution-wide, no phases or enforcement levels), a section of `kind: version`
  (mixes pins with policy, and a deployment without a version would have no policy), and `strata validate` (the
  effective tag depends on the deployment and on `--pin`).
- Named `image_tag_policy`, not `image_policy`, to stay clear of `image_gate_policy`, which gates on a vendor's open
  issues for the images in `sbom.json`.
- A workspace is shared by every ring, so it cannot be strict on prd and lenient on dev by itself. A `rings`
  selector on the policy, using the ring of the deployment's version document, was chosen over a per-deployment
  override, which would be a new mechanism for every policy and cuts across the "rebind only, no field patches"
  rule in `workspace-overrides.md` (work doc). A deployment with no ring is judged (fail
  closed).
- "Floating" is the explicit alias list, not "anything that is not a semantic version" as the SBOM treats a
  declared tag: a strict default trains people to switch the policy to `warn`. `tag_pattern` is the opt-in for more.
- It runs at both build and deploy because `--pin` given only to `deploy run` can change the tag. The deploy result
  replaces the build's for the same policy, so the audit record shows what was in effect at deploy.
- `build run` used to judge before it cleaned or wrote, so a breach failed fast with nothing changed, but a
  denied build recorded no result in `policy_results.json`. Since 2026-11-12 it judges at the end, after the
  SBOM, so it also sees the images rendered charts run (`helm-image-delivery.md` work doc, plan item 9), and a
  denied build records its result. To fail fast, run `strata deploy images` or `strata policy check` before
  `build run`: both judge the declared images offline in seconds. Whether a denied build should reach the
  audit trail is tracked in `denied-build-audit.md` (work doc).
- `deploy images` used to only warn on a missing tag and said nothing about `latest`. With the policy set it now
  fails on `deny`; without the policy it behaves as before.
- Both origins are covered by default, so an untagged third-party module image such as `redis` is flagged, which is
  the point of opting in. `origins` narrows it and `allow` exempts one image.
- Dogfooded on the `config/` example, which sets `image_tag_policy: {}` and has module service images plus one
  artifact (`example-api`, declared `1.0.0`, pinned `1.4.2` by the `prd` version document, reached by a
  `store: artifact` variable). Both tag sources pass. In a copy with the artifact's tag removed, a deployment with
  no version fails with `image_without_tag` and the fix hint, passes under `--pin prd`, and `version update --check`
  reports `needs_value`. A `redis:latest` fails `deploy images`, `build run` and `build run --dry-run` with exit 3
  and no build directory, and `require_pin: true` flags the declared-only images. The example's own tests build it
  and assert the pinned tag reaches `strata.variables` and that the policy result is recorded.
- `version update` used to drop an artifact with no tag from its candidates, as it does a module service with no
  image, because there is nothing to seed. That left an artifact with no tag at all invisible to `--check`, so
  artifacts alone are now reported as `needs_value`.
- Not built: itemised `policy findings` for this policy (the diagnostics already name every image), and tag
  checks in `strata validate`.
- The images a Helm chart deploys by itself were out of scope at first. strata does not write a Helm module's
  service `image` into the chart values (v1 did not either), so for a Helm module the policy judged the declared
  image and could not see an image only the chart knows. Since 2026-11-11 the policy says so instead of staying
  silent:
  - a declared Helm image's finding says "declared only";
  - a Helm module that declares no image is reported as `image_unknown_to_strata`, as `on_unknown_image` decides.

  Since 2026-11-12, with `sbom.render_charts`, it judges the images the rendered charts run (the `chart` origin),
  which moved the build check to the end of `build run`. See `helm-image-delivery.md` (work doc), plan item 9.
