# How To: Model your own structured data in `properties` and enforce its shape

Strata has no kind for every organisation-specific concept (a secret request, a change ticket, a
cost centre). You do not need one. `properties` is a free-form block that merges across
layers and reaches Terraform as `strata.properties`; an [`attributeconfig`](../config/attributeconfig.md)
document turns it into a typed, validated contract. Strata stays out of the domain; your
Terraform does the work.

This guide uses one worked case: **secret requests** for an app registration whose secret is
created by a separate Terraform state.

## The case

Terraform creates an app registration. A second state, with its own CI/CD run, creates the
secret for it. Each secret must say:

- what it is for (the description written on the credential)
- who asked for it
- which Key Vault stores it
- which integration is used for the handover

## 1. Put the data in `properties`

Use a **map keyed by name, not a list**. Properties are deep-merged tenant, then environment,
then deployment, and the merge replaces lists wholesale (`strata.utils.dict_merge.deep_merge`).
With a list, a deployment that adds one request would silently discard the environment's.
With a map, each request merges per key.

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  properties:
    secret_requests:
      contoso-web-client:
        description: "Client secret for contoso-web (prd)"
        requested_by: jane.doe@example.com
        keyvault: kv-contoso-prd
        handover: mail-handover
        expires_in_days: 180
```

Terraform reads `strata.properties.secret_requests` and uses `for_each` over it in the secret
state. Strata does not interpret the values.

## 2. Declare the contract

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: environment-secret-requests
spec:
  target:
    kind: environment
    block: properties
    view: document
  schema:
    type: object
    properties:
      secret_requests:
        type: object
        additionalProperties:
          type: object
          required: [description, requested_by, keyvault, handover]
          additionalProperties: false
          properties:
            description:
              type: string
              minLength: 10
            requested_by:
              type: string
              pattern: "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"
            keyvault:
              type: string
              pattern: "^kv-[a-z0-9-]+$"
            handover:
              type: string
            expires_in_days:
              type: integer
              minimum: 1
              maximum: 730
```

`strata validate` now fails on a request with no `requested_by`, an unknown field, or a vault
name outside your convention. The schema is verbatim JSON Schema, so `enum`, `pattern` and
`if`/`then` all work.

## Choosing `view`

- `document` checks each environment document's own block. Use it when the shape must hold
  wherever the data is written.
- `effective` (target `deployment` only) checks the merged result per deployment. Use it for
  rules that need the final picture, such as "every deployment of `prd` has at least one request".

## What this does not do

- It checks the **shape**, not whether `handover: mail-handover` names a real integration or
  `kv-contoso-prd` exists. Your Terraform or pipeline must check that.
- It never validates `secrets`. `target.block` cannot be `secrets`, and a secret *value* does
  not belong in `properties`: properties are plain, unmasked values, unlike `${secret:KEY}`
  which is never written to disk. Keep only the request metadata here and let the secret
  itself live in the vault.
- `additionalProperties: false` closes the subtree. Leave it out where other teams may add keys.

## Checklist

1. Data goes in a map keyed by name, not a list.
2. One `attributeconfig` per `(kind, block, view)` for a given top-level key. Two documents
   declaring the same key, with overlapping selectors, fail with `attributeconfig_conflict`.
3. Run `strata validate --verbose` to see which documents each schema was applied to. A schema
   matching nothing warns with `attributeconfig_unused`.
