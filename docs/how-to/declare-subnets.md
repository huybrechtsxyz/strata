# How To: Declare subnets by size and create them where they belong

A shared network usually has one owner — a network repository that declares the virtual network, its
address ranges and the core subnets (firewall, gateway, AKS) — while the workloads deployed into it
keep needing subnets of their own. Writing every subnet's address by hand means re-doing subnet math
for each new workload and editing the network owner's document every time.

Since 2.11.0 a subnet can give a **size** instead of an address, and a workload can add its own
sized subnets without touching the network document. Strata **declares**; it never allocates:

| | Who | What |
|---|---|---|
| Where | strata configuration | the address ranges (`address_space`), and which IPAM is used (`ipam`) |
| How | strata configuration | the networks, their subnets, each subnet's size or fixed address, and who creates it |
| The address of a sized subnet | your provisioner code, or an IPAM | chosen at deploy, from what really exists in the network |

The full design and its reasoning: [subnet-addressing.md](../design/subnet-addressing.md).

## 1. Declare the network and its core subnets

In the network owner's [network](../config/network.md) document, give each subnet either a fixed
`cidr` or a `prefix_length`:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: network
meta:
  name: main-network
spec:
  networks:
    - name: vnet-main
      address_space: ["${var:vnet_space}"]   # per environment
      subnets:
        - name: firewall
          cidr: "${var:fw_subnet}"           # fixed: agreed with the network team
          configuration:
            azure_name: AzureFirewallSubnet  # see step 4
        - name: aks-nodes
          prefix_length: 25                  # a /25, address chosen at deploy
        - name: aks-kubeapi
          prefix_length: 28
```

If an IPAM hands out the addresses, say which one with `ipam`. Strata does not call it; it passes the
settings to your provisioner code:

```yaml
    - name: vnet-spoke
      ipam:
        type: azure-avnm                     # a free name your code recognises
        pool: "${var:ipam_pool_id}"
        prefix_length: 22
```

A network needs `address_space`, `ipam`, or both. With both, `address_space` is the range the IPAM
manages, and strata uses it for its checks.

## 2. Let a workload add its own subnets

A workload declares its subnets in its own [workspace](../config/workspace.md), against a network it
lists in `networks`. The network document stays untouched:

```yaml
spec:
  networks: [main-network]
  subnets:
    - network: main-network
      virtual_network: vnet-main  # which network in the document; optional if it has only one
      name: app1-pe               # prefix with the workload name: subnet names are unique per vnet
      prefix_length: 27
  resources:
    - name: app1-storage
      resource: storage-account
      subnet: { network: main-network, subnet: app1-pe }
```

A workload subnet only takes a `prefix_length`. Fixed addresses belong to the network owner.

## 3. Read what validation checks

`strata validate` catches what can be known before deploy:

| Problem | Message or code |
|---|---|
| A subnet with both `cidr` and `prefix_length`, or neither | "needs exactly one of 'cidr' or 'prefix_length'" |
| A network with neither `address_space` nor `ipam` | "needs 'address_space', 'ipam', or both" |
| Fixed subnets that overlap, or fall outside the address space | as before |
| A size larger than every literal range (or the `ipam.prefix_length` block), or for the wrong IP version | "does not fit within any address space" / `subnet_does_not_fit` |
| All subnets together need more addresses than the literal ranges (or the IPAM block) hold | "need N addresses, but … holds only M" / `subnet_capacity_exceeded` |
| A workspace subnet for a network not in `networks` | "references undefined network" |
| A `virtual_network` the document doesn't have, or none when it has several | `unregistered_virtual_network` |
| A workspace subnet reusing a subnet name the network document declares, in any of its networks | `subnet_name_taken` |
| `${value:…subnets.<name>.cidr}` on a sized subnet | `value_reference_no_address` |

The range checks only run when the ranges are literal, or, for a network with only an IPAM, when
`ipam.prefix_length` is given. With `address_space: ["${var:vnet_space}"]`, as above, strata cannot
count addresses, so put the same checks in your Terraform (step 5).

Capacity is a "certainly won't fit" check. Other workloads' subnets aren't in your solution, so a
network that passes can still be full at deploy.

## 4. Create each subnet in the Terraform that owns it

Every subnet in `strata.networks` says who declared it, and carries a fixed `cidr` or a
`prefix_length` (the other is `null`). `ipam` is always present; its `type` is `null` when the
network names no IPAM. The network deployment creates the subnets with `declared_by = "network"`,
and a workload creates its own, `declared_by = "workspace"`. Each still sees the other subnets, for
references and NSG or firewall rules:

```text
locals {
  vnet = var.strata.networks["main-network"].networks["vnet-main"]

  # The network deployment uses "network"; a workload uses "workspace".
  mine = { for name, s in local.vnet.subnets : name => s if s.declared_by == "network" }

  fixed = { for name, s in local.mine : name => s if s.cidr != null }
  sized = { for name, s in local.mine : name => s if s.prefix_length != null }

  # Strata names are lowercase; the subnet's configuration maps one to the name Azure requires.
  azure_name = { for name, s in local.mine : name => try(s.configuration.azure_name, name) }

  uses_ipam = local.vnet.ipam.type != null
}

