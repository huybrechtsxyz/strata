#!/bin/bash
# ==============================================================================
# Script Name   : configure_vm_run.sh
# Description   : Configure the remote VM.
# Usage         : ./configure_vm_run.sh
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
log INFO "[*] Configuration of REMOTE server..."

if ! ssh -o StrictHostKeyChecking=no root@"$RESOURCE_PUBLICIP" << EOF
  export WORKSPACE_NAME="$WORKSPACE_NAME"
  export WORKSPACE_FILE="$WORKSPACE_FILE"
  export TERRAFORM_FILE="$TERRAFORM_FILE"
  export RESOURCE_NAME="$RESOURCE_NAME"
  export BITWARDEN_TOKEN="$BITWARDEN_TOKEN"
  chmod +x "$RESOURCE_INSTALLPOINT/configure_vm_remote.sh"
  "$RESOURCE_INSTALLPOINT/configure_vm_remote.sh"
EOF
then
  log ERROR "[X] Remote initialization failed on $RESOURCE_PUBLICIP"
  exit 1
fi
log INFO "[*] Configuration of REMOTE server...DONE"
}

# --- Main function ------------------------------------------------------------
main() {
  log INFO "[*] Configuration of virtual machine resource '$RESOURCE_NAME'..."
  execute_remote
  log INFO "[+] Configuration of virtual machine resource '$RESOURCE_NAME'... DONE"
}

main