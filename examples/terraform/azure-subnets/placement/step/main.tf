# One placement: the first free, aligned block of `prefix_length` in `ranges`, avoiding `taken`.
#
# Addresses are IPv4 integers ({start, size}), converted by the parent module. The lowest free aligned
# block always starts either at a range start or right after a taken block (rounded up to the block
# size), so those are the only candidates: one per range and one per taken block, whatever the range
# size. Ranges are walked in order; within a range the lowest address wins.

variable "ranges" {
  type = list(object({ start = number, size = number }))
}

variable "taken" {
  type = list(object({ start = number, size = number }))
}

variable "prefix_length" {
  type    = number
  default = null # null: nothing to place in this step
}

locals {
  size = var.prefix_length == null ? 0 : pow(2, 32 - var.prefix_length)

  candidates = local.size == 0 ? [] : distinct([
    for x in concat([for r in var.ranges : r.start], [for t in var.taken : t.start + t.size]) :
    ceil(x / local.size) * local.size
  ])

  # Per range, the lowest candidate inside it that overlaps no taken block.
  first_per_range = [
    for r in var.ranges : try(min([
      for c in local.candidates : c
      if c >= r.start && c + local.size <= r.start + r.size
      && !anytrue([for t in var.taken : c < t.start + t.size && t.start < c + local.size])
    ]...), null)
  ]

  start = try([for s in local.first_per_range : s if s != null][0], null)
}

output "start" {
  description = "Start of the placed block, or null when nothing was asked or nothing is free."
  value       = local.start
}

output "taken" {
  description = "`taken` plus the block placed here, for the next step."
  value       = local.start == null ? var.taken : concat(var.taken, [{ start = local.start, size = local.size }])
}
