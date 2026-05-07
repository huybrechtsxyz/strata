#!/bin/bash
# ==============================================================================
# Script Name   : bootstrap_vm.sh
# Description   : Bootstrap the remote virtual machine.
# Usage         : ./bootstrap_vm.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-11
# Last Modified : 2025-09-05
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Logging function ---------------------------------------------------------
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

# --- Log everything to file and stdout ----------------------------------------
LOG_FILE="/var/log/bootstrap_vm.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

# --- Main ---------------------------------------------------------------------
main() {
  log INFO "[*] Starting bootstrapping for remote VM..."

  # This is a placeholder for the actual bootstrapping logic.
  # No real operations are performed here.

  log INFO "[+] Bootstrapping remote VM completed successfully."
}

main "$@"