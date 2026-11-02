# How To: Model Azure Private DNS Zones (VNet Links + Externally-Owned Zones)

You are driving an Azure private DNS Terraform component (e.g. AKS's private cluster zone, an
application zone linked to a VNet, or a hub-owned zone a spoke only writes a few records into)
from strata, and `kind: dns`'s schema has no `vnet_id`/`private`/`external_id`-shaped fields.
This guide covers why that's intentional and how to model both patterns today with
`spec.zones[].configuration` — strata's provisioner passthrough field — instead of a dedicated
schema.

## The short answer

**`spec.configuration` is not a stopgap — it's the correct, permanent place for this.** A real
Azure private-DNS Terraform component's actual contract (checked directly against a live
consumer) is just a few named inputs on top of `kind: dns`'s existing `records[]`:

| Concept                                       | Real Terraform input shape     | Where it goes in strata                               |
| --------------------------------------------- | ------------------------------ | ----------------------------------------------------- |
| VNet to link the zone to                      | `vnet_id` (string, one ARM ID) | `spec.zones[].configuration.vnet_id`                  |
| Zone created in this subscription             | `local_zone = { name }`        | the zone's own `name` + `records[]` (already modeled) |
| Zone owned elsewhere, records written into it | `hub_zone = { resource_id }`   | `spec.zones[].configuration.hub_zone.resource_id`     |

No new top-level fields needed, and nothing is lost by using `configuration` instead:
`${var:}`/`${secret:}`/`${feature:}`/`${output:...}` tokens inside it resolve exactly the same
way they would in a dedicated field — `resolve_value_tokens_in_mapping()` and the
`${output:...}`-ownership safety check (`_contains_output_token()`) both recurse generically
into every nested `dict`/`list`, not just known top-level keys.

## Why not a dedicated `private`/`vnet_links`/`external_id` schema?

Checked against the real, named consumer's Terraform component before deciding: its actual
`variables.tf` takes a single `vnet_id` string and a `hub_zone = { resource_id }` object — not
the generalized `vnet_links: list[str]`/`external_id: str` shape a first pass at this schema
proposed. Minting fields that don't match any real module's actual contract would mean
*more* translation work for whoever owns that Terraform module, not less — they'd have to map
strata's reshaped names back onto their own `variables.tf` by hand, in both directions.

Every other "promote this to a real schema field" decision in strata's model layer (see
[ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md) on `output_key`, or the
`chart_repository` → named `remotes` consolidation) was made only after finding **two or more**
independently-written real consumers converging on the same shape. This has exactly one real
consumer so far — the bar isn't met yet. Revisit if a second, independently-authored Azure
private-DNS Terraform component needs the same shape.

## Worked example 1 — zone strata owns, linked to a VNet

A zone created and fully owned by this deployment (e.g. an application domain for an AKS
cluster), linked to the spoke's own VNet:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: dns
meta:
  name: spoke-app-dns
spec:
  zones:
    - name: apps.internal.example.com
      ttl: 3600
      configuration:
        vnet_id: "${output:vnet.vnet_id}"
      records:
        - name: grafana
          type: A
          value: "${var:APP_LB_IP}"
      default_tags:
        environment: prd
```

`vnet_id` here is a `${output:...}` token because, in this mapping, the VNet is created by a
*different* strata execution step (`vnet`) than the one that applies this `dns` document —
`dns`'s step must `depends_on: [vnet]` and list this document under its own `targets` so the
existing ownership-claiming check (gap #12) accepts the token. If your workspace instead
provisions the VNet and the DNS zone in the *same* Terraform root/step (as the real reference
consumer actually does — `module.vnet` and `module.dns` wired together in one `main.tf`), there
is no cross-step reference at all: `vnet_id` is pure intra-root HCL the Terraform module itself
resolves, and `configuration` doesn't need to carry it — don't invent a token where the real
module has none.

## Worked example 2 — externally-owned hub zone, records only

A zone owned by a separate, centrally-managed hub subscription. This deployment doesn't create
it — it only links its own VNet to it and writes a couple of its own records:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: dns
meta:
  name: spoke-hub-dns
spec:
  zones:
    - name: hub.internal.example.com   # documentation only — nothing is created with this name
      configuration:
        vnet_id: "${var:SPOKE_VNET_ID}"
        hub_zone:
          resource_id: "${secret:HUB_DNS_ZONE_RESOURCE_ID}"
      records:
        - name: grafana
          type: A
          value: "${var:APP_LB_IP}"
      # No default_tags/custom_tags — a zone this deployment doesn't own has no cloud-resource
      # identity here to tag. ttl is still fine to set: it's a default for the records THIS
      # document writes, not a property of the zone resource itself.
```

`vnet_id`/`hub_zone.resource_id` are `${var:}`/`${secret:}` tokens here, not `${output:...}` —
the hub's VNet and zone are long-lived infrastructure created entirely outside this workspace's
own execution graph, so from strata's point of view they're just already-known constants, the
same as `environment_info.subscription_id` already is. A Value token is opaque: it never needs
to understand that the string happens to be an ARM resource ID.

## Mapping this onto strata

- `spec.zones[].configuration` — raw, provisioner-specific passthrough (`kind: dns`'s own
  schema reference: [config/dns.md](../config/dns.md)). Not validated by strata beyond
  Value-token syntax; the Terraform module's own `variables.tf`/`precondition` blocks are the
  real contract and the real guardrails.
- `${output:step.key}` vs. `${var:}`/`${secret:}` — use `${output:...}` only when the value is
  produced by a *different strata execution step* in the *same* workspace that this document's
  own step `depends_on`; use `${var:}`/`${secret:}` for anything externally-known (hub
  infrastructure, values supplied via environment/secret stores). See
  [value-token-resolution.md](../design/value-token-resolution.md) for the full token model.
- Nothing about this pattern is Azure-specific at the strata-schema level — the same
  `configuration` passthrough convention applies equally to a Cloudflare/Route53 provider's own
  zone-association concepts, whatever shape those turn out to need.
