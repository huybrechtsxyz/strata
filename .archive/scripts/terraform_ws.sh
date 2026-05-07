#!/bin/bash
# ==============================================================================
# Script Name   : terraform_ws.sh
# Description   : Generate workspace.tfvars for the XYZ Platform workspace.
# Usage         : ./terraform_ws.sh <WORKSPACE_NAME> <WORKSPACE_FILE> <TFVARS_FILE>
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-08
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# --- Prerequisites -----------------------------------------------------------
if ! command -v yq &> /dev/null; then
  echo "[X] ERROR: 'yq' is required but not installed."
  exit 1
fi

# --- Parameters --------------------------------------------------------------
WORKSPACE_NAME=${1:? "WORKSPACE_NAME is required."}
WORKSPACE_FILE=${2:? "WORKSPACE_FILE is required."}
TFVARS_FILE=${3:? "TFVARS_FILE is required."}

# --- Load utilities ----------------------------------------------------------
source "$SCRIPT_DIR/utilities.sh"
validate_script "$SCRIPT_DIR/utilities.sh"

# --- Workspace data ----------------------------------------------------------
log INFO "[*] Generating workspace input file for Terraform"
log INFO "[*] Workspace: $WORKSPACE_NAME in $WORKSPACE_FILE"
set_ws_file "$WORKSPACE_FILE"
set_ws_data "$WORKSPACE_NAME"
check_ws_loaded || exit 1

log INFO "[*] ... Exporting secrets from workspace"
export_variables "$WORKSPACE_FILE" ".spec.variables" "" "" ""
export_secrets "$WORKSPACE_FILE" ".spec.secrets" "" "" ""

# --- Output file generation --------------------------------------------------
{
  echo "# Generated from workspace: $WORKSPACE_FILE"
} > "$TFVARS_FILE"

# --- Kamatera Provider Logic--------------------------------------------------
set_provider_kamatera() {
  log INFO "[*] ... Setting up KAMATERA provider..."
  export kamatera_country=$(get_provider_country)
  export kamatera_region=$(get_provider_region)
  export kamatera_manager_id=$(get_ws_property_kamatera_jumpserver)
  log INFO "[*] ... Setting up KAMATERA provider...DONE"
}

# Loop through each provider in the workspace ---------------------------------
for provider_name in $(get_ws_provider_list); do
  log INFO "[*] ... Found provider name: $provider_name"
  provider_file=$(get_ws_provider_file "$provider_name")
  provider_file="$ROOT_DIR/$provider_file"
  log INFO "[*] ... Found provider file: $provider_file"
  set_provider_file "$provider_file"
  
  {
    echo "# Generated from provider: $provider_file"
  } > "$TFVARS_FILE"

  case "$provider_name" in
    kamatera)
      set_provider_kamatera
      ;;
    *)
      log WARN "[*] Unknown provider: $provider_name"
      ;;
  esac

  # Loop provider variables from YAML
  log INFO "[*] ... Generating TF_VAR_ variables for provider: $provider_name"
  for kv in $(yq -r '.spec.references.variables | to_entries[] | "\(.key)=\(.value)"' "$UTL_PROVIDER_FILE"); do
    key="${kv%%=*}"       # Terraform variable name (e.g., kamatera_api_key)
    envvar="${kv#*=}"     # Name of environment variable to read (e.g., KAMATERA_API_KEY)

    if [[ -z "$envvar" ]]; then
      log ERROR "[X] No environment variable defined for key: $key"
      continue
    fi

    value="${!envvar:-}"  # Get the value of the environment variable

    if [[ -z "$value" ]]; then
      log ERROR "[X] Environment variable '$envvar' is not set or empty (needed for TF_VAR_${key})"
      continue
    fi

    export TF_VAR_${key}="$value"
    echo "Exported TF_VAR_${key} from env var $envvar"
  done

done

# --- Output file generation --------------------------------------------------
{
  echo ""
  echo "kamatera_manager_id = \"$kamatera_manager_id\""
  echo "kamatera_country = \"$kamatera_country\""
  echo "kamatera_region  = \"$kamatera_region\""
  echo ""
} > "$TFVARS_FILE"

# --- VM Resource utility functions --------------------------------------------
set_resource_vm() {
  log INFO "[*] ... Setting up VirtualMachine resource..."
  
  vm_resources+=$(get_resource_virtualmachine)

  log INFO "[*] ... Setting up VirtualMachine resource...DONE"
}

export_resource_vm() {
  echo "virtualmachines = {" >> "$TFVARS_FILE"
  for entry in "${vm_resources[@]}"; do
    printf '%s\n' "$entry" | sed 's/^/  /' >> "$TFVARS_FILE"
  done
  echo "}" >> "$TFVARS_FILE"
  echo "" >> "$TFVARS_FILE"
}

# Process the resource templates for the workspace -----------------------------
vm_resources=()

mapfile -t resources < <(get_ws_resource_list)

for resource_name in "${resources[@]}"; do
  log INFO "[*] ... Found resource name: $resource_name"
  resource_file=$(get_ws_resource_file "$resource_name")
  resource_file="$ROOT_DIR/$resource_file"
  log INFO "[*] ... Found resource file for $resource_name: $resource_file"
  set_resource_file "$resource_file"
  export resource_name=$(get_resource_name)
  export resource_kind=$(get_resource_kind)

  case "$resource_kind" in
    VirtualMachine)
      set_resource_vm
      ;;
    *)
      log WARN "[!] Unknown resource kind: '$resource_kind' in file: $resource_file"
      ;;
  esac
done

# --- Output file generation --------------------------------------------------
export_resource_vm

{
  echo "# Generation complete"
} > "$TFVARS_FILE"

# --- Summary -----------------------------------------------------------------
log INFO "[+] Generated terraform workspace: $TFVARS_FILE"
echo ==========================================
cat "$TFVARS_FILE"
echo ==========================================
log INFO "[+] Terraform workspace generation complete"