#!/bin/bash
# ==============================================================================
# Script Name   : terraform_workspace.sh
# Description   : Generate workspace.tfvars for the XYZ Platform workspace.
# Usage         : ./terraform_workspace.sh <WORKSPACE_FILE> <OUTPUT_FILE>
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-08
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Prerequisites -----------------------------------------------------------
if ! command -v yq &> /dev/null; then
  echo "[X] ERROR: 'yq' is required but not installed."
  exit 1
fi

# --- Parameters --------------------------------------------------------------
WORKSPACE_NAME=${1:? "WORKSPACE_NAME is required."}
WORKSPACE_FILE=${2:? "WORKSPACE_FILE is required."}
OUTPUT_FILE=${3:? "OUTPUT_FILE is required."}

# --- Load utilities ----------------------------------------------------------
source "$SCRIPT_DIR/utilities.sh"
validate_script "$SCRIPT_DIR/utilities.sh"

# --- Workspace data ----------------------------------------------------------
log INFO "[*] Generating workspace input file for Terraform"
log INFO "[*] Workspace: $WORKSPACE_NAME in $WORKSPACE_FILE"
set_ws_file "$WORKSPACE_FILE"
set_ws_data "$WORKSPACE_NAME"
check_ws_loaded || exit 1

# Provider-specific parameters
kamatera_country=$(get_ws_query '.spec.providers[] | select(.name == "kamatera") | .properties.country')
kamatera_region=$(get_ws_query '.spec.providers[] | select(.name == "kamatera") | .properties.region')

# Jumpserver ID
# manager_id=$(get_ws_query '.spec.properties.primaryMachine')
manager_id=$(get_workspace_primary_name)

# --- Resource collection -----------------------------------------------------
vm_resources=()

build_vm_entry() {
  local name="$1" role="$2" provider="$3" count="$4" template_file="$5"

  # Extract VM template properties individually
  os_name=$(yq -r '.spec.properties.osName // ""' "$template_file")
  os_code=$(yq -r '.spec.properties.osCode // ""' "$template_file")
  cpu_type=$(yq -r '.spec.properties.cpuType // ""' "$template_file")
  cpu_cores=$(yq -r '.spec.properties.cpuCores // 0' "$template_file")
  ram_mb=$(yq -r '.spec.properties.ramMb // 0' "$template_file")
  billing=$(yq -r '.spec.properties.billing // ""' "$template_file")
  unit_cost=$(yq -r '.spec.properties.unitCost // 0.0' "$template_file")

  # Extract disk sizes as comma-separated values
  local disks
  if ! disks=$(get_ws_vm_disklist "$template_file"); then
    log ERROR "[X] Failed to retrieve disk list from $template_file"
    return 1
  fi
  
  cat << EOF
"$name" = {
  provider   = "$provider"
  role       = "$role"
  count      = $count
  os_name    = "$os_name"
  os_code    = "$os_code"
  cpu_type   = "$cpu_type"
  cpu_cores  = $cpu_cores
  ram_mb     = $ram_mb
  disks_gb   = [${disks}]
  billing    = "$billing"
  unit_cost  = $unit_cost
},
EOF
}

export_virtualmachines() {
  echo "virtualmachines = {" >> "$OUTPUT_FILE"
  for entry in "${vm_resources[@]}"; do
    printf '%s\n' "$entry" | sed 's/^/  /' >> "$OUTPUT_FILE"
  done
  echo "}" >> "$OUTPUT_FILE"
  echo "" >> "$OUTPUT_FILE"
}

# --- Main loop ---------------------------------------------------------------
resource_count=$(get_ws_query '.spec.resources | length')
for ((i=0; i<resource_count; i++)); do
  set_ws_resx_from_index "$i"
  check_ws_resx_loaded || exit 1

  name=$(get_ws_resx_field ".name")
  role=$(get_ws_resx_field ".properties.role")
  provider=$(get_ws_resx_field ".properties.provider")
  template=$(get_ws_resx_field ".properties.template")
  count=$(get_ws_resx_field ".properties.count")

  template_path=$(get_ws_template_filename "$template")
  template_file="$SCRIPT_DIR/../../$template_path"
  if [[ ! -f "$template_file" ]]; then
    log WARN "[!] Template file not found: $name"
    continue
  fi

  kind=$(yq -r '.kind' "$template_file")
  case "$kind" in
    VirtualMachine)
      validate_ws_template_vmresx_file "$template_file" || exit 1
      vm_resources+=("$(build_vm_entry "$name" "$role" "$provider" "$count" "$template_file")")
      ;;
    *)
      echo "[ERROR] Unknown resource kind: '$kind' in template: $template_file" >&2
      return 1
      ;;
  esac
done

# --- Output file generation --------------------------------------------------
{
  echo "# Generated from $WORKSPACE_FILE"
  echo ""
  echo "kamatera_manager_id = \"$manager_id\""
  echo ""
  echo "kamatera_country = \"$kamatera_country\""
  echo "kamatera_region  = \"$kamatera_region\""
  echo ""
} > "$OUTPUT_FILE"

export_virtualmachines

echo "# Generation complete" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# --- Summary -----------------------------------------------------------------
log INFO "[+] Generated terraform workspace: $OUTPUT_FILE"
echo ==========================================
cat "$OUTPUT_FILE"
echo ==========================================
log INFO "[+] Terraform workspace generation complete"