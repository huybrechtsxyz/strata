#!/bin/bash
# ==============================================================================
# Script Name   : configure_vm.sh
# Description   : Configure the remote virtual machine.
# Usage         : ./configure_vm.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-11
# Last Modified : 2025-09-05
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Log everything to file and stdout
LOG_FILE="/var/log/configure_vm.log"
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
RESOURCE_RESXNAME=$(get_tf_vm_resource)

# --- Load bootstrap data ------------------------------------------------------
BOOTSTRAP_FILE=${"$BUILD_PATH/$STD_BOOTSTRAP_FILE"}
: ${BOOTSTRAP_FILE:?"BOOTSTRAP_FILE is not set. Please set it to the path of the workspace definition file."}
log INFO "[*] Loading Bootstrap data from $BOOTSTRAP_FILE"
load_bootstrap_file "$BOOTSTRAP_FILE"
set_boostrap_vm_by_name "$RESOURCE_RESXNAME"
RESOURCE_PRIMARY_NAME=$(get_bootstrap_vm_manager_label)
WORKSPACE_NAME=$(get_bootstrap_ws_name)
WORKSPACE_VERSION=$(get_bootstrap_ws_version)

# --- List server paths -------------------------------------------------------
list_server_paths() {
  log INFO "[*] Workspace server paths on host $HOSTNAME"
  if [[ ! -f "$WORKSPACE_FILE" ]]; then
    echo "[X] Workspace file not found: $WORKSPACE_FILE" >&2
    return 1
  fi

  # Check if the SERVER_ID exists
  if ! jq -e --arg id "$SERVER_ID" '.workspace.servers[] | select(.id == $id)' "$WORKSPACE_FILE" > /dev/null; then
    echo "[X] Server ID not found in workspace: $SERVER_ID" >&2
    return 1
  fi

  # Extract and iterate over the .paths[] for the specified server
  jq -r --arg id "$SERVER_ID" '
    .workspace.servers[] 
    | select(.id == $id) 
    | .paths[]? 
    | .path
  ' "$WORKSPACE_FILE" | while read -r path; do
    log INFO "[x] ... Listing contents of: $path"
    if [[ -d "$path" ]]; then
      ls -la "$path"
    else
      echo "[!] Directory does not exist: $path"
    fi
  done
}

# Function that creates docker labels for each node based on its hostname
# The labels are:
# - role: the role of the node (e.g. manager, worker)
# - server: the server name (e.g. manager-1, worker-2)
# - instance: the instance number (e.g. 1, 2)
# - role=true: a boolean label indicating the role
# This function reads the workspace file to get the node information and applies the labels to all nodes
# It also reads the existing labels and updates them if they differ or are missing
# Finally, it removes any labels that exist but are not desired
create_docker_labels() {
  # Get the current hostname
  log INFO "[*] Applying role label to all nodes..."
  local srvrole nodes

  # Get current hostname and parse role (3rd part of hyphen-separated hostname)
  srvrole=$(echo "$HOSTNAME" | cut -d'-' -f3)
  log INFO "[*] ... Detected role: $srvrole"

  # Workspace JSON file path (environment variables assumed set)
  log INFO "[*] ... Using workspace file: $UTL_BOOTSTRAP_FILE"

  # Get list of all Docker Swarm node hostnames
  log INFO "[*] ... Getting node hostnames"
  mapfile -t nodes < <(docker node ls --format '{{.Hostname}}')
  log INFO "[*] ... Found ${#nodes[@]} nodes"

  for node in "${nodes[@]}"; do
    log INFO "[*] ... Applying role label to $node..."

    # Parse role, instance from node name (3rd and 4th hyphen-separated parts)
    local role instance server
    role=$(echo "$node" | cut -d'-' -f3)
    instance=$(echo "$node" | cut -d'-' -f4)
    server="${role}-${instance}"
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

    # Add custom labels from workspace JSON (jq filters by node id)
    log INFO "[*] ...... Add custom labels from workspace on $node"
    mapfile -t ws_labels < <(jq -r --arg id "$RESOURCE_RESXNAME" '.virtualmachines[] | select(.resource == $id) | .labels[]?' "$UTL_BOOTSTRAP_FILE")
    for label in "${ws_labels[@]}"; do
      # Split label key and value (format is "key":"value" in JSON)
      #local key="${label%%=*}"
      #local val="${label#*=}"
      local key=$(echo "$label" | jq -r '.key')
      local val=$(echo "$label" | jq -r '.value')

      # Add or update label if needed
      if [[ "${existing_labels[$key]}" != "$val" ]]; then
        log INFO "[*] ...... Adding custom label $label"
        docker node update --label-add "$label" "$node" || echo "[!] Warning: Failed to add $label on $node"
      fi

      # Mark as desired to avoid removal
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

# ---Main function to configure the remote server based on its role-------------
main_manager() {
  log INFO "[*] Configuring Manager Node: $HOSTNAME..."

  # Create docker networks
  create_docker_network "wan-$WORKSPACE_NAME"
  create_docker_network "lan-$WORKSPACE_NAME"

  # Moving this to the actual deployment?
  # Create docker secrets and remove file
  # create_secret_file TODO 
  # load_docker_secrets "$PATH_CONFIG/secrets.env"
  # safe_rm_rf "$PATH_CONFIG/secrets.env"

  # Create the required swarm labels
  create_docker_labels || {
    log ERROR "[X] Failed to create node labels."
    return 1
  }

  # Create the directories/volumes
  # create-fs-volumes || {
  #   log ERROR "[X] Failed to create GlusterFS volumes."
  #   return 1
  # }

  # Create the workspace
  # create_workspace || {
  #   log ERROR "[X] Failed to create workspace."
  #   return 1
  # }

  # List the content of the server paths
  list_server_paths
  log INFO "[+] Configuring Manager Node: $HOSTNAME...DONE"
}

# --- Main function to configure the worker node -------------------------------
main_worker() {
  log INFO "[*] Configuring Worker Node: $HOSTNAME..."
  
  # List the content of the server paths
  list_server_paths

  log INFO "[+] Configuring Worker Node: $HOSTNAME...DONE"
}

# --- Main ---------------------------------------------------------------------
main() {
  log INFO "[*] Starting configuration for remote VM..."

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

  log INFO "[+] Configuration of remote VM completed successfully."
}

main "$@"