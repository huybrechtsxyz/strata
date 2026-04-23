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

# Solution workspace path conventions
SOLUTION_DIR: str = ".platform"
SOLUTION_FILE: str = "solution.json"
SOLUTION_LOGGING_FILE: str = "logging.yaml"
SOLUTION_CONFIGURATION_FILE: str = "configuration.yaml"
SOLUTION_WORKSPACE_SUFFIX: str = ".code-workspace"
