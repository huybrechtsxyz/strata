#!/bin/bash
# ==============================================================================
# Script Name   : boostrap_modules_run.sh
# Description   : Check out the linked workspace modules.
# Usage         : ./boostrap_modules_run.sh
# Author        : Vincent Huybrechts
# Created       : 2025-08-11
# Last Modified : 2025-08-11
# ==============================================================================
set -euo pipefail
trap 'echo "ERROR: Script failed at line $LINENO: \"$BASH_COMMAND\""' ERR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Load common start --------------------------------------------------------
source "$SCRIPT_DIR/common_start.sh"
validate_script "$SCRIPT_DIR/common_start.sh"

log INFO "[*] Bootstrapping modules for workspace $WORKSPACE_NAME from $WORKSPACE_FILE ..."

# --- Parse workspace modules --------------------------------------------------
module_count=$(get_ws_query '.spec.modules | length')
for ((i=0; i<module_count; i++)); do
  ws_mod_name=$(get_ws_query ".spec.modules[$i].name")
  ws_mod_file=$(get_ws_query ".spec.modules[$i].file")

  log INFO "[*] Cloning module $ws_mod_name from $ws_mod_file"
  if [[ ! -f "$ws_mod_file" ]]; then
    log ERROR "[X] Module file not found: $ws_mod_file for module $ws_mod_name"
    continue
  fi

  log INFO "[*] ... Reading module overrides"
  exp_repo=$(get_ws_query ".spec.modules[$i].overrides.repository" true)
  exp_ref=$(get_ws_query ".spec.modules[$i].overrides.reference" true)
  exp_path=$(get_ws_query ".spec.modules[$i].overrides.deployPath" true)

  log INFO "[*] ... Reading module file: $ws_mod_file"
  mod_repo=$(yq -r '.spec.repository' "$ws_mod_file")
  mod_ref=$(yq -r '.spec.reference' "$ws_mod_file")
  mod_path=$(yq -r '.spec.deployPath' "$ws_mod_file")

  # Apply overrides if they are non-empty
  [ -n "$exp_repo" ] && mod_repo="$exp_repo"
  [ -n "$exp_ref" ] && mod_ref="$exp_ref"
  [ -n "$exp_path" ] && mod_path="$exp_path"

  if [[ -z "$mod_repo" || "$mod_repo" == "null" ]]; then
    log ERROR "[X] No repository defined in $ws_mod_file"
    continue
  fi
  if [[ -z "$mod_path" || "$mod_path" == "null" ]]; then
    log ERROR "[X] No deployPath defined in $ws_mod_file"
    continue
  fi

  # Destination directory
  dest_dir=$(realpath "$SCRIPT_DIR/../../modules/${ws_mod_name}")
  rm -rf "$dest_dir"
  mkdir -p "$dest_dir"

  log INFO "[*] ... Cloning/copying $mod_repo ($mod_ref) into $dest_dir"

  case "$mod_repo" in
    # Its a local file
    file://*)
      src_path="${mod_repo#file://}"
      log INFO "[*] ... Copying deployPath from local: $src_path/$mod_path"
      cp -r "$src_path/$mod_path"/* "$dest_dir/"
      ;;
    # Remote through http/https
    http://*|https://*)
      if [[ "$mod_repo" =~ \.git$ ]]; then
        log INFO "[*] ... Cloning from git: $mod_repo ($mod_ref)"
        git clone --branch "$mod_ref" --depth 1 "$mod_repo" "$dest_dir.tmp"
        cp -r "$dest_dir.tmp/$mod_path"/* "$dest_dir/"
        rm -rf "$dest_dir.tmp"
      else
        log INFO "[*] Downloading from: $mod_repo"
        tmpfile=$(mktemp)
        curl -fsSL "$mod_repo" -o "$tmpfile"
        mkdir -p "$dest_dir.tmp"
        tar -xf "$tmpfile" -C "$dest_dir.tmp" 2>/dev/null || unzip -q "$tmpfile" -d "$dest_dir.tmp"
        cp -r "$dest_dir.tmp/$mod_path"/* "$dest_dir/"
        rm -rf "$dest_dir.tmp" "$tmpfile"
      fi
      ;;
    # Github
    *)
      log INFO "[*] ... Cloning from GitHub repo: $mod_repo ($mod_ref)"
      git clone --branch "$mod_ref" --depth 1 "https://github.com/${mod_repo}.git" "$dest_dir.tmp"
      cp -r "$dest_dir.tmp/$mod_path"/* "$dest_dir/"
      rm -rf "$dest_dir.tmp"
      ;;
  esac

  log INFO "[+] ... Finished $ws_mod_name — deployPath copied to $dest_dir"
done

log INFO "[+] Bootstrapping modules for workspace from $WORKSPACE_FILE ... DONE"