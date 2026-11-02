# Network (`kind: network`)

Virtual network topology — VPCs/VNets, subnets, and peerings. A single file can declare multiple
independently-tagged networks.

## Schema

- `spec.networks[]`, each:
  - `name` — unique within the spec
  - `address_space[]` — one or more CIDRs (literal, or `${var:}`/`${secret:}`/`${feature:}` tokens)
  - `subnets[]` — each `{name, description, cidr}`; CIDR is also a Value binding
  - `peerings[]` — optional `{name, target}` references to another network in the same spec
  - `configuration` — raw provisioner passthrough
  - `default_tags` / `custom_tags` — cloud tags
  - `custom` — free-form data

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: network
meta:
  name: main-network
spec:
  networks:
    - name: vnet-main
      address_space: ["10.0.0.0/16"]
      subnets:
        - name: app
          cidr: "10.0.1.0/24"
      default_tags:
        environment: example
```

## Notes

- When every CIDR involved is a literal (no unresolved tokens), strata validates at schema time
  that subnets don't overlap and that each subnet fits inside its network's address space. A
  network/subnet whose CIDR uses a `${var:}`/`${secret:}`/`${feature:}` token skips that check
  until the value is resolved.
- Cross-checking a network against the workspace that references it (`WorkspaceResourceSubnetModel`)
  is a Phase 2 check, not yet fully wired — see [docs/design/v2-schema-overview.md](../design/v2-schema-overview.md).
