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
```

## Notes

- **A pin may only target something strata itself materializes.** Tool binaries (Terraform, Helm)
  are placed by CI before strata runs, so there is no `pins.tools` category — the version a recipe
  expects lives on the [`provisioner`](workspace.md)'s `integration` binding instead, as an
  assertion strata checks, not an install instruction.
- `strata validate` checks pin existence/applicability today; actually applying a pin onto a
  rendered artifact isn't wired yet (no build layer consumes it).
