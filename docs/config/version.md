# Version (`kind: version`)

Pinned tool/image/chart/remote versions with rationale. The single place an operator changes to
perform an upgrade — every other document declares *what* it needs; this one declares *which
version of it*. A pin's `reason` is schema, not a comment, so tooling that rewrites `version`/
`available` can never silently erase why a pin is held.

Only ported the one shape a real census found actually used — v1's `version`/`version-lock`
promotion/rollback/canary/floating-pin machinery is not carried over (zero real usage found).

## Schema

- `spec.pins.images` — replaces a module service's whole `image`, keyed by the service name. The value is a whole
  image reference with a tag, a digest or both (`docker.io/library/postgres:16-alpine`, `postgres@sha256:<hex>`),
  never a tag alone: `db: 16-alpine` would run the image `16-alpine`. Another value is an error
  (`image_pin_invalid`), and `strata version set` refuses to write it. `available` is free text
- `spec.pins.charts` — Helm chart version pins, keyed by chart/module name
- `spec.pins.remotes` — overrides a [solution.md](solution.md) remote's `reference` (only valid
  when that remote is `fetch: strata` — an `fetch: external` remote is placed by CI before strata
  runs, so pinning it can't do anything)
- `spec.pins.artifacts` — overrides an [`artifact`](artifact.md) document's `image_tag`: a tag, a digest
  (`sha256:<hex>`) or both (`1.4.2@sha256:<hex>`, see [Tag and digest](artifact.md#tag-and-digest)). Another
  shape is an error (`artifact_tag_invalid`), and `strata version set` refuses to write it
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
      # Shorthand: nothing is held back. Keyed by the module service's name.
      portainer: portainer/portainer-ce:2.39.3-alpine
      db:
        version: docker.io/library/postgres:16-alpine
        status: held
        available: 18.6-alpine
        reason: "postgres majors need pg_upgrade/dump-restore; plan as its own migration task"
        reviewed: 2026-09-08
    charts:
      # Keyed by the chart module's document name.
      authentik:
        version: 2026.5.2
        status: unverified
        reason: "chart version could not be confirmed upstream; re-verify before bumping"
        reviewed: 2026-09-08
    artifacts:
      # Overrides `image_tag` of the artifact named example-api.
      example-api: 1.4.2
    remotes:
      infra: v1.0.0
    assets:
      contoso-api: v1.5.0
```

This is `config/versions/prd.yaml`, the working example in this repository.

## Several documents per deployment

A [`deployment`](deployment.md)'s `spec.version` names one version document or a list of them, and the pins of
every listed document apply together. That lets several products in one ring share what they have in common
without copying it: a shared document holds the common pins, and each product keeps its own.

```yaml
# versions/prd-core.yaml - the shared part, listed by every product's prd deployment
spec:
  pins:
    remotes: { iac: v2.1.0 }
---
# versions/prd-datahub.yaml - one product's pins; its tag says which ring it is
spec:
  promotion: { ring: prd, order: 3 }
  pins:
    artifacts: { datahub-api: 1.4.0 }
---
# deployments/base-prd.yaml - through extends, version lists append (base first)
spec:
  partial: true
  version: [prd-core]
  environments: [prd]
---
# deployments/datahub-prd.yaml - effective: [prd-core, prd-datahub]
spec:
  extends: base-prd
  workspace: datahub
  version: [prd-datahub]
```

Every pin has **one owning document per deployment**. `strata validate` checks each deployment's listed documents
together; the findings are listed under [Findings](#findings). How it works, and why it is built this way:
[version composition](../design/version-composition.md).

- Which targets a deployment uses: its workspace's enabled module services (`images`), chart modules (`charts`),
  every remote a reached document names, every artifact a service, `${artifact:}` token or `store: artifact`
  variable names, and every asset its execution steps select.
- `--pin NAME` on `build run`, `deploy run`, `deploy images`, `policy` and `integration` is repeatable, and what
  it names replaces the deployment's whole list for that run. Leaving out a document that pins something the
  deployment uses is a warning (`version_pin_dropped`): that target falls back to its own declared value.
- Pin logging names the document each pin came from. `build run` writes the build's own `version.yaml` (an
  ordinary version document with only the pins it used) next to `resolved.yaml`, whose `version` lists the
  documents, `pinned_by` says `spec.version` or `--pin`, and `pin_sources` names each pin's document. `deploy run`
  compares a Helm build's chart and artifact pins with `version.yaml` by value (`build_version_mismatch`).
- `strata promote status WORKSPACE` reads the workspace's deployments: its rows are the pins they run, with the
  owning document (marked `shared` when deployments of another workspace list it too). `promote apply` writes each
  pin into its owner, and skips a shared document unless `--shared` is given, and a document only some of the
  ring's deployments list (a customer's own). Without a workspace, `promote status --ring prd` shows what the whole
  ring runs, every workspace in one table with a workspace column.
- `strata version show NAME` lists a document's pins and the deployments that list it.

### No `spec.workspace`

A version document names no workspace: the deployments that list it decide where it applies, so one document can
be shared by deployments of several workspaces. `spec.workspace` was removed in strata 2.7: a document that still
declares it fails to load, with a message saying to delete the line. (ADR-0063, ADR-0064, ADR-0065.)

## Findings

What strata reports about version documents and the deployments that list them. Every finding below carries a
`hint`: the console prints it under the finding (`hint: ...`), and JSON output has it as `diagnostics[].hint`.

| Code                           | Severity    | When                                                                                                  | What to do                                                                                     |
| ------------------------------ | ----------- | ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `version_pin_conflict`         | error       | two documents one deployment lists pin the same target, even with equal values                        | remove the pin from one; a pin several products share belongs in a shared document they all list |
| `version_ring_conflict`        | error       | two documents one deployment lists are tagged for different rings                                     | tag only the ring's product document; leave shared documents untagged                          |
| `build_version_mismatch`       | error       | `deploy run` of a Helm build whose chart or artifact pins differ from the build's `version.yaml`      | rebuild with the same versions, or deploy what the build holds                                 |
| `version_document_unused`      | warning     | a document the deployment lists itself pins nothing it uses, while other deployments do               | list this workspace's document instead (`strata version show` says who uses which)             |
| `version_listed_twice`         | warning     | one written list names a document twice (repeating a document the base lists is fine)                 | remove the repeat                                                                              |
| `version_ring_drift`           | warning     | deployments of one ring run a shared (untagged) document's pin at different values                    | list one shared document in every deployment of the ring                                       |
| `version_pin_dropped`          | warning     | a `--pin` list leaves out a document that pins something the deployment uses                          | add that document to `--pin`                                                                   |
| `version_pin_unused`           | information | a pin none of the deployments listing the document uses                                               | nothing for a pin staged ahead of its use; otherwise remove it                                 |
| `version_ring_none`            | information | a deployment in no ring while others of its workspace are in a rollout                                | list the ring's tagged document too, or leave it out on purpose                                |
| `version_document_unlisted`    | information | an untagged document no deployment lists (a tagged one is `promotion_orphan_ring`)                    | nothing if CI uses it through `--pin`; otherwise list it in a deployment, or delete it         |
| `stale_pin`                    | warning     | a pin names no module service, chart module, remote, artifact or asset                                | remove it (`strata version update` proposes this)                                              |

## Notes

- **A pin may only target something strata itself materializes.** Tool binaries (Terraform, Helm)
  are placed by CI before strata runs, so there is no `pins.tools` category — the version a recipe
  expects lives on the [`provisioner`](workspace.md)'s `integration` binding instead, as an
  assertion strata checks, not an install instruction.
- `strata validate` checks pin existence/applicability: a pin naming no module service, chart
  module, remote, artifact or asset is a `stale_pin` warning. An `images` pin is also a warning when it names
  another image than the service runs (`image_pin_renamed`; `postgres` and `docker.io/library/postgres` are the same
  image), or when the service declares no `image` (`pin_not_applicable`: nothing to replace; a service with an
  `artifact` is pinned under `artifacts`). `images`, `charts`, `remotes` and
  `assets` pins are applied by `build run`; `artifacts` pins are applied wherever the artifact is
  resolved (`store: artifact`, build and deploy). `--pin NAME` on `build run`/`deploy run`
  overrides a deployment's `spec.version` for that run only (repeat it for several documents).
- A target with no current value of its own (a module service with no `image`) is never seeded by
  `version new --from` or offered as a pin to add by `version update`: a pin must have a non-empty
  value. An artifact with a blank `image_tag` is the exception for `version update`: with no pin it
  has no tag at all, so it is reported as needing a version (`pin_needed`), though still never seeded.
  An artifact no document references is not offered at all; `version update` names it once
  (`artifact_unused`). One that is referenced but not deployed yet (staged) is offered as usual.
  A pin it already has is not stale and stays.
- `strata version update NAME` lists the pins you could add (each target with no pin yet, seeded with
  its current value) and the pins to remove (stale ones). It never edits the file and exits 0, since
  a target with no pin is normal. Add `--check` to exit 3 when anything is left to add or remove, for a
  CI gate. An artifact with no tag and no pin is a warning even without `--check`, and an error with it.
  When deployments list NAME, only the targets they use are offered, and none another of their listed documents
  already pins; a document no deployment lists yet is compared with the whole solution.