resource "azurerm_subnet" "fixed" {
  for_each             = local.fixed
  name                 = local.azure_name[each.key]
  virtual_network_name = var.vnet_name
  resource_group_name  = var.vnet_resource_group
  address_prefixes     = [each.value.cidr]
}
```

Two rules for the network deployment:

- **Create subnets as separate `azurerm_subnet` resources**, never as inline `subnet` blocks on
  `azurerm_virtual_network`. With inline blocks the vnet deploy deletes every subnet it doesn't
  declare, including the ones workloads created.
- **Put the vnet and its subnets in two execution steps**, the subnets' step depending on the vnet's,
  when your subnet code reads the existing vnet (step 5): on the first deploy that vnet doesn't exist
  yet.

A workload's identity needs read access on the vnet and permission to create subnets in it, such as
Network Contributor on the vnet or a custom role.

## 5. Choose the address of a sized subnet

**With an IPAM**, read `local.vnet.ipam` (`type`, `pool`, `prefix_length`, `configuration`) and ask
the IPAM for a block of `prefix_length`, using that IPAM's own Terraform provider or resources. The IPAM
records the allocation, so the address stays the same on every deploy, and it sees every workload's
subnets.

**Without an IPAM**, the existing vnet is the register of what is taken. The reference module
[examples/terraform/azure-subnets](../../examples/terraform/azure-subnets/main.tf) does this for Azure;
copy it into your Terraform and call it from both the network deployment and each workload:

```text
module "subnets" {
  source              = "./azure-subnets"
  network             = var.strata.networks["main-network"].networks["vnet-main"]
  declared_by         = "workspace"          # "network" in the network deployment
  vnet_name           = "vnet-main-prd"
  resource_group_name = "rg-network-prd"
}
```

It:

1. reads the vnet's current subnets;
2. keeps the address of every subnet that already exists, by name, so later deploys don't move it;
3. gives each new subnet the first free block of its size, largest first, walking the ranges in order
   and skipping every existing and fixed subnet;
4. sets `lifecycle { ignore_changes = [address_prefixes] }`, so a live subnet never moves;
5. fails with a clear message when no block is free ("Ask the network owner to add a range to
   address_space"), when a fixed `cidr` fits no range, or when a network has no `address_space`.

Limits: IPv4 only, and at most 10 new subnets per network in one apply (apply in batches, or extend
the chain in `placement/main.tf`). Requires Terraform 1.6 or later and azurerm 4 or later. Add your
subnets' own settings (delegations, service endpoints) from `configuration` in the module's
`azurerm_subnet`.

Downstream code (NSGs, firewall rules) reads the real addresses from the module's `subnets` output,
never from a computed local.

Two workloads deploying into the same vnet at the same moment can pick the same free block. Azure
rejects the second subnet as overlapping, and running that deploy again places it elsewhere.

## 6. Grow the address space

When a network is full, the network owner appends a range to `address_space` (or grows the IPAM pool).
Existing subnets keep their addresses, and new ones can land in the new range. A workload never grows
the address space itself.

## 7. Firewall rules for a sized subnet

`${value:network.main-network.spec.networks.vnet-main.subnets.app1-pe.cidr}` only works for a subnet
with a fixed `cidr`. A sized subnet's address exists only after deploy, so a rule for it must take the
address from the provisioner code: the subnet resource's `address_prefixes`.
