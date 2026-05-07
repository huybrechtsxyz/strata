#!/bin/bash
# ==============================================================================
# Script Name   : initialize_vm_remote.sh
# Description   : Initialize the remote virtual machine.
# Usage         : ./initialize_vm_remote.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-11
# Last Modified : 2025-08-11
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Install yq ---------------------------------------------------------------
install_yq() {
  if ! command -v yq >/dev/null; then
    echo "[*] yq not found. Installing..."
    local yq_version=$(curl -s https://api.github.com/repos/mikefarah/yq/releases/latest | grep tag_name | cut -d '"' -f 4)
    curl -L "https://github.com/mikefarah/yq/releases/download/${yq_version}/yq_linux_amd64" -o /usr/local/bin/yq
    chmod +x /usr/local/bin/yq
    echo "[+] yq installed successfully."
  else
    echo "[*] yq is already installed: $(yq --version)"
  fi
}

# --- Load common start --------------------------------------------------------
install_yq
source "$SCRIPT_DIR/common_start.sh"
validate_script "$SCRIPT_DIR/common_start.sh"

# --- Install the private SSH key ----------------------------------------------
install_private_key() {
  if [[ -f /root/.ssh/id_rsa_temp ]]; then
    log INFO "[*] Installing uploaded private key..."
    mkdir -p ~/.ssh
    mv /root/.ssh/id_rsa_temp ~/.ssh/id_rsa
    chmod 600 ~/.ssh/id_rsa
    echo -e "Host *\n  StrictHostKeyChecking no\n" > ~/.ssh/config
    log INFO "[+] Installing uploaded private key...DONE"
  else
    log WARN "[!] No private key found at /root/.ssh/id_rsa_temp — skipping."
  fi
}

# --- Install Docker -----------------------------------------------------------
install_docker() {
  if ! command -v docker &>/dev/null; then
    log INFO "[*] Installing Docker..."
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg lsb-release
    curl -fsSL https://get.docker.com | bash
    log INFO "[+] Docker installed successfully."
  else
    log INFO "[*] Docker is already installed: $(docker --version)"
  fi
}

# --- Install GlusterFS --------------------------------------------------------
install_gluster() {
  if ! command -v glusterfs --version &>/dev/null; then
    log INFO "[*] Installing GlusterFS..."
    apt-get update -y
    apt-get install -y glusterfs-server
    systemctl enable --now glusterd
    log INFO "[+] GlusterFS installed successfully."
  else
    log INFO "[*] GlusterFS is already installed."
  fi
}

# Function to prepare and mount disk volumes
# This function reads the workspace metadata to find disk information
# and mounts the disks according to the specified mount points.
# It also checks the disk sizes against expected values and formats them if necessary.
# The function assumes the workspace metadata is stored in a JSON file at $PATH_TEMP/$WORKSPACE.ws.json
# and that the disks are named in a specific pattern (e.g., /dev/sdb, /dev/sdc, etc.).
# The OS disk is identified by the root partition mounted at '/'.
# The function also ensures that the disks are formatted as ext4 and labeled according to the metadata.
# It creates mount points based on a template from the workspace metadata and ensures
# that the mount points are added to /etc/fstab for persistence across reboots.
initialize_disks() {
  log INFO "[*] Preparing and mounting disk volumes..."

  # Identify the OS disk by partition mounted at root '/'
  log INFO "[*] ... Identify the OS disk by root mountpoint"
  local os_part=$(findmnt -n -o SOURCE /)
  local os_disk=$(lsblk -no PKNAME "$os_part")
  local os_disk_base="$os_disk"
  log INFO "[*] ... OS disk identified: /dev/$os_disk_base (root partition: $os_part)"

  # Get non-OS disks sorted by size
  log INFO "[*] ... Getting non-os disks"
  mapfile -t disks < <(lsblk -dn -o NAME,SIZE -b | \
    grep -v "^$os_disk_base$" | \
    grep -E '^sd[b-z]' | \
    sort -k2,2n -k1,1)

  # Insert OS disk as the first element
  os_size=$(lsblk -bn -o SIZE -d "/dev/$os_disk_base")
  disks=("$os_disk_base $os_size" "${disks[@]}")

  # Create disk name array
  declare -a disk_names
  for line in "${disks[@]}"; do
    disk_names+=("$(echo "$line" | awk '{print $1}')")
  done

  # Getting workspace disks
  local vm_tmpl=$(get_ws_resx_template "$RESX_DATA")
  local vm_file=$(get_ws_template_filename "$WS_DATA" "$vm_tmpl")
  local disk_count=$(get_ws_vm_disks "$vm_tmpl")
  log INFO "[*] ... Found $disk_count disks for $HOSTNAME (including OS disk)"
  if (( ${#disk_names[@]} < disk_count )); then
    log ERROR "[!] Only found ${#disk_names[@]} disks but expected $disk_count"
    return 1
  fi

  # Get mouting template for server
  local mount_template=$(get_ws_resx_mountpoint "$RESX_DATA")
  
  # Loop all disks found
  log INFO "[*] Looping over all disks"
  for i in $(seq 0 $((disk_count - 1))); do
    log INFO "[*] Mounting disk $i for $HOSTNAME"

    local disk="/dev/${disk_names[$i]}"
    local label=$(get_ws_vm_disk_label "$vm_tmpl")
    local part=""
    local fs_type=""
    local current_label=""
    local mnt="${mount_template//\$\{disk\}/$((i + 1))}"
    label=$(resolve_disk_label "$label" "$(( i + 1 ))" "$RESX_DATA")

    if [[ $i -eq 0 ]]; then
      # OS disk — use the actual root partition, not just first partition
      part="$os_part"
      fs_type=$(blkid -s TYPE -o value "$part" 2>/dev/null || echo "")
      current_label=$(blkid -s LABEL -o value "$part" 2>/dev/null || echo "")
      log INFO "[*] ... Checking OS disk label on $part (expected label=$label)"
      if [[ "$fs_type" != "ext4" ]]; then
        log WARN "[!] OS disk has unexpected FS type ($fs_type), skipping label check"
        continue
      elif [[ "$current_label" != "$label" ]]; then
        log INFO "[*] ... Relabeling OS disk from $current_label to $label"
        e2label "$part" "$label"
      else
        log INFO "[*] ... OS disk label is already correct: $label"
      fi
      # Make sure the mountpoint exist
      mkdir -p "$mnt"
      continue
    else
      # Data disks — expect partition 1 on the disk (e.g., /dev/sdb1)
      part=$(lsblk -nr -o NAME "$disk" | awk 'NR==2 {print "/dev/" $1}')
      fs_type=$(blkid -s TYPE -o value "$part" 2>/dev/null || echo "")
      current_label=$(blkid -s LABEL -o value "$part" 2>/dev/null || echo "")
    fi

    log INFO "[*] ... Mounting data disk $i for $HOSTNAME"
    log INFO "[*] ... Preparing disk $disk (label=$label)"

    # Get expected disk size from workspace metadata (in GB)
    local expected_size_gb=$(get_ws_vm_disk_size "$vm_tmpl")

    # Get actual disk size in bytes and convert to GB (rounding down)
    local actual_size_bytes=$(lsblk -bn -o SIZE -d "$disk")
    local actual_size_gb=$(( actual_size_bytes / 1024 / 1024 / 1024 ))

    log INFO "[*] ... Validating size for $disk: expected ${expected_size_gb}GB, found ${actual_size_gb}GB"

    if disk_size_matches "$actual_size_gb" "$expected_size_gb"; then
      log INFO "[*] ... Disk size for $disk matches expected ${expected_size_gb}GB, found ${actual_size_gb}GB"
    else
      log ERROR "[!] Disk size mismatch for $disk — expected ${expected_size_gb}GB, got ${actual_size_gb}GB"
      continue  # Skip this disk to avoid accidental mount/format
    fi

    # Check if partition exists (lsblk part)
    if ! lsblk "$part" &>/dev/null; then
      log INFO "[*] ... Partitioning $disk"
      parted -s "$disk" mklabel gpt
      parted -s -a optimal "$disk" mkpart primary ext4 0% 100%
      sync
      sleep 5
      # refresh fs_type and current_label after new partition creation
      part="/dev/$(lsblk -nro NAME "$disk" | sed -n '2p')"
      fs_type=$(blkid -s TYPE -o value "$part" 2>/dev/null || echo "")
      current_label=$(blkid -s LABEL -o value "$part" 2>/dev/null || echo "")
    else
      log INFO "[*] ... Skipping partitioning: $disk already partitioned"
    fi

    # Formatting and labeling
    if [[ -z "$fs_type" ]]; then
      log INFO "[*] ... Formatting $part as ext4 with label $label"
      mkfs.ext4 -L "$label" "$part"
    elif [[ "$fs_type" != "ext4" ]]; then
      log WARN "[!] $part has unexpected FS type ($fs_type), skipping"
      continue
    elif [[ "$current_label" != "$label" ]]; then
      log INFO "[*] ... Relabeling $part from $current_label to $label"
      e2label "$part" "$label"
    else
      log INFO "[*] ... $part already formatted and labeled $label"
    fi

    # Mount and create the diskmountpoint
    mkdir -p "$mnt"
    if ! mountpoint -q "$mnt"; then
      log INFO "[*] ... Mounting $label to $mnt"
      mount "/dev/disk/by-label/$label" "$mnt"
    else
      log INFO "[+] ... Already mounted: $mnt"
    fi

    # Ensure persistence in fstab (idempotent)
    fstab_line="LABEL=$label $mnt ext4 defaults 0 2"
    if grep -qE "^\s*LABEL=$label\s" /etc/fstab; then
      if ! grep -Fxq "$fstab_line" /etc/fstab; then
        log INFO "[*] ... Updating existing fstab entry for $label"
        sed -i.bak "/^\s*LABEL=$label\s/c\\$fstab_line" /etc/fstab
      else
        log INFO "[*] ... fstab entry for $label is already correct"
      fi
    else
      log INFO "[*] ... Adding fstab entry for $label"
      echo "$fstab_line" >> /etc/fstab
    fi

  done

  log INFO "[+] Preparing and mounting disk volumes...DONE"
}

# This function initializes a new Swarm cluster if the current node is a manager,
# or joins an existing Swarm cluster if the current node is a worker.
# It retrieves the join tokens from the manager node and stores them in /tmp/manager_token.txt and /tmp/worker_token.txt.
# The function checks if the node is already part of a Swarm and skips initialization if it is.
# If the node is a manager, it initializes the Swarm and creates join tokens.
# If the node is a worker, it waits for the manager node to provide the join tokens before joining the Swarm.
# Function that configures swarm servers and stores its tokens in /tmp and NOT /tmp/app => Reason: /tmp/app gets cleaned !
initialize_swarm() {
  log INFO "[*] Configuring Docker Swarm on $HOSTNAME..."
  # SSH Options
  SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

  # Check if this node is already part of a Swarm
  local SWARM_STATE=$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || echo inactive)
  if [ "$SWARM_STATE" = "active" ]; then
    log INFO "[*] ... Setting configuration for ACTIVE Docker Swarm cluster"
  else
    log INFO "[*] ... Setting configuration for INACTIVE Docker Swarm cluster"
  fi

  # Determine if this node is the primary manager
  IS_PRIMARY=false
  if [[ "$HOSTNAME" == *"$RESOURCE_PRIMARY_NAME"* ]]; then
    IS_PRIMARY=true
    log INFO "[*] ... Setting configuration for PRIMARY MANGER"
  fi

  # Determine if this node is a manager
  IS_MANAGER=false
  if [[ "$HOSTNAME" == *"$RESOURCE_PRIMARY_FILTER"* ]]; then
    IS_MANAGER=true
    log INFO "[*] ... Setting configuration for MANGER node"
  else
    log INFO "[*] ... Setting configuration for WORKER node"
  fi

  # Swarm is already active
  if [ "$SWARM_STATE" = "active" ]; then
     log INFO "[*] Node is already part of a Docker Swarm cluster."
    return
  fi

  # Initialize Swarm if this is the primary manager
  if [ "$IS_PRIMARY" = true ]; then
    log INFO "[*] ... Initializing new Docker Swarm cluster..."
    docker swarm init --advertise-addr "$RESOURCE_PRIVATEIP"
    log INFO "[*] ... Finished initializing new Docker Swarm cluster..."
  else
    log INFO "[*] ... Joining existing Docker Swarm cluster on $RESOURCE_MANAGERIP..."
    for i in {1..12}; do
      # Get join token
      JOIN_TOKEN=""
      if [ "$IS_MANAGER" = true ]; then
        JOIN_TOKEN=$(ssh $SSH_OPTS root@$RESOURCE_MANAGERIP 'docker swarm join-token manager -q' || echo '')
      else
        JOIN_TOKEN=$(ssh $SSH_OPTS root@$RESOURCE_MANAGERIP 'docker swarm join-token worker -q' || echo '')
      fi

      # Valid tokens?
      if [[ -n "$JOIN_TOKEN" ]]; then
        log INFO "[*] ... Swarm tokens are available on $RESOURCE_MANAGERIP"
        break
      fi

      log WARN "[!] ... Attempt $i: Waiting for Swarm tokens..."
      sleep 5
    done

    if [[ -z "$JOIN_TOKEN" ]]; then
      log ERROR "[x] Timed out waiting for Swarm tokens. Exiting."
      exit 1
    fi

    if [ "$IS_MANAGER" = true ]; then
      log INFO "[*] ... Joining as Swarm Manager..."
      docker swarm join --token "$JOIN_TOKEN" $RESOURCE_MANAGERIP:2377 --advertise-addr "$RESOURCE_PRIVATEIP"
    else
      log INFO "[*] ... Joining as Swarm Worker..."
      docker swarm join --token "$JOIN_TOKEN" $RESOURCE_MANAGERIP:2377 --advertise-addr "$RESOURCE_PRIVATEIP"
    fi

    log INFO "[+] Successfully joined Swarm cluster"
  fi

  log INFO "[*] Configuring Docker Swarm on $HOSTNAME...DONE"
}

# --- Enable Docker service ----------------------------------------------------
enable_docker() {
  log INFO "[*] Ensuring Docker is enabled and running..."

  if ! systemctl is-enabled --quiet docker; then
    log INFO "[*] ... Enabling Docker service..."
    systemctl enable docker
  else
    log INFO "[*] ... Docker service is already enabled."
  fi

  if ! systemctl is-active --quiet docker; then
    log INFO "[*] ... Starting Docker service..."
    systemctl start docker
  else
    log INFO "[*] ... Docker service is already running."
  fi

  log INFO "[+] Docker is enabled and running."
}

# --- Main ---------------------------------------------------------------------
main() {
  log INFO "[*] Starting initialization for remote VM..."

  install_private_key
  install_docker
  install_gluster
  initialize_swarm
  enable_docker

  log INFO "[+] Initialization for remote VM completed successfully."
}

main