# Stand-in for a real Terraform module registry the `infra` remote points
# at — not a real, executable Terraform root module, just enough for
# `strata build run` to have something real to copy and demonstrate the
# provisioner pipeline (ADR-0022) end to end without any network access.
resource "null_resource" "web_storage" {}
resource "null_resource" "web_vm" {}
