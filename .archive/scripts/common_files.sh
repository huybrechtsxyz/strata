#!/bin/bash
#===============================================================================
# Script Name   : common_files.sh
# Description   : 
# Usage         : source common_files.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-11
#===============================================================================

VARIABLE_FILE="$SCRIPT_DIR/$WORKSPACE_NAME.ws.env"
SECRET_FILE="$SCRIPT_DIR/$WORKSPACE_NAME.ws.secrets"

# Create a temporary directory for the initialization scripts
create_environment_files() {
  log INFO "[*] Creating environment files for workspace '$WORKSPACE_NAME'..."

  log INFO "[*] ... Exporting fixed environment variables for Terraform"
  export_variables "$WORKSPACE_FILE" ".spec.variables" "VAR_" "" ""
  generate_env_file "VAR_" "$VARIABLE_FILE" 

  log INFO "[*] ... Exporting secrets from $WORKSPACE_FILE"
  export_secrets "$WORKSPACE_FILE" ".spec.secrets" "SECRET_" "" ""
  generate_env_file "SECRET_" "$SECRET_FILE"

  log INFO "[*] Creating environment files for workspace '$WORKSPACE_NAME'...DONE"
}

# 
copy_workspace_files() {
log INFO "[*] Copying workspace files to remote server $RESOURCE_PUBLICIP..."

ssh -o StrictHostKeyChecking=no root@$RESOURCE_PUBLICIP << EOF
echo "[*] Creating installation paths..."
mkdir -p "$RESOURCE_INSTALLPOINT" \
  "$RESOURCE_INSTALLPOINT/modules" \
  "$RESOURCE_INSTALLPOINT/templates" \
  "$RESOURCE_INSTALLPOINT/workspaces" \
chmod 777  "$RESOURCE_INSTALLPOINT" \
  "$RESOURCE_INSTALLPOINT/modules" \
  "$RESOURCE_INSTALLPOINT/templates" \
  "$RESOURCE_INSTALLPOINT/workspaces" \
echo "[*] Listing installation paths..."
ls -lRa "$RESOURCE_INSTALLPOINT"
EOF

log INFO "[*] Copying workspace scripts and configuration files to remote server..."
scp -o StrictHostKeyChecking=no \
  ./deploy/scripts/* \
  root@"$RESOURCE_PUBLICIP":"$RESOURCE_INSTALLPOINT"/ || {
    log ERROR "[X] Failed to transfer workspace scripts to remote server"
    exit 1
  }

log INFO "[*] Copying module files to remote server..."
scp -r -o StrictHostKeyChecking=no \
  "modules"/ \
  root@"$RESOURCE_PUBLICIP":"$RESOURCE_INSTALLPOINT"/ || {
    log ERROR "[X] Failed to transfer module files to remote server"
    exit 1
  }

log INFO "[*] Copying template files to remote server..."
scp -r -o StrictHostKeyChecking=no \
  "templates"/ \
  root@"$RESOURCE_PUBLICIP":"$RESOURCE_INSTALLPOINT"/ || {
    log ERROR "[X] Failed to transfer template files to remote server"
    exit 1
  }

log INFO "[*] Copying workspace files to remote server..."
scp -r -o StrictHostKeyChecking=no \
  "workspaces"/ \
  root@"$RESOURCE_PUBLICIP":"$RESOURCE_INSTALLPOINT"/ || {
    log ERROR "[X] Failed to transfer workspace files to remote server"
    exit 1
  }

log INFO "[*] Debugging installation path of remote server..."
ssh -o StrictHostKeyChecking=no root@$RESOURCE_PUBLICIP << EOF
  ls -Rla "$RESOURCE_INSTALLPOINT"
EOF

log INFO "[*] Copying workspace files to remote server $RESOURCE_PUBLICIP...DONE"
}

cleanup_workspace_secrets() {
log INFO "[*] Removing .secrets files from remote server..."
ssh -o StrictHostKeyChecking=no root@"$RESOURCE_PUBLICIP" 'bash -seuo pipefail' << EOF
base_path="$RESOURCE_INSTALLPOINT"

# Safety checks
if [[ -z "\$base_path" || "\$base_path" == "/" ]]; then
  echo "[WARN] Skipped unsafe or empty path: '\$base_path'"
  exit 1
fi

real_base=\$(realpath -m "\$base_path")
if [[ "\$real_base" == "/" ]]; then
  echo "[ERROR] Refusing to operate on root directory"
  exit 1
fi

if [[ -d "\$real_base" ]]; then
  echo "[INFO] Searching for .secrets files in: \$real_base"
  find "\$real_base" -type f -name '*.secrets' -exec rm -f {} +
else
  echo "[WARN] Skipped non-existent path: \$real_base"
fi
EOF

}