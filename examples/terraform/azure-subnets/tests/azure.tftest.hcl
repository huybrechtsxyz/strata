# The Azure wrapper against a mocked azurerm provider: `terraform init && terraform test`.
# What a mock cannot show (the real vnet read, Azure's overlap check) is listed in the design doc.

mock_provider "azurerm" {}

variables {
  vnet_name           = "vnet-main"
  resource_group_name = "rg-network"
  network = {
    address_space = ["10.0.0.0/22"]
    ipam          = { type = null, pool = null, prefix_length = null, configuration = {} }
    subnets = {
      firewall    = { cidr = "10.0.0.0/26", prefix_length = null, declared_by = "network", description = null, configuration = { azure_name = "AzureFirewallSubnet" }, custom = {} }
      aks-nodes   = { cidr = null, prefix_length = 25, declared_by = "network", description = null, configuration = {}, custom = {} }
      aks-kubeapi = { cidr = null, prefix_length = 28, declared_by = "network", description = null, configuration = {}, custom = {} }
      app1-pe     = { cidr = null, prefix_length = 27, declared_by = "workspace", description = null, configuration = {}, custom = {} }
    }
  }
}

run "first_deploy_of_the_network" {
  command = plan
  variables { declared_by = "network" }

  override_data {
    target = data.azurerm_virtual_network.this
    values = { subnets = [] }
  }

  assert {
    condition = { for n, s in azurerm_subnet.this : n => [s.name, s.address_prefixes[0]] } == {
      firewall    = ["AzureFirewallSubnet", "10.0.0.0/26"]
      aks-nodes   = ["aks-nodes", "10.0.0.128/25"]
      aks-kubeapi = ["aks-kubeapi", "10.0.0.64/28"]
    }
    error_message = "Unexpected: ${jsonencode({ for n, s in azurerm_subnet.this : n => s.address_prefixes })}"
  }
}

run "existing_subnets_keep_their_address_and_new_ones_avoid_them" {
  # A later deploy: the vnet already holds the network's subnets and another workload's subnet.
  command = plan
  variables { declared_by = "workspace" }

  override_data {
    target = data.azurerm_virtual_network.this
    values = { subnets = ["AzureFirewallSubnet", "aks-nodes", "aks-kubeapi", "app2-pe"] }
  }
  override_data {
    target = data.azurerm_subnet.existing["AzureFirewallSubnet"]
    values = { address_prefixes = ["10.0.0.0/26"] }
  }
  override_data {
    target = data.azurerm_subnet.existing["aks-nodes"]
    values = { address_prefixes = ["10.0.0.128/25"] }
  }
  override_data {
    target = data.azurerm_subnet.existing["aks-kubeapi"]
    values = { address_prefixes = ["10.0.0.64/28"] }
  }
  override_data {
    target = data.azurerm_subnet.existing["app2-pe"]
    values = { address_prefixes = ["10.0.0.96/27"] }
  }

  assert {
    condition     = keys(azurerm_subnet.this) == ["app1-pe"]
    error_message = "A workload must create only its own subnets."
  }
  assert {
    condition     = azurerm_subnet.this["app1-pe"].address_prefixes[0] == "10.0.1.0/27"
    error_message = "Unexpected: ${azurerm_subnet.this["app1-pe"].address_prefixes[0]}"
  }
}

run "a_created_sized_subnet_is_read_back_not_recomputed" {
  # app1-pe exists at an address placement would not choose today (10.0.0.64/27 is free): it stays there.
  command = plan
  variables { declared_by = "workspace" }

  override_data {
    target = data.azurerm_virtual_network.this
    values = { subnets = ["app1-pe"] }
  }
  override_data {
    target = data.azurerm_subnet.existing["app1-pe"]
    values = { address_prefixes = ["10.0.3.224/27"] }
  }

  assert {
    condition     = azurerm_subnet.this["app1-pe"].address_prefixes[0] == "10.0.3.224/27"
    error_message = "An existing subnet moved: ${azurerm_subnet.this["app1-pe"].address_prefixes[0]}"
  }
}

run "an_ipam_network_is_refused" {
  command = plan
  variables {
    declared_by = "network"
    network = {
      address_space = []
      ipam          = { type = "azure-avnm", pool = "pool-id", prefix_length = 22, configuration = {} }
      subnets       = {}
    }
  }
  expect_failures = [var.network]
}
