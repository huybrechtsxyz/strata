# Provider (`kind: provider`)

A cloud provider instance: which provider type, which region, and provider-level defaults
(tags, passthrough configuration). One document per real account/subscription/region
combination a workspace provisions into.

## Schema

- `spec.properties.type` — provider type (must match a [providerconfig.md](providerconfig.md) document's `meta.name`)
- `spec.properties.region` — region/datacenter (cross-checked against that `ProviderConfig`'s `spec.regions`)
- `spec.properties.display_name` — optional human-readable label, purely cosmetic
- `spec.authentication` — optional; one of several supported methods (OAuth2, API key,
  certificate/mTLS, managed identity, CLI-based, ...)
- `spec.configuration` — raw passthrough to the provisioner (e.g. extra Terraform provider-block
  arguments), never validated by strata
- `spec.default_tags` — default cloud tags applied to every resource this provider creates
- `spec.custom` — free-form data for external tooling, not consumed by any provisioner
- `spec.lifecycle` — optional IaC workflow hook phases

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-main
spec:
  properties:
    type: azure
    region: westeurope
    display_name: "West Europe"
  default_tags:
    managed-by: strata
```

## Notes

- No `spec.references` (unlike v1): a provider no longer declares required variable/secret *key
  names* separately — any value that needs to vary by environment is a `${var:}`/`${secret:}`/
  `${feature:}` token directly in the relevant field, checked against a real `Environment` in
  Phase 2 (see [environment.md](environment.md)).
- `default_tags` is distinct from `meta.tags` — the former are real cloud tags, the latter is a
  free-form list for strata-internal categorization only.
