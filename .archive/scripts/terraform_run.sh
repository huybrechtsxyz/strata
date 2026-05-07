#!/bin/bash
# ==============================================================================
# Script Name   : terraform_run.sh
# Description   : Apply Terraform configurations using the specified workspace file.
# Usage         : ./terraform_run.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-11
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Prerequisites -----------------------------------------------------------
if ! command -v yq &> /dev/null; then
  echo "[X] ERROR: 'yq' is required but not installed."
  exit 1
fi

# --- Validate required environment variables ---------------------------------
: "${WORKSPACE_NAME:?WORKSPACE_NAME is not set. Please set it to the workspace name.}"
: "${WORKSPACE_FILE:?WORKSPACE_FILE is not set. Please set it to the workspace file.}"
: "${BITWARDEN_TOKEN:?BITWARDEN_TOKEN is not set. Please set it to the Bitwarden API token.}"
: "${PATH_TEMP:?PATH_TEMP is not set. Please set it to the temporary path.}"

# --- Load utilities ----------------------------------------------------------
source "$SCRIPT_DIR/utilities.sh"
validate_script "$SCRIPT_DIR/utilities.sh"

# --- Workspace data ----------------------------------------------------------
log INFO "[*] Loading workspace '$WORKSPACE_NAME' from '$WORKSPACE_FILE'"
WORKSPACE_FILE="$SCRIPT_DIR/../../$WORKSPACE_FILE"
set_ws_file "$WORKSPACE_FILE"
set_ws_data "$WORKSPACE_NAME"

# --- Generate workspace variables ---------------------------------------------
TFVARS_FILE="./workspace.tfvars"
log INFO "[*] Generating workspace variables in '$TFVARS_FILE'"
chmod +x "$SCRIPT_DIR/terraform_ws.sh"
"$SCRIPT_DIR/terraform_ws.sh" "$WORKSPACE_NAME" "$WORKSPACE_FILE" "$TFVARS_FILE"
log INFO "[*] Generation complete"

# --- Get environment variables ---------------------------------------------
log INFO "[*] Exporting secrets from workspace"
export_secrets "$WORKSPACE_FILE" ".spec.secrets" "TF_VAR_" "" "lower"
TF_VAR_organization="$(get_ws_property_tf_organization)"
TF_VAR_cloudspace="$(get_ws_property_tf_cloudspace)"

log INFO "[*] Setting additional Terraform environment variables"
export TF_TOKEN_app_terraform_io=$TF_VAR_terraform_api_token
export TF_VAR_organization="$TF_VAR_organization"
export TF_VAR_cloudspace="$TF_VAR_cloudspace"

# --- Generate main.tf from template -------------------------------------------
log INFO "[*] Generating main.tf from template"
envsubst < main.template.tf > main.tf
rm -f main.template.tf
cat main.tf
log INFO "[*] main.tf generation complete"

# --- Terraform execution ------------------------------------------------------
# Note: Saving a generated plan is currently not supported in Terraform Cloud
log INFO "[*] Running terraform init"
mkdir -p "$PATH_TEMP"
terraform init

log INFO "[*] Running terraform plan"
terraform plan -var-file="workspace.tfvars" -input=false

log INFO "[*] Running terraform apply"
TERRAFORM_APPLY=${TERRAFORM_APPLY:-false}
if [[ "$TERRAFORM_APPLY" == "true" ]]; then
  log INFO "[*] Running terraform apply"
  terraform apply -auto-approve -var-file="workspace.tfvars" -input=false
  log INFO "[*] Terraform apply completed"
else
  log INFO "[*] Terraform apply skipped (RUN_TERRAFORM_APPLY != true)"
fi

# --- Terraform output ---------------------------------------------------------
log INFO "[*] Reading Terraform output..."
terraform output -json terraform_output | jq -c '.' | tee "$PATH_TEMP/tfoutput.json"

log INFO "[+] Terraform output saved to tf_output.json and $PATH_TEMP/tfoutput.json" 