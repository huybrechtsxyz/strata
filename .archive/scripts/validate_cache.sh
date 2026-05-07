#!/bin/bash
# ==============================================================================
# Script Name   : validate_cache.sh
# Description   : Validate if the contents of a directory have changed
# Usage         : ./validate_cache.sh <directory_to_check> <hash_json_file>
# Author        : Vincent Huybrechts
# Created       : 2025-10-20
# Last Modified : 2025-10-20
# ==============================================================================
# Returns 0 if no change, 1 if change detected. Prints path to hashfile if change.
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Parameters ----------------------------------------------------------------
dir_to_check="$1"
json_file="$2"

# --- Function to generate hash JSON -------------------------------------------
generate_hash_json() {
  tmpfile="$1"
  echo '{ "files": [' > "$tmpfile"
  first=1
  find "$dir_to_check" -type f -printf "%P|%T@\n" | sort | while IFS='|' read fname mtime; do
    hash=$(sha256sum "$dir_to_check/$fname" | awk '{print $1}')
    if [ $first -eq 0 ]; then echo ',' >> "$tmpfile"; fi
    echo "  {\"name\": \"${fname}\", \"sha256\": \"${hash}\", \"mtime\": ${mtime}}" >> "$tmpfile"
    first=0
  done
  echo ']}' >> "$tmpfile"
}

# --- Main Logic -----------------------------------------------------------------
tmp_hash_json="$(mktemp)"

if [ ! -f "$json_file" ]; then
  generate_hash_json "$json_file"
  echo "$json_file"
  exit 1
else
  jq -r '.files[] | "\(.name)|\(.mtime)"' "$json_file" | sort > /tmp/json_files.txt
  find "$dir_to_check" -type f -printf "%P|%T@\n" | sort > /tmp/dir_files.txt

  if diff -q /tmp/dir_files.txt /tmp/json_files.txt; then
    generate_hash_json "$tmp_hash_json"
    mv "$tmp_hash_json" "$json_file"
    echo "$json_file"
    exit 1
  else
    rm -f "$tmp_hash_json"
    exit 0
  fi
fi