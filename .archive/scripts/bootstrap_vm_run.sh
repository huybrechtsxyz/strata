#!/bin/bash
# ==============================================================================
# Script Name   : bootstrap_vm_run.sh
# Description   : Bootstrap workspace scripts and configuration to a remote VM.
# Usage         : ./bootstrap_vm_run.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-11
# Last Modified : 2025-08-11
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load common start and common files
source "$SCRIPT_DIR/common_start.sh"
validate_script "$SCRIPT_DIR/common_start.sh"

source "$SCRIPT_DIR/common_files.sh"
validate_script "$SCRIPT_DIR/common_files.sh"

# Validate required environment variables
: "${BITWARDEN_TOKEN:?BITWARDEN_TOKEN is not set. Please set it to the Bitwarden API token.}"

# Create environment files
create_files() {
  create_environment_files
}

# Copy workspace files to remote VM
copy_files() {
  copy_workspace_files
}

# Main function
main() {
  log INFO "[*] Bootstrap of workspace resources for virtual machine '$RESOURCE_NAME'..."

  if ! create_files; then
    log ERROR "[X] Failed to create environment files."
    exit 1
  fi

  if ! copy_files; then
    log ERROR "[X] Failed to copy installation files to remote server."
    exit 1
  fi

  if ! cleanup_workspace_secrets; then
    log ERROR "[X] Remote installation cleanup failed."
    exit 1
  fi

  log INFO "[+] Bootstrap of workspace resources for virtual machine '$RESOURCE_NAME'... DONE"
}

main