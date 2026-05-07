#!/bin/bash
# ==============================================================================
# Script Name   : initialize_vm.sh
# Description   : Initialize the remote virtual machine.
# Usage         : ./initialize_vm.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-11
# Last Modified : 2025-10-08
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -- Log everything to file and stdout -----------------------------------------
LOG_FILE="/var/log/initialize_vm.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

# -- Environment variables -----------------------------------------------------
if [ -n "$1" ]; then
  export RESOURCE_HOSTNAME="$1"
fi
if [ -n "$2" ]; then
  export BUILD_PATH=$2
fi

: ${RESOURCE_HOSTNAME:?"RESOURCE_HOST is not set. Please set it to the server name."}
: ${BUILD_PATH:?"BUILD_PATH is not set. Please set it to the build path."}

# --- Load common start --------------------------------------------------------
source "$SCRIPT_DIR/utilities.sh"
validate_script "$SCRIPT_DIR/utilities.sh"
log INFO "[*] Loaded $SCRIPT_DIR/utilities.sh"

# --- Load terraform data ------------------------------------------------------
TERRAFORM_FILE=${"$BUILD_PATH/$STD_TERRAFORM_FILE"}
: ${TERRAFORM_FILE:?"TERRAFORM_FILE is not set. Please set it to the path of the TFOUTPUT_FILE."}
log INFO "[*] Loading Terraform data from $TERRAFORM_FILE"
load_tf_file "$TERRAFORM_FILE"
set_tf_server_by_name "$RESOURCE_HOSTNAME"

log INFO "[*] ... Getting resource ip-addresses"
RESOURCE_RESXNAME=$(get_tf_vm_resource)
RESOURCE_MANAGERIP=$(get_tf_vm_managerip)
RESOURCE_PRIVATEIP=$(get_tf_vm_privateip)
RESOURCE_PUBLICIP=$(get_tf_vm_publicip)
: ${RESOURCE_RESXNAME:?"RESOURCE_RESXNAME is not set. Please set it to the resource name from terraform data."}
: ${RESOURCE_MANAGERIP:?"RESOURCE_MANAGERIP is not set. Please set it to the manager IP from terraform data."}
: ${RESOURCE_PRIVATEIP:?"RESOURCE_PRIVATEIP is not set. Please set it to the private IP from terraform data."}
: ${RESOURCE_PUBLICIP:?"RESOURCE_PUBLICIP is not set. Please set it to the public IP from terraform data."}
log INFO "[*] ... Getting resource ip-addresses...DONE"

# --- Load bootstrap data ------------------------------------------------------
BOOTSTRAP_FILE=${"$BUILD_PATH/$STD_BOOTSTRAP_FILE"}
: ${BOOTSTRAP_FILE:?"BOOTSTRAP_FILE is not set. Please set it to the path of the workspace definition file."}
log INFO "[*] Loading Bootstrap data from $BOOTSTRAP_FILE"
load_bootstrap_file "$BOOTSTRAP_FILE"
set_boostrap_vm_by_name "$RESOURCE_RESXNAME"

# Set environment variables
log INFO "[*] ... Getting resource primary manager info"
RESOURCE_PRIMARY_NAME=$(get_bootstrap_vm_manager_label)
RESOURCE_PRIMARY_FILTER=$(get_bootstrap_vm_manager_filter)
: ${RESOURCE_PRIMARY_NAME:?"RESOURCE_PRIMARY_NAME is not set. Please set it to the primary manager name from bootstrap data."}
: ${RESOURCE_PRIMARY_FILTER:?"RESOURCE_PRIMARY_FILTER is not set. Please set it to the primary manager filter from bootstrap data."}
log INFO "[*] ... Getting resource primary manager info...DONE"

