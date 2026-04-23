#!/usr/bin/env python3
"""Fixed configuration constants for the xyz-platform package."""

# List of valid script file extensions
SCRIPT_EXTENSIONS = {".sh", ".bash", ".py", ".ps1"}

# Default path constants (empty string = use hardcoded fallback in callers)
DEFAULT_BUILD_PATH: str = ""
DEFAULT_DIST_PATH: str = ""
DEFAULT_OBJECT_PATH: str = ""
DEFAULT_STATE_DIR: str = ""
DEFAULT_STATE_FILE: str = ""
