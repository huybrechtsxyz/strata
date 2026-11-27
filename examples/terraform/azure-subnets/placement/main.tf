# Pure placement: which address each new sized subnet gets. No provider, no Azure: the parent module
# reads what exists and passes it in, so this part is tested offline (tests/placement.tftest.hcl).
#
# IPv4 only. New subnets are placed largest first (then by name), each one seeing the blocks the
# previous ones took: HCL has no sequential loop, so this is a chain of `step` modules, MAX_NEW deep.

variable "address_space" {
  description = "The network's ranges, in order (strata's `address_space`, resolved)."
  type        = list(string)

  validation {
    condition     = alltrue([for c in var.address_space : can(regex("^\\d+\\.\\d+\\.\\d+\\.\\d+/\\d+$", c))])
    error_message = "The placement module handles IPv4 ranges only."
  }
}

variable "taken" {
  description = "Blocks already used in the vnet: existing subnets, plus every fixed `cidr` declared."
  type        = list(string)
  default     = []
}

variable "fixed" {
  description = "Fixed `cidr`s declared for the network; each must fit one of the ranges."
  type        = list(string)
  default     = []
}

variable "requests" {
  description = "New subnets to place: name => prefix_length."
  type        = map(number)
  default     = {}
}

locals {
  max_new = 10

  as_block = { for c in distinct(concat(var.address_space, var.taken, var.fixed)) : c => {
    start = sum([for i, o in split(".", cidrhost(c, 0)) : tonumber(o) * pow(256, 3 - i)])
    size  = pow(2, 32 - tonumber(split("/", c)[1]))
  } }

  ranges = [for c in var.address_space : local.as_block[c]]
  taken  = [for c in distinct(concat(var.taken, var.fixed)) : local.as_block[c]]

  unplaceable_fixed = [
    for c in var.fixed : c
    if !anytrue([for r in local.ranges : local.as_block[c].start >= r.start && local.as_block[c].start + local.as_block[c].size <= r.start + r.size])
  ]

  # Largest first (smallest prefix length), then by name; names never contain a space.
  order = [for k in sort([for n, p in var.requests : format("%03d %s", p, n)]) : split(" ", k)[1]]
  asked = [for i in range(local.max_new) : try(var.requests[local.order[i]], null)]
}

module "step_1" {
  source        = "./step"
  ranges        = local.ranges
  taken         = local.taken
  prefix_length = local.asked[0]
}

module "step_2" {
  source        = "./step"
  ranges        = local.ranges
  taken         = module.step_1.taken
  prefix_length = local.asked[1]
}

module "step_3" {
  source        = "./step"
  ranges        = local.ranges
  taken         = module.step_2.taken
  prefix_length = local.asked[2]
}

module "step_4" {
  source        = "./step"
  ranges        = local.ranges
  taken         = module.step_3.taken
  prefix_length = local.asked[3]
}

module "step_5" {
  source        = "./step"
  ranges        = local.ranges
  taken         = module.step_4.taken
  prefix_length = local.asked[4]
}

module "step_6" {
  source        = "./step"
  ranges        = local.ranges
  taken         = module.step_5.taken
  prefix_length = local.asked[5]
}

module "step_7" {
  source        = "./step"
  ranges        = local.ranges
  taken         = module.step_6.taken
  prefix_length = local.asked[6]
}

module "step_8" {
  source        = "./step"
  ranges        = local.ranges
  taken         = module.step_7.taken
  prefix_length = local.asked[7]
}

module "step_9" {
  source        = "./step"
  ranges        = local.ranges
  taken         = module.step_8.taken
  prefix_length = local.asked[8]
}

module "step_10" {
  source        = "./step"
  ranges        = local.ranges
  taken         = module.step_9.taken
  prefix_length = local.asked[9]
}

locals {
  starts = [
    module.step_1.start, module.step_2.start, module.step_3.start, module.step_4.start, module.step_5.start,
    module.step_6.start, module.step_7.start, module.step_8.start, module.step_9.start, module.step_10.start,
  ]

  unplaced = [for i, n in local.order : n if i < local.max_new && local.starts[i] == null]

  addresses = {
    for i, n in local.order : n => format(
      "%d.%d.%d.%d/%d",
      floor(local.starts[i] / 16777216) % 256, floor(local.starts[i] / 65536) % 256,
      floor(local.starts[i] / 256) % 256, local.starts[i] % 256, var.requests[n],
    ) if i < local.max_new && local.starts[i] != null
  }
}

output "addresses" {
  description = "name => CIDR for every requested subnet."
  value       = local.addresses

  precondition {
    condition     = length(local.unplaceable_fixed) == 0
    error_message = "Fixed subnet(s) ${join(", ", local.unplaceable_fixed)} fit none of the ranges ${join(", ", var.address_space)}."
  }

  precondition {
    condition     = length(var.requests) <= local.max_new
    error_message = "${length(var.requests)} new subnets in one apply; this module places at most ${local.max_new}. Apply in batches, or raise max_new and add steps."
  }

  precondition {
    condition     = length(local.unplaced) == 0
    error_message = "No free block for subnet(s) ${join(", ", [for n in local.unplaced : "${n} (/${var.requests[n]})"])} in ${join(", ", var.address_space)}. Ask the network owner to add a range to address_space."
  }
}
