# Image pin validation - Work

- Status: in-progress (built 2026-11-10, not released)
- Last updated: 2026-11-10

## Overview

A pin under `version.spec.pins.images` replaces a module service's whole `image` at `build run`
(`workload_controller.py`). Until now `strata validate` only checked that the pin's key names a module service, and
`strata version set images` wrote any non-empty string. So a pin written as only the tag, `db: 16-alpine`, became the
image `16-alpine`, which a runtime pulls as `docker.io/library/16-alpine`, and nothing said so. An `artifacts` pin is
shape-checked since design 4 of [artifact-gap-analysis.md](artifact-gap-analysis.md); an `images` pin was not.

## Design

A pin's value must be a whole image reference that pins something: `NAME:TAG`, `NAME@sha256:<hex>` or
`NAME:TAG@sha256:<hex>`.

| Finding | Severity | Code | When |
| --- | --- | --- | --- |
| Not an image reference | error | `image_pin_invalid` | The name breaks Docker's rules (upper case, empty, a bad registry host), or the tag or digest does (the same rules as an artifact's `image_tag`). |
| Pins nothing | error | `image_pin_invalid` | The value has no tag and no digest. This is the `db: 16-alpine` case, and also `db: postgres`, which floats on `latest`. |
| Another image | warning | `image_pin_renamed` | The pin's image name differs from the `image` of every service with that name. Names are compared as a runtime pulls them, so `postgres` and `docker.io/library/postgres` are the same image. |
| Changes nothing | warning | `pin_not_applicable` | Every service with that name declares no `image` (a Helm service without one, or a service with an `artifact`), so `build run` has nothing to replace. |

- The renamed case is a warning, not an error: moving to a mirror or another image on purpose is legitimate, and the
  module's own `image` is the place to say so.
- The error message for a value with no tag uses the service's own image name when there is one:
  `write 'docker.io/library/postgres:16-alpine'`.
- `strata version set images NAME VALUE` refuses a value that fails the error rules, before it writes the file.
  `--available` stays free text: the shipped example holds a bare upstream tag there (`available: 18.6-alpine`), and
  nothing applies it.
- A stale pin (no such service) is still only `stale_pin`; its value is still checked.
- `strata promote status` compared pins as bare versions, so a valid image pin was never marked `← behind`. It now
  compares an `images` pin by its tag, only between siblings that name the same image
  ([promotion.md](promotion.md)). Found when the promotion tests, which pinned bare tags, failed the new check.
- Out of scope: checking a service's own `image` at load. It may legitimately have no tag on a Helm module, where it
  is only declared, and that is [helm-image-delivery.md](helm-image-delivery.md)'s concern.

## Implementation plan

1. `sbom_utils.py`: `image_ref_problem(value)`, the reference syntax (name grammar, then the tag and digest rules
   `image_tag_problem` already has); `image_pin_problem(value, name_hint=None)`, which adds "no tag or digest";
   `canonical_image_name(name)`, the name as a runtime pulls it (`docker.io/library/postgres`).
2. `version_pins.py`: the `images` branch checks the value, then compares it with the services of that name.
   `_known_service_names` becomes a map from name to services with their module.
3. `version_controller.py`: `set_version_pin` refuses an invalid `images` value.
4. Tests: the utilities; each finding; the shipped example stays clean; `version set` refuses and leaves the file
   unchanged. Fixtures that pinned a service with no image get one.
5. Docs: `docs/config/version.md`, the schema description of `pins.images`, the CHANGELOG.
6. `promotion_controller.py`: `_pin_order()` orders a pin by its tag.

## Changelog

- 2026-11-10: created, designed and built; the `promote status` ordering fix added.
