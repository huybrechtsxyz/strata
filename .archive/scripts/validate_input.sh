#!/usr/bin/env python3
# ==============================================================================
# Script Name   : validate_input.py
# Description   : Validate input parameters for workspace creation.
# Author        : Vincent Huybrechts
# Created       : 2025-09-08
# Modified      : 2025-09-08
# ==============================================================================

import os
import sys
import traceback

CONFIG_DIR = "./config"
SEARCH_DIR = "."
EXIT_SUCCESS = 0
EXIT_FAILURE = 1

def log(level, msg):
  print(f"{level} {msg}")

def require_env(var):
  val = os.environ.get(var, "").strip()
  if not val:
    log("ERROR", f"[X] '{var.lower()}' input is required")
    sys.exit(EXIT_FAILURE)
  return val

def load_yaml_documents(path):
  import yaml
  with open(path, "r", encoding="utf-8") as f:
    try:
      return list(yaml.safe_load_all(f))
    except yaml.YAMLError as e:
      log("ERROR", f"[X] Failed parsing YAML file '{path}': {e}")
      sys.exit(EXIT_FAILURE)

def collect_file_references(node, acc):
  if isinstance(node, dict):
    if "file" in node:
      value = node["file"]
      if isinstance(value, str):
        acc.append(value)
      elif isinstance(value, list):
        for v in value:
          if isinstance(v, str):
            acc.append(v)
    for v in node.values():
      collect_file_references(v, acc)
  elif isinstance(node, list):
    for item in node:
      collect_file_references(item, acc)

def main():
  # Check PyYAML availability (replacement for yq need)
  try:
    import yaml  # noqa: F401
  except ImportError:
    log("ERROR", "[X] 'PyYAML' is required. Install with: pip install pyyaml")
    return EXIT_FAILURE

  log("INFO", "[*] Validating workspace parameters ...")
  workspace_name = require_env("WORKSPACE_NAME")
  workspace_file = require_env("WORKSPACE_FILE")

  if not os.path.isfile(workspace_file):
    log("ERROR", f"[X] 'workspace_file' must point to an existing file: {workspace_file}")
    return EXIT_FAILURE

  docs = load_yaml_documents(workspace_file)
  if not docs:
    log("ERROR", f"[X] Workspace file '{workspace_file}' is empty")
    return EXIT_FAILURE
  meta_name = None
  # Try to find meta.name in first doc that has it
  for d in docs:
    if isinstance(d, dict):
      meta = d.get("meta")
      if isinstance(meta, dict) and "name" in meta:
        meta_name = meta["name"]
        break
  if meta_name != workspace_name:
    log("ERROR", f"[X] Workspace '{workspace_name}' does not match meta.name '{meta_name}' in file '{workspace_file}'")
    return EXIT_FAILURE
  log("INFO", "[*] Workspace parameters validation completed successfully")

  log("INFO", "[*] Validating file references in workspace files ...")
  if not os.path.isdir(CONFIG_DIR):
    log("INFO", f"[*] Config directory '{CONFIG_DIR}' not found; skipping file reference validation")
    return EXIT_SUCCESS

  missing = 0
  for root, _, files in os.walk(CONFIG_DIR):
    for fname in files:
      if not fname.lower().endswith((".yaml", ".yml")):
        continue
      yaml_path = os.path.join(root, fname)
      log("INFO", f"[*] ... Processing {yaml_path}...")
      refs = []
      docs = load_yaml_documents(yaml_path)
      for d in docs:
        collect_file_references(d, refs)
      if not refs:
        log("INFO", f"[*] ...... No file references found in {yaml_path}")
        continue
      for ref in refs:
        full_path = os.path.join(SEARCH_DIR, ref)
        if os.path.exists(full_path):
          log("INFO", f"[*] ..... Exists: {full_path}")
        else:
          log("WARN", f"[!] ...... Missing: {full_path}")
          missing += 1

  if missing:
    log("ERROR", f"[X] Found {missing} missing file references in workspace files")
    return EXIT_FAILURE

  log("INFO", "[*] File references validation completed successfully")
  log("INFO", "[*] All validations completed successfully")
  return EXIT_SUCCESS

if __name__ == "__main__":
  try:
    code = main()
    sys.exit(code)
  except Exception:
    log("ERROR", f"[X] Unhandled exception:\n{traceback.format_exc()}")
    sys.exit(EXIT_FAILURE)