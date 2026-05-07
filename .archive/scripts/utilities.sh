#!/bin/bash
# ==============================================================================
# Script Name   : utilities.sh
# Description   : Collection of modular, reusable Bash functions.
# Usage         : source utilities.sh
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-08-11
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

# Logging function
log() {
  local level="$1"; shift
  local msg="$*"
  local timestamp
  timestamp=$(date +"%Y-%m-%d %H:%M:%S")
  case "$level" in
    INFO)    echo -e "[\033[1;34mINFO\033[0m]  - $msg" ;;
    WARN)    echo -e "[\033[1;33mWARN\033[0m]  - $msg" ;;
    ERROR)   echo -e "[\033[1;31mERROR\033[0m] - $msg" >&2 ;;
    *)       echo -e "[UNKNOWN] - $msg" ;;
  esac
}

# Function to load a script and handle errors
# Usage: load_script <script_path>
load_script() {
  local script_path="$1"
  if [[ -f "$script_path" ]]; then
    source "$script_path"
    log INFO "[*] ...Loaded $script_path"
  else
    log ERROR "[X] Missing $(basename "$script_path") at $(dirname "$script_path")"
    exit 1
  fi
}

# Function to load a source file and handle errors
# Usage: load_source <source_path>
# This function checks if the source file exists and sources it.
# If the file does not exist, it logs an error and exits.
load_source() {
  local source_path="$1"
  if [[ -f "$source_path" ]]; then
    +a
    source "$source_path"
    -a
    log INFO "[*] ...Sourced $source_path"
  else
    log ERROR "[X] Missing $(basename "$source_path") at $(dirname "$source_path")"
    exit 1
  fi
}

# Function to validate if a script exists and handle errors
# Usage: validate_script <script_path>
validate_script() {
  local script_path="$1"
  if [[ -f "$script_path" ]]; then
    log INFO "[*] Validated $script_path"
  else
    log ERROR "[X] Missing $(basename "$script_path") at $(dirname "$script_path")"
    exit 1
  fi
}

