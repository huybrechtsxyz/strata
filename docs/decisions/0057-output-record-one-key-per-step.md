# The output record is one key per step

- Status: accepted
- Date: 2026-11-09
- Replaces: [ADR-0036](0036-cross-run-key-value-output-store.md) D3's "one record per deployment". The rest of D3
  (staleness is a hard failure) and all other ADR-0036 decisions stay.
- Related: [docs/design/deployment-documentation.md](../design/deployment-documentation.md) (the report record, which
  uses the same layout, and the size rules)

## Context and Problem Statement

ADR-0036 D3 stores every step's outputs for a deployment in one JSON document under one key,
`strata/deployment-outputs/<deployment>`, written by read-modify-write with compare-and-swap.

The key-value backends have different limits per key. Azure App Configuration allows 10 KB per key-value, including
the key, label, content type and tags, on every tier. Azure Blob allows 5,000 MiB per write. On App Configuration,
one key per deployment means the limit applies to all steps together: a deployment with many steps, or one large
output (a kubeconfig, a certificate), fails to record, with the backend's own error. `DeploymentOutputStore` had no
size check.

The deployment documentation work adds a report record next to it, and needed to decide its layout too.

## Decision

1. The output record is one key per step: `strata/step-outputs/<deployment>/<step>`. The value is that step's entry
   as before: `collected_at` and the non-sensitive `values`, as compact JSON. A new prefix, not
   `strata/deployment-outputs/<deployment>/<step>`: on a Blob container with a hierarchical namespace the old key
   `strata/deployment-outputs/<deployment>` is a file, and nothing can be created under it.
2. Each step writes only its own key, with a plain `put()`. There is nothing to merge, so compare-and-swap would add
   nothing: two runs of the same step at once each write a valid latest result.
3. Destroy overwrites a step's key with `{"destroyed_at": ...}` (the capability has no delete), and `read()` treats
   it as "not recorded".
4. Each key-value backend declares `MAX_VALUE_BYTES`, the largest key plus value one write may store (`None` when it
   has no practical limit): `azure-appconfig` 10,000, `azure-keyvault` 25,000, `azure-blob` none. Where a provider
   says "10 KB" without saying 1,000 or 1,024, the smaller number is used. Before a write, the record is measured,
   key included: above 8,000 bytes on a backend with a limit, the write goes ahead with a warning naming the step and
   the size; above the limit, no value is written and the error names the step, its size and the limit. `deploy run`
   reports both as warnings, as it already did for a failed output write: the step has deployed. Above the limit the
   step's key gets a small `{"omitted_at", "size"}` marker and the step is removed from the old key, so no reader
   falls back to the values of an earlier deploy: a read fails, saying why.
5. Staleness (`output_store_max_age`) applies per step key, exactly as it applied per entry.

**Transition**, removed together in a later release that says so in the changelog:

6. `read()` falls back to the old per-deployment key when a step has no key of its own, so existing deployments keep
   working without redeploying. A destroyed marker stops the fallback, or the values from before the destroy would
   come back.
7. While an older strata version may still read only the old key, `record()` also writes the step into it, with
   read-modify-write and compare-and-swap as before, but only when the old key already exists: a new deployment
   never gets one. When the old record would be too large, the step is removed from it instead, so an older strata
   finds nothing (a loud failure) rather than an outdated value (a silent one). A failed mirror is a warning.
8. `clear()` writes the destroyed marker to each step key that exists, and removes the steps from the old key as
   before.
9. `strata validate` still requires a backend with compare-and-swap (`output_store_lacks_cas`), because the mirror
   in 7 needs it. That requirement can be revisited when the transition ends.

## Consequences

- The App Configuration limit applies per step, not per deployment, and a too-large step gets a clear error.
- A deploy that consumes outputs from several steps does one `get()` per step, as it already did (each `read()` read
  the whole record).
- Two key formats exist until the transition ends; during it, each `record()` on a deployment with an old key does
  one extra read and write.
- The report record (deployment documentation work) uses the same layout under its own prefix.
