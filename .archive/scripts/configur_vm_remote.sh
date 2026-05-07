#!/bin/bash
# ==============================================================================
# Script Name   : configure_vm_remote.sh
# Description   : Configure the remote virtual machine.
# Usage         : ./configure_vm_remote.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-11
# Last Modified : 2025-08-11
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Load common start --------------------------------------------------------
source "$SCRIPT_DIR/common_start.sh"
validate_script "$SCRIPT_DIR/common_start.sh"

# Function that creates docker labels for each node based on its hostname
# The labels are:
# - role: the role of the node (e.g. manager, worker)
# - server: the server name (e.g. manager-1, worker-2)
# - instance: the instance number (e.g. 1, 2)
# - role=true: a boolean label indicating the role
# This function reads the workspace file to get the node information and applies the labels to all nodes
# It also reads the existing labels and updates them if they differ or are missing
# Finally, it removes any labels that exist but are not desired
configure_docker_labels() {
  # Get the current hostname
  log INFO "[*] Applying role label to all nodes..."
  local srvrole nodes

  # Get current hostname and parse role (3rd part of hyphen-separated hostname)
  srvrole=$(get_role_from_hostname "$HOSTNAME")
  log INFO "[*] ... Detected role: $srvrole"

  # Workspace JSON file path (environment variables assumed set)
  log INFO "[*] ... Using workspace file: $WORKSPACE_FILE"

  # Get list of all Docker Swarm node hostnames
  log INFO "[*] ... Getting node hostnames"
  mapfile -t nodes < <(docker node ls --format '{{.Hostname}}')
  log INFO "[*] ... Found ${#nodes[@]} nodes"

  for node in "${nodes[@]}"; do
    log INFO "[*] ... Applying role labels to $node..."

    # Parse role, instance from node name (3rd and 4th hyphen-separated parts)
    local role instance server
    role=$(get_role_from_hostname "$node")
    instance=$(get_instance_from_hostname "$node")
    server=$(get_label_from_hostname "$node")
    log INFO "[*] ...... Setting $role=true on $node"
    log INFO "[*] ...... Setting role=$role on $node"
    log INFO "[*] ...... Setting server=$server on $node"
    log INFO "[*] ...... Setting instance=$instance on $node"

    # Initialize associative arrays
    declare -A existing_labels
    declare -A desired_labels

    # Read existing labels into associative array
    log INFO "[*] ...... Reading existing labels on $node"
    while IFS='=' read -r k v; do
      [[ -z "$k" && -z "$v" ]] && continue  # Skip empty lines or pure "="
      if [[ "$k" =~ ^[a-zA-Z0-9_.-]+$ && -n "$v" ]]; then
        existing_labels["$k"]="$v"
      else
        log WARN "[!] Skipping malformed label: $k=$v"
      fi
    done < <(
      docker node inspect "$node" \
        --format '{{range $k, $v := .Spec.Labels}}{{printf "%s=%s\n" $k $v}}{{end}}' \
        | grep -E '^[^=]+=[^=]+$'  # Optional extra guard
    )

    # Define desired standard labels
    desired_labels["$role"]="true"
    desired_labels["role"]="$role"
    desired_labels["server"]="$server"
    desired_labels["instance"]="$instance"

    # Update/add standard labels if they differ or are missing
    log INFO "[*] ...... Update/add standard labels $node"
    for key in "${!desired_labels[@]}"; do
      if [[ "${existing_labels[$key]}" != "${desired_labels[$key]}" ]]; then
        log INFO "[*] ...... Setting $key=${desired_labels[$key]}"
        docker node update --label-add "$key=${desired_labels[$key]}" "$node" || echo "[!] Warning: Failed to set $key on $node"
      fi
    done

    log INFO "[*] ...... Add custom labels from workspace on $node"

    # Capture all labels into an array
    # Get the resource name from the terraform file based on the node hostname
    local resxname=$(get_tf_resource_name "$node")
    mapfile -t custom_labels < <(get_ws_resx_labels "$resxname")

    # Debug log all labels before applying
    if [[ ${#custom_labels[@]} -eq 0 ]]; then
      log INFO "[*] ...... No custom labels found for role=$role"
    else
      log INFO "[*] ...... Custom labels for role=$role:"
      for kv in "${custom_labels[@]}"; do
        log INFO "           $kv"
      done
    fi

    # Apply labels
    for kv in "${custom_labels[@]}"; do
      key=${kv%%=*}
      val=${kv#*=}

      if [[ -z "$key" ]]; then
        log WARN "[!] Skipping malformed label: '$kv'"
        continue
      fi

      if [[ "${existing_labels[$key]}" != "$val" ]]; then
        log INFO "[*] ...... Adding custom label $key=$val"
        docker node update --label-add "$key=$val" "$node" || \
          echo "[!] Warning: Failed to add $key=$val on $node"
      fi

      desired_labels["$key"]="$val"
    done

    # Remove any labels that exist but are not desired
    log INFO "[*] ...... Cleaning up obsolete labels on $node"
    for key in "${!existing_labels[@]}"; do
      if [[ -z "${desired_labels[$key]}" ]]; then
        log INFO "[*] ...... Removing $key"
        docker node update --label-rm "$key" "$node" || echo "[!] Warning: Failed to remove $key on $node"
      fi
    done

    # Clean up arrays before next iteration
    unset existing_labels
    unset desired_labels
  done

  log INFO "[+] Applying role label to all nodes...DONE"
  return 0
}

# Function to create a GlusterFS cluster
# It reads the Terraform configuration file to get the private IPs of the nodes
# It then probes each node to add it to the cluster, and detaches any nodes that
# are no longer in the desired state
# It also waits for the peers to connect and shows the status of the cluster
# Returns 0 on success, 1 on failure
configure_fs_cluster() {
  log INFO "[*] Creating GlusterFS cluster..."

  # Get current Gluster peers
  readarray -t CURRENT_PEERS < <(
    gluster peer status 2>/dev/null | awk '/Hostname:/ {print $2}'
  )
  log INFO "[*] Current peers: ${CURRENT_PEERS[*]:-(none)}"

  # Extract all desired private IPs from Terraform output
  log INFO "[*] Extracting private IPs from $TERRAFORM_FILE..."
  readarray -t PRIVATE_IPS < <(get_private_ips)
  log INFO "[*] Desired peers: ${PRIVATE_IPS[*]:-(none)}"

  # Sanity check: make sure we got something
  if [[ ${#PRIVATE_IPS[@]} -eq 0 ]]; then
    log ERROR "[!] No private IPs found in $TERRAFORM_FILE"
    return 1
  fi

  # Add missing peers
  log INFO "[*] Probing any missing peers..."
  for ip in "${PRIVATE_IPS[@]}"; do
    if [[ "$ip" == "$RESOURCE_MANAGERIP" ]]; then
      continue
    fi
    if printf '%s\n' "${CURRENT_PEERS[@]}" | grep -qx "$ip"; then
      log DEBUG "[*] Peer $ip already connected."
    else
      log INFO "[*] Probing new peer $ip..."
      if gluster peer probe "$ip"; then
        log INFO "[+] Successfully probed $ip"
      else
        log ERROR "[!] Failed to probe $ip"
      fi
    fi
  done

  # Remove stale peers
  log INFO "[*] Checking for stale peers..."
  for ip in "${CURRENT_PEERS[@]}"; do
    if [[ "$ip" == "$RESOURCE_MANAGERIP" ]]; then
      continue
    fi
    if printf '%s\n' "${PRIVATE_IPS[@]}" | grep -qx "$ip"; then
      log DEBUG "[*] Peer $ip still desired."
    else
      log WARN "[!] Peer $ip is no longer in configuration. Detaching..."
      if gluster peer detach "$ip" force; then
        log INFO "[+] Detached stale peer $ip"
      else
        log ERROR "[!] Failed to detach $ip"
      fi
    fi
  done

  # Wait for cluster to stabilize
  log INFO "[*] Waiting for peers to connect..."
  sleep 5
  gluster peer status

  log INFO "[+] GlusterFS cluster created/updated successfully."
}

# Function to create the base server paths for GlusterFS volumes
# It reads the workspace file to get the paths and mount points for each server
# It then creates the directories on each server via SSH
# It then creates the GlusterFS volumes based on the paths
# It then creates environment variables for the paths > /PATH_CONFIG/workspace.env
# At the end, it starts the volumes
# ------------------------------------------------------------------------------
# NOTE: This function uses 'force' when creating GlusterFS volumes.
#
# Rationale for using 'force':
#   - Many nodes in this cluster do not have extra attached disks.
#   - GlusterFS is only used here for synchronizing config files and small operational data.
#   - Persistent workloads and stateful services store data in Redis, Consul, or Postgres.
#   - Therefore, creating volumes on the root (/) filesystem is acceptable.
#
# WARNING:
#   - If you expand GlusterFS usage to store large data, re-evaluate this decision.
#   - Monitor root filesystem capacity to avoid filling up the OS partition.
# ------------------------------------------------------------------------------
configure_fs_volumes() {
  log INFO "[*] Creating GlusterFS volumes..."
  log INFO "[*] Workspace '$WORKSPACE_NAME' setup target server ID: $HOSTNAME"

  declare -A bricks_map
  declare -A volume_map
  declare -A desired_volumes

  # Load terraform server data
  log INFO "[*] ... Loading terraform data..."
  mapfile -t servers < <(get_tf_vm_all)
  server_count=${#servers[@]}
  log INFO "[*] ... Terraform data loaded: $server_count servers found: $(get_tf_vm_all_labels | paste -sd ',')" 

  ### TODO : TO COMPLETE

  log INFO "[+] GlusterFS volumes created successfully."
}

create_workspace() {
}

# Main function to configure the remote server based on its role----------------
main_manager() {
  log INFO "[*] Configuring Manager Node: $HOSTNAME..."

  # Create docker networks
  create_docker_network "wan-$WORKSPACE"
  create_docker_network "lan-$WORKSPACE"

  # Create docker secrets and remove file
  load_docker_secrets "$PATH_CONFIG/secrets.env"
  safe_rm_rf "$PATH_CONFIG/secrets.env"

  # Create the required swarm labels
  configure_docker_labels || {
    log ERROR "[X] Failed to create node labels."
    return 1
  }

  # Create the glusterfs cluster
  configure_fs_cluster || {
    log ERROR "[X] Failed to create GlusterFS cluster."
    return 1
  }

  # Create the directories/volumes
  configure_fs_volumes || {
    log ERROR "[X] Failed to create GlusterFS volumes."
    return 1
  }

  # Create the workspace
  create_workspace || {
    log ERROR "[X] Failed to create workspace."
    return 1
  }

  log INFO "[+] Configuring Manager Node: $HOSTNAME...DONE"
}

# Main function to configure the worker node -----------------------------------
main_worker() {
  log INFO "[*] Configuring Worker Node: $HOSTNAME..."
  
  # List the content of the server paths
  list_server_paths

  log INFO "[+] Configuring Worker Node: $HOSTNAME...DONE"
}

# --- Main ---------------------------------------------------------------------
main() {
  log INFO "[*] Starting configuration for remote VM: $HOSTNAME..."

  docker info --format '{{.Swarm.LocalNodeState}}' | grep -q "active" || {
    log ERROR "[!] Docker Swarm is not active. Run 'docker swarm init' first."
    exit 1
  }

  # Determine if this node is the primary manager
  IS_PRIMARY=false
  if [[ "$HOSTNAME" == *"$RESOURCE_PRIMARY_NAME"* ]]; then
    IS_PRIMARY=true
    log INFO "[*] ... Setting configuration for PRIMARY MANGER"
  fi


  if [[ "$IS_PRIMARY" == "true" ]]; then
    main_manager || {
      log ERROR "[!] Failed to configure manager node."
      exit 1
    }
  else
    main_worker || {
      log ERROR "[!] Failed to configure worker node."
      exit 1
    }
  fi

  log INFO "[+] Configuration for remote VM $HOSTNAME completed successfully."
}

main