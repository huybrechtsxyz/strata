# Stand-in for a real Terraform module registry the `infra` remote points
# at — not a real, executable Terraform root module, just enough for
# `strata build run` to have something real to copy and demonstrate the
# provisioner pipeline (ADR-0022) end to end without any network access.
resource "null_resource" "web_storage" {}
resource "null_resource" "web_vm" {}

# The `api` asset slot, read through the local strata generates (strata_locals.tf):
# the same line works for every deployment, whichever Asset document is bound.
output "api_contract" {
  value = file(local.strata_assets.api.files["openapi.json"])
}