# Save force removal function
# As a safety precaution, check that the path you're about to wipe isn't / or empty
safe_rm_rf() {
  local path="$1"

  if [[ -z "$path" || "$path" == "/" ]]; then
    log WARN "[!] Skipped unsafe or empty path: '$path'"
    return 1
  fi

  local real_path=$(realpath -m "$path")
  if [[ "$real_path" == "/" ]]; then
    log ERROR "[X] Refusing to remove root directory"
    return 1
  fi

  if [[ -f "$real_path" ]]; then
    log INFO "[*] Removing file: $real_path"
    rm -f "$real_path"
  elif [[ -d "$real_path" ]]; then
    log INFO "[*] Removing directory contents: $real_path"
    shopt -s nullglob dotglob
    rm -rf "$real_path"/*
    shopt -u nullglob dotglob
  else
    log WARN "[!] Skipped non-existent path: $real_path"
    return 0
  fi
}

#================================================================================
# Disk utility functions
#================================================================================

# Function to check if the actual disk size matches the expected size within a tolerance
disk_size_matches() {
  local actual_gb="$1"        # e.g. 39
  local expected_gb="$2"      # e.g. 40
  local tolerance_mb="${3:-20}"  # Optional, default to 20 MiB

  local BYTES_PER_GB=1073741824
  local BYTES_PER_MB=1048576

  local expected_bytes=$(( expected_gb * BYTES_PER_GB ))
  local actual_bytes=$(( actual_gb * BYTES_PER_GB ))
  local diff_bytes=$(( actual_bytes - expected_bytes ))
  local diff_mb=$(( diff_bytes / BYTES_PER_MB ))
  local abs_diff_mb=${diff_mb#-}

  if (( abs_diff_mb <= tolerance_mb )); then
    return 0  # Match within tolerance
  else
    return 1  # Too far off
  fi
}

#================================================================================
# Docker utility functions
#================================================================================

# Function to create a Docker network if it doesn't exist
# Usage: create_docker_network <network_name>
# Example: create_docker_network my_overlay_network
# This function checks if a Docker overlay network exists and creates it if not.
# It requires Docker to be running in Swarm mode.
create_docker_network() {
  local network="$1"
  log INFO "[*] Ensuring Docker network '$network' exists..."

  if docker network inspect "$network" --format '{{.Id}}' &>/dev/null; then
    log INFO "[=] ... Docker network '$network' already exists."
  else
    log INFO "[+] ... Creating Docker overlay network '$network'..."
    if docker network create --driver overlay "$network"; then
      log INFO "[+] ... Docker network '$network' created successfully."
    else
      log ERROR "[X] Failed to create Docker network '$network'. Is Docker Swarm mode enabled?"
      return 1
    fi
  fi
}

# Function to create a Docker secret
# Usage: create_docker_secret <label> <name> <value>
# Example: create_docker_secret my_secret my_secret_name "my_secret_value"
# This function creates a Docker secret if it doesn't already exist.
# If the secret already exists and is in use, it will skip deletion and creation.
# If the secret exists but is not in use, it will remove the old secret and create a new one.
# It also checks if the secret is in use before attempting to remove it.
create_docker_secret() {
  local label="$1"
  local name="$2"
  local value="$3"

  log INFO "[*] ... Processing secret: $label"

  if [[ -z "$name" ]]; then
    log WARN "[!] ... Secret name is not defined for $label. Skipping."
    return 1
  fi

  if [[ -z "$value" ]]; then
    log WARN "[!] ... Secret value for '$name' is empty. Skipping."
    return 0
  fi

  if docker secret inspect "$name" &>/dev/null; then
    log INFO "[*] ... Secret '$name' already exists."

    if is_secret_in_use "$name"; then
      log INFO "[*] ... Secret '$name' is in use. Skipping deletion and creation."
      return 0
    fi

    log INFO "[*] ... Removing old secret '$name'..."
    if ! docker secret rm "$name"; then
      log WARN "[!] Could not remove secret '$name' (possibly still in use). Skipping recreate."
      return 0
    fi
  fi

  # Use printf to avoid trailing newline
  if printf "%s" "$value" | docker secret create "$name" -; then
    log INFO "[+] ... Secret '$name' created."
  else
    log ERROR "[X] ... Failed to create secret '$name'."
    return 1
  fi
}

# Function to check if a Docker secret is in use
# Usage: is_secret_in_use <secret_name>
# Example: is_secret_in_use my_secret
# This function checks if a Docker secret is currently in use by any service or container.
is_secret_in_use() {
  local secret_name="$1"
  local usage_found=0

  # Check services using the secret
  local services_using
  services_using=$(docker service ls --format '{{.Name}}' | \
    xargs -r -n1 -I{} docker service inspect {} --format '{{range .Spec.TaskTemplate.ContainerSpec.Secrets}}{{if eq .SecretName "'"$secret_name"'"}}{{$.Spec.Name}}{{end}}{{end}}' 2>/dev/null | grep -v '^$' || true)

  if [[ -n "$services_using" ]]; then
    log INFO "[*] ... Secret '$secret_name' is in use by Docker service(s): $services_using"
    usage_found=1
  fi

  # Check containers using the secret (running standalone containers might mount secrets differently)
  local containers_using
  containers_using=$(docker ps --format '{{.ID}}' | \
    xargs -r -n1 -I{} docker inspect {} --format '{{range .Mounts}}{{if and (eq .Type "bind") (hasPrefix .Source "/var/lib/docker/swarm/secrets/")}}{{.Name}}{{end}}{{end}}' 2>/dev/null | \
    grep -w "$secret_name" || true)

  if [[ -n "$containers_using" ]]; then
    log INFO "[*] ... Secret '$secret_name' is in use by running container(s)."
    usage_found=1
  fi

  if [[ $usage_found -eq 1 ]]; then
    return 0
  else
    log INFO "[*] ... Secret '$secret_name' is not in use."
    return 1
  fi
}

# Function to load Docker secrets from a file
# Usage: load_docker_secrets <secrets_file>
# Example: load_docker_secrets /path/to/secrets.env
# This function reads a file containing key-value pairs (one per line) and creates Docker secrets
# It skips blank lines and comments, trims whitespace, and removes surrounding quotes from values.
# It also checks if the secret already exists and is in use before attempting to create it.
# If the secret exists but is not in use, it will remove the old secret and create a new one.
load_docker_secrets() {
  
  local secrets_file=${1:-}

  log INFO "[*] Loading secrets from $secrets_file..."

  if [[ ! -f "$secrets_file" ]]; then
    log ERROR "[x] Secrets file not found: $secrets_file"
    return 1
  fi

  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    # Skip blank lines or comments
    [[ -z "$key" || "$key" =~ ^\s*# ]] && continue

    # Trim whitespace
    key=$(echo "$key" | xargs)
    value=$(echo "$value" | xargs)

    # Remove surrounding quotes from value
    value="${value%\"}"
    value="${value#\"}"

    create_docker_secret "$key" "$key" "$value"
  done < "$secrets_file"

  echo "[+] Finished loading secrets."
}

#================================================================================
# Terraform utility functions
#================================================================================

STD_TERRAFORM_FILE="terraform.json"
UTL_TERRAFORM_FILE=""
UTL_TERRAFORM_VM=""

# Load terraform file
load_tf_file() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    log ERROR "[X] terraform file not found: $file" >&2
    return 1
  fi
  
  if ! err=$(jq empty "$file" 2>&1 >/dev/null); then
    log ERROR "[X] terraform file is not valid JSON: $file" >&2
    log ERROR "[X] jq parse error: $err" >&2
    return 1
  fi

  UTL_TERRAFORM_FILE="$(realpath "$file")"
  return 0
}

# Get the correct virtual machine from terraform output
set_tf_server_by_name() {
  local vmname="$1"
  if [[ -z "$vmname" ]]; then
    log ERROR "[X] No resource name given [set_tf_server_by_name]" >&2
    return 1
  fi

  # Get count of exact matches
  local count=$(jq --arg vmname "$vmname" '[.virtualmachines[] | select(.name == $vmname)] | length' "$UTL_TERRAFORM_FILE")
  # Validate match count
  if [[ "$count" -eq 0 ]]; then
    log ERROR "[X] No matching server found for hostname: $vmname" >&2
    return 1
  elif [[ "$count" -gt 1 ]]; then
    log ERROR "[X] Multiple servers ($count) matched hostname: $vmname" >&2
    return 1
  fi

  # Assign the single matching VM name
  UTL_TERRAFORM_VM=$(jq -r --arg vmname "$vmname" '.virtualmachines[] | select(.name == $vmname) | .name' "$UTL_TERRAFORM_FILE")
  log INFO "[*] Server $vmname found in Terraform data"
}

# Check if the terraform module is initialized
check_tf_vm_loaded() {
  if [[ -z "$UTL_TERRAFORM_VM" ]]; then
    log ERROR "[X] No VM data loaded. Did you run set_tf_server_by_name?" >&2
    return 1
  fi
}

# Get a field from a loaded vm
get_tf_vm_field() {
  local field="$1"
  check_tf_vm_loaded || exit 1
  jq -r --arg vmname "$UTL_TERRAFORM_VM" --arg field "$field" \
    '.virtualmachines[] | select(.name == $vmname) | .[$field]' \
    "$UTL_TERRAFORM_FILE"
}

#================================================================================

#"resource":"vm-manager"
get_tf_vm_resource() {
  get_tf_vm_field 'resource'
}

#"public_ip":"185.0.0.1"
get_tf_vm_publicip() {
  get_tf_vm_field 'public_ip'
}

#"private_ip":"10.0.0.1"
get_tf_vm_privateip() {
  get_tf_vm_field 'private_ip'
}

#"private_ip":"10.0.0.1"
get_tf_vm_managerip() {
  get_tf_vm_field 'manager_ip'
}

#================================================================================
# Bootstrap utility functions
#================================================================================

STD_BOOTSTRAP_FILE="workspace.json"
UTL_BOOTSTRAP_FILE=""
UTL_BOOTSTRAP_VM=""

# Load bootstrap file
load_boostrap_file() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    log ERROR "[X] bootstrap file not found: $file" >&2
    return 1
  fi
  
  if ! err=$(jq empty "$file" 2>&1 >/dev/null); then
    log ERROR "[X] bootstrap file is not valid JSON: $file" >&2
    log ERROR "[X] jq parse error: $err" >&2
    return 1
  fi

  UTL_BOOTSTRAP_FILE="$(realpath "$file")"
  return 0
}

# Get the correct virtual machine from resource node name
set_boostrap_vm_by_name() {
  local resxname="$1"
  if [[ -z "$resxname" ]]; then
    log ERROR "[X] No resource name given [set_boostrap_by_name]" >&2
    return 1
  fi

  # Get count of exact matches
  local count=$(jq --arg resxname "$resxname" '[.virtualmachines[] | select(.name == $resxname)] | length' "$UTL_BOOTSTRAP_FILE")
  # Validate match count
  if [[ "$count" -eq 0 ]]; then
    log ERROR "[X] No matching resource found for hostname: $resxname" >&2
    return 1
  elif [[ "$count" -gt 1 ]]; then
    log ERROR "[X] Multiple resources ($count) matched with $resxname" >&2
    return 1
  fi

  # Assign the single matching VM name
  UTL_BOOTSTRAP_VM=$(jq -r --arg resxname "$resxname" '.virtualmachines[] | select(.name == $resxname) | .name' "$UTL_BOOTSTRAP_FILE")
  log INFO "[*] Server $resxname found in Bootstrap data"
}

# Check if the bootstrap module is initialized
check_bootstrap_vm_loaded() {
  if [[ -z "$UTL_BOOTSTRAP_VM" ]]; then
    log ERROR "[X] No Resource data loaded. Did you run set_boostrap_by_name?" >&2
    return 1
  fi
}

get_bootstrap_ws_name() {
  jq -r '.workspace.name' "$UTL_BOOTSTRAP_FILE"
}

get_bootstrap_ws_version() {
  jq -r '.workspace.version' "$UTL_BOOTSTRAP_FILE"
}

# Get a field from a loaded vm
get_bootstrap_vm_field() {
  local field="$1"
  check_bootstrap_vm_loaded || exit 1
  jq -r --arg resxname "$UTL_BOOTSTRAP_VM" --arg field "$field" \
    '.virtualmachines[] | select(.name == $resxname) | .[$field]' \
    "$UTL_BOOTSTRAP_FILE"
}

#================================================================================

# Get the bootstrap VM name of the selected resource
get_bootstrap_vm_name() {
  get_bootstrap_vm_field 'name'
}

# Get the manager node (only find the first one)
get_bootstrap_vm_manager() {
  local field="$1"
  jq -r --arg field "$field" \
    '[.virtualmachines[] | select(.ismanager == true)][0] | .[$field]' \
    "$UTL_BOOTSTRAP_FILE"
}

# Get the manager node ROLE 
get_bootstrap_vm_manager_label() {
  role=$(get_bootstrap_vm_manager 'role')
  echo "$role-1"
}

# Get the manager node FILTER
get_bootstrap_vm_manager_filter() {
  role=$(get_bootstrap_vm_manager 'role')
  echo "-$role-"
}

#================================================================================

# Get disks from virtualmachine resource template
get_bootstrap_vm_disk_count() {
  check_bootstrap_vm_loaded || exit 1
  jq -r --arg resxname "$UTL_BOOTSTRAP_VM" \
    '.virtualmachines[] | select(.name == $resxname) | .disks | length' \
    "$UTL_BOOTSTRAP_FILE"
}

# Extract disk sizes as comma-separated values from a template file
get_bootstrap_vm_disk_sizelist() {
  check_bootstrap_vm_loaded || exit 1
  jq -r --arg resxname "$UTL_BOOTSTRAP_VM" \
    '.virtualmachines[] | select(.name == $resxname) | [.disks[].size] | join(",")' \
    "$UTL_BOOTSTRAP_FILE"
}

# Get the disk label from a specific disk index
get_bootstrap_vm_disk_label() {
  local disk_index="$1"
  check_bootstrap_vm_loaded || exit 1
  jq -r --arg resxname "$UTL_BOOTSTRAP_VM" --argjson disk_index "$disk_index" \
    '.virtualmachines[] | select(.name == $resxname) | .disks[$disk_index].label' \
    "$UTL_BOOTSTRAP_FILE"
}

# Get the disk label from a specific disk index
get_bootstrap_vm_disk_mount() {
  local disk_index="$1"
  check_bootstrap_vm_loaded || exit 1
  jq -r --arg resxname "$UTL_BOOTSTRAP_VM" --argjson disk_index "$disk_index" \
    '.virtualmachines[] | select(.name == $resxname) | .disks[$disk_index].mount' \
    "$UTL_BOOTSTRAP_FILE"
}

# Get the disk label from a specific disk index
get_bootstrap_vm_disk_size() {
  local disk_index="$1"
  check_bootstrap_vm_loaded || exit 1
  jq -r --arg resxname "$UTL_BOOTSTRAP_VM" --argjson disk_index "$disk_index" \
    '.virtualmachines[] | select(.name == $resxname) | .disks[$disk_index].size' \
    "$UTL_BOOTSTRAP_FILE"
}

# Retrieve the value of a variable by its key from workspace.json
# Usage: get_workspace_variable <key> <workspace_json_file>
get_workspace_variable_value() {
  local key="$1"
  local file="$2"
  if [ -z "$key" ] || [ -z "$file" ] || ! [ -f "$file" ]; then
    echo ""
    return 1
  fi
  # Use jq to find the variable by key (case-insensitive)
  local value
  value=$(jq -r --arg k "$key" '
    .variables[]? | select(.key | ascii_downcase == ($k | ascii_downcase)) | .value // empty
  ' "$file")
  echo "$value"
}