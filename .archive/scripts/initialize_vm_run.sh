#!/bin/bash
# ==============================================================================
# Script Name   : initialize_vm_run.sh
# Description   : Initialize the remote VM.
# Usage         : ./initialize_vm_run.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-11
# Last Modified : 2025-08-11
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: `$BASH_COMMAND`"' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Load common start --------------------------------------------------------
source "$SCRIPT_DIR/common_start.sh"
validate_script "$SCRIPT_DIR/common_start.sh"

# --- Execute initialization procedure remotely ------------------------------------
execute_remote() {
log INFO "[*] Initializing REMOTE server ..."

if ! ssh -o StrictHostKeyChecking=no root@"$RESOURCE_PUBLICIP" << EOF
  export WORKSPACE_NAME="$WORKSPACE_NAME"
  export WORKSPACE_FILE="$RESOURCE_INSTALLPOINT/$WORKSPACE_FILE"
  export TERRAFORM_FILE="$RESOURCE_INSTALLPOINT/$TERRAFORM_FILE"
  export RESOURCE_HOST="$RESOURCE_HOST"
  export RESOURCE_NAME="$RESOURCE_NAME"
  export BITWARDEN_TOKEN="$BITWARDEN_TOKEN"
  chmod +x "$RESOURCE_INSTALLPOINT/initialize_vm_remote.sh"
  "$RESOURCE_INSTALLPOINT/initialize_vm_remote.sh"
EOF
then
  log ERROR "[X] Remote initialization failed on $RESOURCE_PUBLICIP"
  exit 1
fi
log INFO "[*] Initializing REMOTE server ...DONE"
}

# --- Main function ------------------------------------------------------------
main() {
  log INFO "[*] Initializing of virtual machine resource '$RESOURCE_NAME'..."
  execute_remote
  log INFO "[+] Initializing of virtual machine resource '$RESOURCE_NAME'... DONE"
}

main