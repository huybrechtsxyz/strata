#!/bin/bash
# ==============================================================================
# Script Name   : validate_bitwarden.sh
# Description   : Validate if the Bitwarden Secrets Manager token is valid.
# Usage         : ./validate_bitwarden.sh
# Author        : Vincent Huybrechts
# Created       : 2025-10-20
# Last Modified : 2025-10-20
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[*] Validating Bitwarden token ..."

# Check if BITWARDEN_TOKEN is set
if [ -z "$BWS_ACCESS_TOKEN" ]; then
  echo "❌ ERROR: BITWARDEN_TOKEN secret is not set"
  exit 1
fi

# Test Bitwarden Secrets Manager token by attempting to list projects
echo "Testing Bitwarden Secrets Manager token validity..."
bws --version

# Test authentication by trying to list projects (this validates the token)
bws project list > /dev/null 2>&1

if [ $? -eq 0 ]; then
  echo "✅ Bitwarden Secrets Manager token is valid and authentication successful"
else
  echo "❌ ERROR: Bitwarden Secrets Manager token is invalid or authentication failed"
  echo "Please check that:"
  echo "  1. BITWARDEN_TOKEN secret is correctly set in GitHub"
  echo "  2. The token is a valid Bitwarden Secrets Manager access token"
  echo "  3. The token has not expired"
  echo "  4. The token has sufficient permissions to access projects/secrets"
  exit 1
fi

echo "[*] Validating Bitwarden token ... DONE"