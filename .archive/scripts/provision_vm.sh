#!/bin/bash
# ==============================================================================
# Script Name   : provision_vm.sh
# Description   : Provision the remote virtual machine.
# Usage         : ./provision_vm.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-11
# Last Modified : 2025-09-05
# ==============================================================================
# Installs and configures:
#   - yq
#   - Docker
#   - GlusterFS
#   - (Placeholder for future hardening steps)
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
LOG_FILE="/var/log/provision_vm.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

# --- Run apt-get update only once ----------------------------------------------
apt_update_once() {
  if [ ! -f /var/tmp/.apt_updated ]; then
    log INFO "Running apt-get update (first time)"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    touch /var/tmp/.apt_updated
  fi
}

# --- Install yq ---------------------------------------------------------------
install_yq() {
  if command -v yq >/dev/null; then
    log INFO "[*] yq is already installed: $(yq --version)"
    retun 0
  fi
  
  log INFO "[*] yq not found. Installing..."
  local yq_version=$(curl -s https://api.github.com/repos/mikefarah/yq/releases/latest | grep tag_name | cut -d '"' -f 4)
  if [ -z "$yq_version" ]; then
    log "Could not determine yq latest tag; falling back to v4.44.1"
    yq_version="v4.44.1"
  fi
  curl -L "https://github.com/mikefarah/yq/releases/download/${yq_version}/yq_linux_amd64" -o /usr/local/bin/yq
  chmod +x /usr/local/bin/yq
  log INFO "[+] yq installed successfully."
}

# --- Install Docker -----------------------------------------------------------
install_docker() {
  if command -v docker &>/dev/null; then
    log INFO "[*] Docker is already installed: $(docker --version)"
    exit 0
  fi
    
  log INFO "[*] Installing Docker..."
  apt-get install -y ca-certificates curl gnupg lsb-release
  curl -fsSL https://get.docker.com | bash
  log INFO "[+] Docker installed: $(docker --version)"
  log INFO "[+] Docker installed successfully."
}

enable_docker() {
  log INFO "[*] Ensuring Docker is enabled and running..."

  if ! systemctl is-enabled --quiet docker; then
    log INFO "[*] ... Enabling Docker service..."
    systemctl enable docker
  else
    log INFO "[*] ... Docker service is already enabled."
  fi

  if ! systemctl is-active --quiet docker; then
    log INFO "[*] ... Starting Docker service..."
    systemctl start docker
  else
    log INFO "[*] ... Docker service is already running."
  fi

  log INFO "[+] Docker is enabled and running."
}

# --- Install GlusterFS --------------------------------------------------------
install_gluster() {
  if command -v glusterfs --version &>/dev/null; then
    log INFO "[*] GlusterFS is already installed: $(glusterfs --version)"
    exit 0
  fi

  log INFO "[*] Installing GlusterFS..."
  apt-get install -y glusterfs-server
  systemctl enable --now glusterd
  log INFO "[+] GlusterFS installed successfully."
}

# --- Install Bitwarden CLI -----------------------------------------------------
install_bitwarden() {
  # Check if bw (Bitwarden CLI) is installed
  if ! command -v bw &> /dev/null; then
    log INFO "Bitwarden CLI not found. Installing..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
      curl -L https://vault.bitwarden.com/download/?app=cli -o bw.zip
      unzip bw.zip -d bwcli
      sudo mv bwcli/bw /usr/local/bin/
      rm -rf bw.zip bwcli
    elif [[ "$OSTYPE" == "darwin"* ]]; then
      brew install bitwarden-cli
    else
      log ERROR "Please install Bitwarden CLI manually for your OS."
      exit 1
    fi
  else
    log INFO "Bitwarden CLI is already installed."
  fi

  # Bitwarden Secret Manager (bws) install logic
  if ! command -v bws &> /dev/null; then
    log INFO "Bitwarden Secret Manager CLI not found. Installing..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
      curl -L https://vault.bitwarden.com/download/?app=secret-manager-cli -o bws.zip
      unzip bws.zip -d bwscli
      sudo mv bwscli/bws /usr/local/bin/
      rm -rf bws.zip bwscli
    elif [[ "$OSTYPE" == "darwin"* ]]; then
      brew install bitwarden-secret-manager-cli
    else
      log ERROR "Please install Bitwarden Secret Manager CLI manually for your OS."
      exit 1
    fi
  else
    log INFO "Bitwarden Secret Manager CLI is already installed."
  fi
}

# --- Install Python ------------------------------------------------------------
install_python() {
  if command -v python3 &>/dev/null; then
    log INFO "[*] Python is already installed: $(python3 --version)"
    return 0
  fi

  log INFO "[*] Installing Python..."
  apt-get install -y python3 python3-pip python3-venv
  log INFO "[+] Python installed: $(python3 --version)"
  log INFO "[+] Python and pip installed successfully."
}

# --- System Hardening ---------------------------------------------------------
hardening() {
  # Placeholder for any future hardening steps
  true
}

# --- Main ---------------------------------------------------------------------
main() {
  log INFO "[*] Starting provisioning for remote VM..."

  apt_update_once
  install_yq
  install_docker
  enable_docker
  install_gluster
  install_bitwarden
  install_python

  hardening

  log INFO "[+] Provisioning for remote VM completed successfully."
}

main "$@"