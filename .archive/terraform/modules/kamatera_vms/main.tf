terraform {
  required_providers {
    kamatera = {
      source = "Kamatera/kamatera"
    }
    tls = {
      source = "hashicorp/tls"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
}

# Provider configuration for Kamatera
provider "kamatera" {
  api_client_id = var.kamatera_api_key
  api_secret    = var.kamatera_api_secret
}

# Create a random suffix resource
resource "random_string" "suffix" {
  length  = 4
  upper   = false
  special = false
}

# Generate a unique SSH key for each VM
resource "tls_private_key" "vm_keys" {
  for_each  = var.kamatera_vms
  algorithm = "ED25519"
}

# define the data center we will create the server and all related resources in
# see the section below "Listing available data centers" for more details
data "kamatera_datacenter" "dc1" {
  country = var.kamatera_country
  name    = var.kamatera_region
}

# define the server image we will create the server with
# see the section below "Listing available public images" for more details
# also see "Using a private image" if you want to use a private image you created yourself
data "kamatera_image" "images" {
  for_each = {
    for image in local.unique_images :
    "${image.os_name}-${image.os_code}" => image
  }

  datacenter_id = data.kamatera_datacenter.dc1.id
  os            = each.value.os_name
  code          = each.value.os_code
}

locals {
  # Extract unique OS images from the VM definitions
  unique_images = distinct([
    for s in var.kamatera_vms : {
      os_name = s.os_name
      os_code = s.os_code
    }
  ])

  # List of all VM public keys
  vm_public_keys = [for k in tls_private_key.vm_keys : k.public_key_openssh]

  # List of all VM private keys
  vm_private_keys    = { for k, v in tls_private_key.vm_keys : k => v.private_key_pem }
  all_vm_public_keys = join("\n", local.vm_public_keys)
}

# Set up private network
# Name example: vlan-shared-1234
resource "kamatera_network" "private-lan" {
  datacenter_id = data.kamatera_datacenter.dc1.id
  name          = "vlan-${var.workspace}-${random_string.suffix.result}"

  subnet {
    ip  = "10.0.0.0"
    bit = 23
  }
}

# Provision servers: for each virtual machine on kamatera
# Name example: srv-shared-manager-1-1234
resource "kamatera_server" "server" {
  for_each = var.kamatera_vms

  name          = "srv-${var.workspace}-${each.key}-${random_string.suffix.result}"
  image_id      = data.kamatera_image.images["${each.value.os_name}-${each.value.os_code}"].id
  datacenter_id = data.kamatera_datacenter.dc1.id
  cpu_cores     = each.value.cpu_cores
  cpu_type      = each.value.cpu_type
  ram_mb        = each.value.ram_mb
  disk_sizes_gb = each.value.disks_gb
  billing_cycle = each.value.billing
  power_on      = true
  password      = var.kamatera_root_password
  ssh_pubkey    = var.kamatera_public_key

  network {
    name = "wan"
  }

  network {
    name = kamatera_network.private-lan.full_name
  }
}

resource "null_resource" "setup_vm" {
  for_each = kamatera_server.server

  # Re-run provisioning when script changes
  triggers = {
    script_hash = sha1(join("|", [
      filesha256("${path.cwd}/scripts/provision_vm.sh")
    ]))
    public_keys = sha1(join("|", local.vm_public_keys))
  }

  connection {
    host        = each.value.public_ip
    type        = "ssh"
    user        = "ubuntu"
    private_key = var.kamatera_private_key
  }

  # Upload initialization script
  provisioner "file" {
    source      = "${path.cwd}/scripts/provision_vm.sh"
    destination = "/tmp/provision_vm.sh"
  }

  # Upload per-VM SSH key material (private key for that VM + all public keys) atomically
  provisioner "remote-exec" {
    inline = [
      "set -euo pipefail",
      "mkdir -p /home/ubuntu/.ssh",
      # Write this VM's private key
      "cat > /home/ubuntu/.ssh/id_ed25519 <<'EOF'\n${tls_private_key.vm_keys[each.key].private_key_pem}\nEOF",
      "chmod 600 /home/ubuntu/.ssh/id_ed25519",
      # Append all public keys (idempotent: remove old block first)
      "grep -v 'BEGIN-VM-PUB-KEYS' /home/ubuntu/.ssh/authorized_keys 2>/dev/null | grep -v 'END-VM-PUB-KEYS' > /home/ubuntu/.ssh/authorized_keys.new || true",
      "mv /home/ubuntu/.ssh/authorized_keys.new /home/ubuntu/.ssh/authorized_keys || true",
      "echo '## BEGIN-VM-PUB-KEYS' >> /home/ubuntu/.ssh/authorized_keys",
      "cat <<'PUBKEYS' >> /home/ubuntu/.ssh/authorized_keys\n${join("\n", local.vm_public_keys)}\nPUBKEYS",
      "echo '## END-VM-PUB-KEYS' >> /home/ubuntu/.ssh/authorized_keys",
      "chmod 600 /home/ubuntu/.ssh/authorized_keys",
      "chown -R ubuntu:ubuntu /home/ubuntu/.ssh",
      # Run provisioning script
      "chmod +x /tmp/provision_vm.sh",
      "sudo /tmp/provision_vm.sh"
    ]
  }

  depends_on = [kamatera_server.server]
}
