# Reference module: create the subnets one deployment declares in an existing Azure vnet, placing
# sized subnets (`prefix_length`) without an IPAM. The vnet itself is the register of what is taken.
# Design: docs/design/subnet-addressing.md (D6). Usage: docs/how-to/declare-subnets.md.
#
#   module "subnets" {
#     source              = "./azure-subnets"
#     network             = var.strata.networks["main-network"].networks["vnet-main"]
#     declared_by         = "network"        # the network deployment; a workload passes "workspace"
#     vnet_name           = "vnet-main-prd"
#     resource_group_name = "rg-network-prd"
#   }
#
# The network deployment creates the vnet in an earlier execution step: this module reads it.

terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.0" # tested with 4.81 and 5.15
    }
  }
}

variable "network" {
  description = "One network from the strata payload: strata.networks[<document>].networks[<name>]."
  type        = any

  validation {
    condition     = length(var.network.address_space) > 0
    error_message = "This network has no address_space (an IPAM network): allocate through that IPAM instead."
  }
}

variable "declared_by" {
  description = "Which subnets this deployment creates: \"network\" (the network deployment) or \"workspace\" (a workload)."
  type        = string

  validation {
    condition     = contains(["network", "workspace"], var.declared_by)
    error_message = "declared_by is \"network\" or \"workspace\"."
  }
}

variable "vnet_name" {
  description = "Azure name of the existing virtual network."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the virtual network."
  type        = string
}

data "azurerm_virtual_network" "this" {
  name                = var.vnet_name
  resource_group_name = var.resource_group_name
}

data "azurerm_subnet" "existing" {
  for_each             = toset(data.azurerm_virtual_network.this.subnets)
  name                 = each.key
  virtual_network_name = var.vnet_name
  resource_group_name  = var.resource_group_name
}

locals {
  subnets = var.network.subnets

  # Strata names are lowercase; a subnet's configuration may map one to the name Azure requires.
  azure_name = { for n, s in local.subnets : n => try(s.configuration.azure_name, n) }

  # What exists in the vnet now, by Azure name: every deployment's subnets, created by anyone.
  existing = { for n, s in data.azurerm_subnet.existing : n => s.address_prefixes[0] }

  mine  = { for n, s in local.subnets : n => s if s.declared_by == var.declared_by }
  fixed = [for s in local.subnets : s.cidr if s.cidr != null]

  # A sized subnet that already exists keeps its address: read back by name, never recomputed.
  new = { for n, s in local.mine : n => s.prefix_length if s.cidr == null && !contains(keys(local.existing), local.azure_name[n]) }

  address = {
    for n, s in local.mine : n => coalesce(s.cidr, lookup(local.existing, local.azure_name[n], null), lookup(module.placement.addresses, n, null))
  }
}

module "placement" {
  source        = "./placement"
  address_space = var.network.address_space
  taken         = values(local.existing)
  fixed         = local.fixed
  requests      = local.new
}

resource "azurerm_subnet" "this" {
  for_each             = local.mine
  name                 = local.azure_name[each.key]
  virtual_network_name = var.vnet_name
  resource_group_name  = var.resource_group_name
  address_prefixes     = [local.address[each.key]]

  # Add what your subnets need from each.value.configuration (delegations, service endpoints, ...).

  lifecycle {
    ignore_changes = [address_prefixes] # an address, once created, never moves
  }
}

output "subnets" {
  description = "name => {id, name, address_prefix}. Read addresses from here, never from a computed local."
  value = {
    for n, s in azurerm_subnet.this : n => { id = s.id, name = s.name, address_prefix = s.address_prefixes[0] }
  }
}
