# How To: Share version pins between products in one ring

A ring runs several products, each in its own workspace: Staging, DataHub, Binocs. They have something in
common, such as the remote their infrastructure code is fetched from or a shared image, and each has its own
images and artifacts. This guide pins the common part **once**, in a shared version document, and gives each
product its own document, so a product moves on its own while the shared part can never run at two values inside
one deployment.

A deployment's `spec.version` names one [version](../config/version.md) document or a list of them, and their
pins apply together. Each pin has one owning document: two listed documents pinning the same target is an error.

## The documents

```yaml
# versions/prd-core.yaml - the shared part: untagged, listed by every product's prd deployments
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: prd-core
spec:
  pins:
    remotes: { iac: v2.1.0 }
---
# versions/prd-datahub.yaml - one product's pins; its tag says which ring it is
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: prd-datahub
spec:
  promotion: { ring: prd, order: 3 }
  pins:
    artifacts: { datahub-api: 1.4.0 }
```

A version document names no workspace: the deployments that list it decide where it applies. A shared document
has no `promotion` tag, so it never decides a ring; the product document's tag does.

Each product's deployments get the shared document from a base and add their own:

```yaml
# deployments/base-prd.yaml
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

Through `extends` the lists append, base first, each document once.

## Creating and filling the documents

```
strata version new prd-core
strata version new prd-datahub          # then add promotion: { ring: prd, order: 3 }
strata version update prd-datahub       # proposes only what DataHub's deployments use and prd-core does not pin
strata version set prd-core remotes iac v2.1.0
strata version set prd-datahub artifacts datahub-api 1.4.0
strata version show prd-core            # its pins, and the deployments that list it
```

## What `strata validate` checks

For each deployment, the pins of its listed documents together:

| Mistake                                                    | Finding                              |
| ---------------------------------------------------------- | ------------------------------------ |
| `prd-datahub` also pins `remotes/iac`                      | error `version_pin_conflict`         |
| `datahub-prd` lists `prd-staging` instead of `prd-datahub` | warning `version_document_unused`    |
| a product document and the shared one tagged for different rings | error `version_ring_conflict`  |
| `staging-prd` lists an older shared document               | warning `version_ring_drift`         |
| a pin no deployment listing the document uses              | information `version_pin_unused`     |

## Building and deploying

`build run` applies the pins and logs, for each, the document it came from. It writes `version.yaml` (an
ordinary version document with the pins this build used) next to `resolved.yaml`, which records the documents
(`version`), whether `--pin` chose them (`pinned_by`) and where each pin came from (`pin_sources`).

To try other documents for one run, repeat `--pin`. What you pass replaces the deployment's whole list:

```
strata build run datahub-prd --pin prd-core --pin prd-datahub-rc
```

Leaving `prd-core` out would let `iac` fall back to the remote's own reference; the build warns
(`version_pin_dropped`).

## Promoting one product

`strata promote status datahub` shows, per ring, the pins DataHub's deployments run, with the document that owns
each (`shared` when other workspaces list it too). `strata promote apply datahub prd` copies the preceding ring's
values into the documents that own them in `prd`, and skips the shared `prd-core`, so promoting DataHub never
moves Staging. Move the shared part when every product is ready for it:

```
strata promote apply datahub prd --shared
```

To see what the whole ring runs, every product in one table:

```
strata promote status --ring prd
```

## A customer that differs

A customer's deployment lists another document for the part that differs, instead of a copy of the whole ring:

```yaml
spec:
  extends: base-prd                    # [prd-core]
  workspace: datahub
  version: [prd-datahub-fabrikam]      # instead of prd-datahub
```

`strata version new prd-datahub-fabrikam --from prd-datahub` starts that file. `promote apply` never writes a
customer's own document.
