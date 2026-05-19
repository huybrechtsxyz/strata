#!/usr/bin/env python3
"""Fixed configuration constants for the strata package."""

# List of valid script file extensions
SCRIPT_EXTENSIONS = {".sh", ".bash", ".py", ".ps1"}

# Default path constants (empty string = use hardcoded fallback in callers)
DEFAULT_BUILD_PATH: str = ""
DEFAULT_DIST_PATH: str = ""
DEFAULT_OBJECT_PATH: str = ""
DEFAULT_STATE_DIR: str = ""
DEFAULT_STATE_FILE: str = ""

# Public URLs — update when the help-site is live
DOCS_URL: str = "https://docs.xyzplatform.com"
SUPPORT_URL: str = "https://support.xyzplatform.com"

# Solution workspace path conventions
SOLUTION_DIR: str = ".strata"
SOLUTION_FILE: str = "solution.json"
SOLUTION_LOGGING_FILE: str = "logging.yaml"
SOLUTION_CONFIG_FILE: str = "cli.yaml"
SOLUTION_GITIGNORE_FILE: str = ".gitignore"
SOLUTION_CONFIGURATION_FILE: str = "configuration.yaml"
SOLUTION_WORKSPACE_SUFFIX: str = ".code-workspace"
