# Version (`kind: version`)

Pinned tool/image/chart/remote versions with rationale. The single place an operator changes to
perform an upgrade — every other document declares *what* it needs; this one declares *which
version of it*. A pin's `reason` is schema, not a comment, so tooling that rewrites `version`/
`available` can never silently erase why a pin is held.

Only ported the one shape a real census found actually used — v1's `version`/`version-lock`
promotion/rollback/canary/floating-pin machinery is not carried over (zero real usage found).

## Schema

- `spec.pins.images` — image tag/digest pins, keyed by name
- `spec.pins.charts` — Helm chart version pins, keyed by chart/module name
- `spec.pins.remotes` — overrides a [solution.md](solution.md) remote's `reference` (only valid
  when that remote is `fetch: strata` — an `fetch: external` remote is placed by CI before strata
  runs, so pinning it can't do anything)
- `spec.pins.artifacts` — overrides an [`artifact`](artifact.md) document's `image_tag`
- `spec.pins.assets` — overrides the git `reference` of an [`asset`](asset.md) that declares its own
  `spec.location`, keyed by the asset's name (only valid when its `location.fetch` is `strata`; an
  asset using a manifest remote is pinned under `remotes`)

Each pin is either a bare string (shorthand — nothing held back) or structured:

- `version` — the pinned value
- `status` — `current` (default), `held`, or `unverified` — the latter two **require** `reason`
- `available` — the version known to be available upstream (refresh tooling may rewrite this freely)
- `reason` — why the pin is held/unverified — never rewritten by tooling
- `reviewed` — date the pin was last looked at

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: prd
spec:
  pins:
    images:
      caddy: caddy:2-alpine
      db:
        version: docker.io/library/postgres:16-alpine
        status: held
        available: 18.6-alpine
        reason: "postgres majors need pg_upgrade/dump-restore; plan as its own migration task"
        reviewed: 2026-09-08
    charts:
      immich: 0.13.1
    remotes:
      infra: v1.0.0
    assets:
      c0062-api: v1.5.0
```

## Notes

- **A pin may only target something strata itself materializes.** Tool binaries (Terraform, Helm)
  are placed by CI before strata runs, so there is no `pins.tools` category — the version a recipe
  expects lives on the [`provisioner`](workspace.md)'s `integration` binding instead, as an
  assertion strata checks, not an install instruction.
- `strata validate` checks pin existence/applicability: a pin naming no module service, chart
  module, remote, artifact or asset is a `stale_pin` warning. `images`, `charts`, `remotes` and
  `assets` pins are applied by `build run`; `artifacts` pins are applied wherever the artifact is
  resolved (`store: artifact`, build and deploy). `--pin NAME` on `build run`/`deploy run`
  overrides a deployment's `spec.version` for that run only.
- A target with no current value of its own (a module service with no `image`, an artifact with a
  blank `image_tag`) is never seeded by `version new --from` or offered by `version update`: a pin
  must have a non-empty value.
- `strata version update NAME` lists the pins you could add (each target with no pin yet, seeded with
  its current value) and the pins to remove (stale ones). It never edits the file and exits 0, since
  a target with no pin is normal. Add `--check` to exit 3 when anything is left to add or remove, for a
  CI gate.
