#!/bin/bash
#===============================================================================
# Script Name   : common_start.sh
# Description   : 
# Usage         : ./common_start.sh <RESOURCE_NAME>
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-08
#===============================================================================

: ${WORKSPACE_NAME:?"WORKSPACE_NAME is not set. Please set it to the workspace name."}
: ${WORKSPACE_FILE:?"WORKSPACE_FILE is not set. Please set it to the workspace file."}
: ${TERRAFORM_FILE:?"TERRAFORM_FILE is not set. Please set it to the terraform file."}
: ${RESOURCE_HOST:?"RESOURCE_HOST is not set. Please set it to the server name."}
: ${RESOURCE_NAME:?"RESOURCE_NAME is not set. Please set it to the resource name."}

# Load script utilities
HOSTNAME=$(hostname)
source "$SCRIPT_DIR/utilities.sh"
validate_script "$SCRIPT_DIR/utilities.sh"
log INFO "[*] Loaded $SCRIPT_DIR/utilities.sh"

# Load terraform data
log INFO "[*] Loading Terraform data from $TERRAFORM_FILE"
set_tf_file "$TERRAFORM_FILE"
set_tf_server_by_name "$RESOURCE_HOST"

# Get workspace data
log INFO "[*] Loading workspace: $WORKSPACE_NAME in $WORKSPACE_FILE"
set_ws_file "$WORKSPACE_FILE"
set_ws_data "$WORKSPACE_NAME"
check_ws_loaded || exit 1
set_ws_resx_from_name "$RESOURCE_NAME"

# Set environment variables
log INFO "[*] ... Getting resource ip-addresses"
RESOURCE_PUBLICIP=$(get_tf_vm_publicip)
RESOURCE_MANAGERIP=$(get_tf_vm_managerip)
RESOURCE_PRIVATEIP=$(get_tf_vm_privateip)

: ${RESOURCE_PUBLICIP:?"RESOURCE_PUBLICIP is not set. Please set it to the server public ip."}
: ${RESOURCE_MANAGERIP:?"RESOURCE_MANAGERIP is not set. Please set it to the server manager ip."}
: ${RESOURCE_PRIVATEIP:?"RESOURCE_PRIVATEIP is not set. Please set it to the server private ip."}

log INFO "[*] ... Getting resource installpoint"
RESOURCE_INSTALLPOINT=$(get_ws_resx_installpoint)
: ${RESOURCE_INSTALLPOINT:?"RESOURCE_INSTALLPOINT is not set. Please set it to the resource install point."}
log INFO "[*] ... Setting installpoint to: $RESOURCE_INSTALLPOINT"

log INFO "[*] ... Getting primary machine name and filter"
RESOURCE_PRIMARY_NAME=$(get_workspace_primary_name)
RESOURCE_PRIMARY_FILTER=$(get_workspace_primary_filter)

: ${RESOURCE_PRIMARY_NAME:?"RESOURCE_PRIMARY is not set. Please set it to the primary resource for the workspace."}
: ${RESOURCE_PRIMARY_FILTER:?"RESOURCE_PRIMARY_FILTER is not set. Please set it to the primary resource role."}