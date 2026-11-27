# Offline tests of the placement logic: `terraform init && terraform test` in the placement folder.

run "cidrhost_drops_host_bits" {
  # V9: the module normalises every CIDR with cidrhost(c, 0).
  command = plan
  variables {
    address_space = ["10.10.1.32/24"]
    requests      = { app = 26 }
  }
  assert {
    condition     = output.addresses.app == "10.10.1.0/26"
    error_message = "cidrhost() no longer drops host bits: ${jsonencode(output.addresses)}"
  }
}

run "aks_layout_largest_first" {
  command = plan
  variables {
    address_space = ["10.0.0.0/22"]
    requests      = { aks-nodes = 25, aks-kubeapi = 28, app = 24 }
  }
  assert {
    condition = output.addresses == {
      app         = "10.0.0.0/24"
      aks-nodes   = "10.0.1.0/25"
      aks-kubeapi = "10.0.1.128/28"
    }
    error_message = "Unexpected layout: ${jsonencode(output.addresses)}"
  }
}

run "fills_a_gap_and_spills_into_an_added_range" {
  # The /26 fits the gap at .64; no /25 is free in the first range, so it lands in the added one.
  command = plan
  variables {
    address_space = ["10.0.0.0/24", "10.0.8.0/24"]
    taken         = ["10.0.0.0/26", "10.0.0.128/26"]
    requests      = { small = 26, big = 25 }
  }
  assert {
    condition     = output.addresses == { big = "10.0.8.0/25", small = "10.0.0.64/26" }
    error_message = "Unexpected: ${jsonencode(output.addresses)}"
  }
}

run "ranges_are_walked_in_order_not_by_address" {
  command = plan
  variables {
    address_space = ["10.9.0.0/24", "10.0.0.0/24"]
    requests      = { app = 27 }
  }
  assert {
    condition     = output.addresses.app == "10.9.0.0/27"
    error_message = "Unexpected: ${jsonencode(output.addresses)}"
  }
}

run "unaligned_end_of_a_taken_block_is_rounded_up" {
  command = plan
  variables {
    address_space = ["10.0.0.0/24"]
    taken         = ["10.0.0.0/28"]
    requests      = { app = 26 }
  }
  assert {
    condition     = output.addresses.app == "10.0.0.64/26"
    error_message = "Unexpected: ${jsonencode(output.addresses)}"
  }
}

run "fixed_subnets_are_avoided" {
  command = plan
  variables {
    address_space = ["10.0.0.0/24"]
    fixed         = ["10.0.0.0/25"]
    requests      = { app = 25 }
  }
  assert {
    condition     = output.addresses.app == "10.0.0.128/25"
    error_message = "Unexpected: ${jsonencode(output.addresses)}"
  }
}

run "large_range_small_subnets_no_enumeration_limit" {
  # A /8 holds 2^21 /29 blocks; candidates are only range starts and taken ends.
  command = plan
  variables {
    address_space = ["10.0.0.0/8"]
    taken         = ["10.0.0.0/9"]
    requests      = { a = 29, b = 29, c = 16 }
  }
  assert {
    condition     = output.addresses == { c = "10.128.0.0/16", a = "10.129.0.0/29", b = "10.129.0.8/29" }
    error_message = "Unexpected: ${jsonencode(output.addresses)}"
  }
}

run "ten_new_subnets_in_one_apply" {
  command = plan
  variables {
    address_space = ["10.0.0.0/24"]
    requests      = { for i in range(10) : "s${i}" => 28 }
  }
  assert {
    condition     = length(distinct(values(output.addresses))) == 10 && output.addresses.s9 == "10.0.0.144/28"
    error_message = "Unexpected: ${jsonencode(output.addresses)}"
  }
}

run "eleven_new_subnets_fail_clearly" {
  command = plan
  variables {
    address_space = ["10.0.0.0/24"]
    requests      = { for i in range(11) : "s${i}" => 28 }
  }
  expect_failures = [output.addresses]
}

run "full_network_fails_clearly" {
  command = plan
  variables {
    address_space = ["10.0.0.0/24"]
    taken         = ["10.0.0.0/25", "10.0.0.128/26"]
    requests      = { app = 25 }
  }
  expect_failures = [output.addresses]
}

run "fixed_subnet_outside_the_ranges_fails" {
  command = plan
  variables {
    address_space = ["10.0.0.0/24"]
    fixed         = ["10.1.0.0/26"]
  }
  expect_failures = [output.addresses]
}

run "ipv6_is_refused" {
  command = plan
  variables {
    address_space = ["fd00::/48"]
  }
  expect_failures = [var.address_space]
}
