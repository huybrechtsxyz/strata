# Network (`kind: network`)

Virtual network topology — VPCs/VNets, subnets, and peerings. A single file can declare multiple
independently-tagged networks.

## Schema

- `spec.networks[]`, each:
  - `name` — unique within the spec
  - `address_space[]` — one or more CIDRs (literal, or `${var:}`/`${secret:}`/`${feature:}` tokens).
    Optional when `ipam` is given; a network needs `address_space`, `ipam`, or both
  - `ipam` — which IPAM allocates this network's addresses: `type` (a free name such as `azure-avnm`
    or `netbox`), and optional `pool` (may hold tokens), `prefix_length` and `configuration`. Strata
    does not call the IPAM; it passes these settings to your provisioner code
  - `subnets[]` — each `{name, description, configuration, custom}` with exactly one of:
    - `cidr` — a fixed address (a literal or a Value binding), as before
    - `prefix_length` — a size (`25` is a /25); your provisioner code or an IPAM chooses the address
      at deploy

    May be empty, for example when every subnet is declared by the workloads that use the network
    (see [workspace](workspace.md) `spec.subnets[]`)
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
          cidr: "10.0.1.0/24"        # fixed address
        - name: aks-nodes
          prefix_length: 25          # size only; the address is chosen at deploy
      default_tags:
        environment: example
    - name: vnet-spoke
      ipam:
        type: azure-avnm
        pool: "${var:ipam_pool_id}"
        prefix_length: 22
```

[how-to/declare-subnets.md](../how-to/declare-subnets.md) shows the whole flow: core subnets here,
workload subnets in a workspace, and the Terraform that creates each.

## Notes

- When every CIDR involved is a literal (no unresolved tokens), strata validates at schema time
  that fixed subnets don't overlap and that each fits inside the network's address space. A
  network/subnet whose CIDR uses a `${var:}`/`${secret:}`/`${feature:}` token skips that check
  until the value is resolved.
- A `prefix_length` subnet has no address in configuration, so it takes no part in the overlap
  check. With a literal address space strata checks that the size can fit a range, and that all
  subnets together, fixed and sized, don't need more addresses than the ranges hold. Without one,
  the same two checks use the block the network asks the IPAM for (`ipam.prefix_length`): no
  subnet larger than it, and no more addresses than it holds. Whether a free block is really left
  is decided at deploy, by your provisioner code or the IPAM.
- `${value:network...subnets.<name>.cidr}` on a `prefix_length` subnet fails with
  `value_reference_no_address`: take its address from your provisioner code instead.
- Subnet names follow strata's naming rule (lowercase letters, digits, `-` and `_`). For a name the
  cloud requires, such as Azure's `AzureFirewallSubnet`, map it in your provisioner code, for example
  from the subnet's `configuration`.
- When the solution is validated, a workspace resource's `subnet` and a workspace's own
  `spec.subnets[]` are checked against this document: the subnet or network must exist, a workspace
  subnet must not reuse a subnet name the document declares in any of its networks, and it must fit the address space.