# Function to initialize firewall rules
# Funtion reads the bootstrap workspace metadata to find firewall rules and applies them using ufw (Uncomplicated Firewall).
# It ensures that ufw is installed and enabled, and then iterates over the firewall rules defined in the workspace metadata,
# applying each rule using the appropriate ufw command.
initialize_firewall() {
  # Ensure ufw is installed
  if ! command -v ufw &>/dev/null; then
    log ERROR "[!] UFW (Uncomplicated Firewall) is not installed !"
    return 1
  fi

  # Check if the boostrap file loaded correctly
  if [ -z "$UTL_BOOTSTRAP_FILE" ]; then
    log ERROR "[!] Bootstrap data is not loaded. Cannot initialize firewall."
    return 1
  fi

  log INFO "[*] Initializing firewall rules..."

  # Find the correct VM block in workspace definition
  local vm_json
  vm_json=$(jq -c ".virtualmachines[] | select(.name == \"$RESOURCE_RESXNAME\")" "$UTL_BOOTSTRAP_FILE")
  if [ -z "$vm_json" ]; then
    log ERROR "[!] No VM block found for $RESOURCE_RESXNAME in workspace definition."
    return 1
  fi

  # Get the firewall block
  local fw_json
  fw_json=$(echo "$vm_json" | jq -c ".firewall")
  if [ -z "$fw_json" ] || [ "$fw_json" = "null" ]; then
    log WARN "[!] No firewall block found for $RESOURCE_RESXNAME. Skipping firewall setup."
    return 0
  fi

  # 1. Process reset
  local reset
  reset=$(echo "$fw_json" | jq -r ".reset")
  if [ "$reset" = "true" ]; then
    log INFO "[*] Resetting UFW rules..."
    ufw --force reset
    ufw default deny incoming
    ufw default deny outgoing
  fi

  # 2. Process defaults
  local defaults_count
  defaults_count=$(echo "$fw_json" | jq ".defaults | length")
  if [ "$defaults_count" -gt 0 ]; then
    for i in $(seq 0 $((defaults_count - 1))); do
      local direction permission comment
      direction=$(echo "$fw_json" | jq -r ".defaults[$i].direction")
      permission=$(echo "$fw_json" | jq -r ".defaults[$i].permission")
      comment=$(echo "$fw_json" | jq -r ".defaults[$i].comment")
      log INFO "[*] Setting default $direction $permission ($comment)"
      if [ "$direction" = "in" ] && [ "$permission" = "deny" ]; then
        ufw default deny incoming
      elif [ "$direction" = "out" ] && [ "$permission" = "deny" ]; then
        ufw default deny outgoing
      elif [ "$direction" = "in" ] && [ "$permission" = "allow" ]; then
        ufw default allow incoming
      elif [ "$direction" = "out" ] && [ "$permission" = "allow" ]; then
        ufw default allow outgoing
      fi
    done
  fi

  # 3. Process deny rules
  local deny_count
  deny_count=$(echo "$fw_json" | jq ".deny | length")
  if [ "$deny_count" -gt 0 ]; then
    for i in $(seq 0 $((deny_count - 1))); do
      local direction proto port interface from to comment
      direction=$(echo "$fw_json" | jq -r ".deny[$i].direction")
      proto=$(echo "$fw_json" | jq -r ".deny[$i].proto")
      port=$(echo "$fw_json" | jq -r ".deny[$i].port")
      interface=$(echo "$fw_json" | jq -r ".deny[$i].interface")
      from=$(echo "$fw_json" | jq -r ".deny[$i].from")
      to=$(echo "$fw_json" | jq -r ".deny[$i].to")
      comment=$(echo "$fw_json" | jq -r ".deny[$i].comment")
      log INFO "[*] Deny rule: $direction $proto $port $interface $from $to ($comment)"
      # Build ufw deny command
      local ufw_cmd="ufw deny"
      if [ -n "$proto" ]; then ufw_cmd+=" proto $proto"; fi
      if [ -n "$port" ]; then ufw_cmd+=" $port"; fi
      if [ -n "$from" ]; then ufw_cmd+=" from $from"; fi
      if [ -n "$to" ]; then ufw_cmd+=" to $to"; fi
      if [ -n "$interface" ]; then ufw_cmd+=" in on $interface"; fi
      $ufw_cmd || log WARN "[!] Failed to apply deny rule: $ufw_cmd"
    done
  fi

  # 4. Process allow rules
  local allow_count
  allow_count=$(echo "$fw_json" | jq ".allow | length")
  if [ "$allow_count" -gt 0 ]; then
    for i in $(seq 0 $((allow_count - 1))); do
      local direction proto port interface from to comment
      direction=$(echo "$fw_json" | jq -r ".allow[$i].direction")
      proto=$(echo "$fw_json" | jq -r ".allow[$i].proto")
      port=$(echo "$fw_json" | jq -r ".allow[$i].port")
      interface=$(echo "$fw_json" | jq -r ".allow[$i].interface")
      from=$(echo "$fw_json" | jq -r ".allow[$i].from")
      to=$(echo "$fw_json" | jq -r ".allow[$i].to")
      comment=$(echo "$fw_json" | jq -r ".allow[$i].comment")
      log INFO "[*] Allow rule: $direction $proto $port $interface $from $to ($comment)"
      # Build ufw allow command
      local ufw_cmd="ufw allow"
      if [ -n "$proto" ]; then ufw_cmd+=" proto $proto"; fi
      if [ -n "$port" ]; then ufw_cmd+=" $port"; fi
      if [ -n "$from" ]; then ufw_cmd+=" from $from"; fi
      if [ -n "$to" ]; then ufw_cmd+=" to $to"; fi
      if [ -n "$interface" ]; then ufw_cmd+=" in on $interface"; fi
      $ufw_cmd || log WARN "[!] Failed to apply allow rule: $ufw_cmd"
    done
  fi

  log INFO "[*] Initializing firewall rules... DONE"
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
  local disk_count=$(get_bootstrap_vm_disk_count)
  log INFO "[*] ... Found $disk_count disks for $HOSTNAME (including OS disk)"
  if (( ${#disk_names[@]} < disk_count )); then
    log ERROR "[!] Only found ${#disk_names[@]} disks but expected $disk_count"
    return 1
  fi
  
  # Loop all disks found
  log INFO "[*] Looping over all disks"
  for i in $(seq 0 $((disk_count - 1))); do
    log INFO "[*] Mounting disk $i for $HOSTNAME"

    local disk="/dev/${disk_names[$i]}"
    local label=$(get_bootstrap_vm_disk_label "$i")
    local mount_template=$(get_bootstrap_vm_disk_mount "$i")
    local part=""
    local fs_type=""
    local current_label=""

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
    local expected_size_gb=$(get_bootstrap_vm_disk_size "$i")

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

# Function to create a GlusterFS cluster
# It reads the Terraform configuration file to get the private IPs of the nodes
# It then probes each node to add it to the cluster, and detaches any nodes that
# are no longer in the desired state
# It also waits for the peers to connect and shows the status of the cluster
# Returns 0 on success, 1 on failure
create-fs-cluster() {
  log INFO "[*] Creating GlusterFS cluster..."

  # Determine if this node is the primary manager
  if [[ "$HOSTNAME" == *"$RESOURCE_PRIMARY_NAME"* ]]; then
    log INFO "[*] ... Setting configuration for PRIMARY MANGER"
  else
    log INFO "[*] ... Skipping GlusterFS configuration for NON-PRIMARY MANGER"
    return 0
  fi

  # Get current peer IPs from Gluster
  readarray -t CURRENT_PEERS < <(
    gluster peer status | awk '/Hostname:/ {print $2}'
  )
  log INFO "[*] Current peers $CURRENT_PEERS..."

  # Extract all private IPs
  log INFO "[*] Extracting private ips from $TERRAFORM_FILE..."
  readarray -t PRIVATE_IPS < <(
    jq -r '.virtualmachines[].private_ip' "$TERRAFORM_FILE"
  )

  # Add any missing peers
  log INFO "[*] Add any missing peers"
  for ip in "${PRIVATE_IPS[@]}"; do
    if [[ "$ip" == "$RESOURCE_MANAGERIP" ]]; then
      continue
    fi
    if printf '%s\n' "${CURRENT_PEERS[@]}" | grep -q "^$ip$"; then
      log INFO "[*] Peer $ip already connected."
    else
      log INFO "[*] Probing new peer $ip..."
      gluster peer probe "$ip"
    fi
  done

  # Remove peers no longer in desired state
  log INFO "[*] Remove peers no longer in desired state"
  for ip in "${CURRENT_PEERS[@]}"; do
    if [[ "$ip" == "$RESOURCE_MANAGERIP" ]]; then
      continue
    fi
    if printf '%s\n' "${PRIVATE_IPS[@]}" | grep -q "^$ip$"; then
      # Still desired
      continue
    else
      log WARN "[!] Peer $ip is no longer in configuration. Detaching..."
      gluster peer detach "$ip" force || echo "[ERROR] Failed to detach $ip"
    fi
  done

  # Wait for peers
  log INFO "[*] Waiting for peers to connect..."
  sleep 5
  gluster peer status

  log INFO "[+] GlusterFS cluster created successfully."
}

# --- Main ---------------------------------------------------------------------
main() {
  log INFO "[*] Starting initialization for remote VM..."

  initialize_firewall || exit 1  # Setup firewall rules
  initialize_disks || exit 1  # Prepare and mount disk volumes
  initialize_swarm || exit 1  # Initialize or join Docker Swarm

  log INFO "[+] Initialization of remote VM completed successfully."
}

main "$@"