#!/usr/bin/env python3
"""Fixed configuration constants for the strata package."""

# List of valid script file extensions
SCRIPT_EXTENSIONS = {".sh", ".bash", ".py", ".ps1", ".js", ".mjs", ".go"}

# Default path constants (empty string = use hardcoded fallback in callers)
DEFAULT_BUILD_PATH: str = "build"
DEFAULT_DIST_PATH: str = ""
DEFAULT_OBJECT_PATH: str = ""
DEFAULT_STATE_DIR: str = ""
DEFAULT_STATE_FILE: str = ""

# Public URLs
DOCS_URL: str = "https://huybrechtsxyz.github.io/strata"
SUPPORT_URL: str = "https://github.com/huybrechtsxyz/strata/issues"

# Solution workspace path conventions
SOLUTION_DIR: str = ".strata"
SOLUTION_FILE: str = "solution.json"
SOLUTION_LOGGING_FILE: str = "logging.yaml"
SOLUTION_CONFIG_FILE: str = "cli.yaml"
SOLUTION_GITIGNORE_FILE: str = ".gitignore"
SOLUTION_CONFIGURATION_FILE: str = "configuration.yaml"
SOLUTION_WORKSPACE_SUFFIX: str = ".code-workspace"
SOLUTION_AUDIT_LOG_FILE: str = "audit.log"
SOLUTION_COLLECTORS_FILE: str = "collectors.yaml"
SOLUTION_SBOM_IGNORE_FILE: str = "sbom-ignore.yaml"
SOLUTION_CVE_ALLOWED_FILE: str = "cve-allowed.yaml"
SOLUTION_GUIDE_FILE: str = "guide.yaml"

# .strata/ subdirectory names (relative to SOLUTION_DIR)
SOLUTION_LOGS_DIR: str = "logs"
SOLUTION_INTEGRATIONS_DIR: str = "integrations"
SOLUTION_POLICIES_DIR: str = "policies"
SOLUTION_TEMPLATES_DIR: str = "templates"
SOLUTION_PLUGINS_DIR: str = "plugins"
SOLUTION_LOCKFILE_PARSERS_DIR: str = "lockfile_parsers"
SOLUTION_SCHEMAS_DIR: str = "schemas"
SOLUTION_LOCKS_DIR: str = "locks"
SOLUTION_DEPLOYMENTS_DIR: str = "deployments"
SOLUTION_OUTPUTS_DIR: str = "outputs"
SOLUTION_BUILD_DIR: str = "build"
SOLUTION_DEPLOY_LOG_DIR: str = "deploy-log"
