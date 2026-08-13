#!/usr/bin/env python3
"""Fixed configuration constants and workspace path builders for the strata package.

Path builder functions (``get_*``) return the **canonical fallback path** for
each strata workspace location.  Higher-level components (``ConfigurationService``,
``SolutionController``) may apply user-configured overrides on top — but the
arithmetic ``work_path / SOLUTION_DIR / SOLUTION_X`` lives here and nowhere else.
"""

from pathlib import Path

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
SOLUTION_DIAGRAMS_DIR: str = "diagrams"
SOLUTION_LOCKS_DIR: str = "locks"
SOLUTION_DEPLOYMENTS_DIR: str = "deployments"
SOLUTION_OUTPUTS_DIR: str = "outputs"
SOLUTION_BUILD_DIR: str = "build"
SOLUTION_DEPLOY_LOG_DIR: str = "deploy-log"
SOLUTION_DRIFT_DIR: str = "drift"
SOLUTION_DRIFT_RULES_FILE: str = "drift_rules.yaml"
SOLUTION_COST_DIR: str = "cost"
SOLUTION_COST_CACHE_DIR: str = "cache/cost"

# AI agent integration (ADR-0025)
SOLUTION_AI_CACHE_DIR: str = "cache/ai"
SOLUTION_PROMPTS_DIR: str = "prompts"

# Resolved-model cache (ADR-0026)
SOLUTION_MODEL_CACHE_DIR: str = "cache/model"
SOLUTION_MODEL_CACHE_DB_FILE: str = "cache.db"


# ---------------------------------------------------------------------------
# Workspace path builders — canonical fallback; one source of truth.
# Call these instead of repeating work_path / SOLUTION_DIR / SOLUTION_X.
# ---------------------------------------------------------------------------


def get_strata_dir(work_path: Path) -> Path:
    """Return the ``.strata/`` state directory."""
    return work_path / SOLUTION_DIR


def get_solution_json_path(work_path: Path) -> Path:
    """Return the path to ``solution.json``."""
    return work_path / SOLUTION_DIR / SOLUTION_FILE


def get_logging_config_path(work_path: Path) -> Path:
    """Return the path to ``logging.yaml``."""
    return work_path / SOLUTION_DIR / SOLUTION_LOGGING_FILE


def get_cli_config_path(work_path: Path) -> Path:
    """Return the path to ``cli.yaml`` (workspace CLI defaults)."""
    return work_path / SOLUTION_DIR / SOLUTION_CONFIG_FILE


def get_configuration_path(work_path: Path) -> Path:
    """Return the path to ``configuration.yaml``."""
    return work_path / SOLUTION_DIR / SOLUTION_CONFIGURATION_FILE


def get_audit_log_path(work_path: Path) -> Path:
    """Return the path to ``audit.log``."""
    return work_path / SOLUTION_DIR / SOLUTION_AUDIT_LOG_FILE


def get_collectors_path(work_path: Path) -> Path:
    """Return the path to ``collectors.yaml``."""
    return work_path / SOLUTION_DIR / SOLUTION_COLLECTORS_FILE


def get_sbom_ignore_path(work_path: Path) -> Path:
    """Return the path to ``sbom-ignore.yaml``."""
    return work_path / SOLUTION_DIR / SOLUTION_SBOM_IGNORE_FILE


def get_cve_allowed_path(work_path: Path) -> Path:
    """Return the path to ``cve-allowed.yaml``."""
    return work_path / SOLUTION_DIR / SOLUTION_CVE_ALLOWED_FILE


def get_guide_path(work_path: Path) -> Path:
    """Return the path to ``guide.yaml``."""
    return work_path / SOLUTION_DIR / SOLUTION_GUIDE_FILE


def get_logs_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/logs/``."""
    return work_path / SOLUTION_DIR / SOLUTION_LOGS_DIR


def get_integrations_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/integrations/``."""
    return work_path / SOLUTION_DIR / SOLUTION_INTEGRATIONS_DIR


def get_policies_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/policies/``."""
    return work_path / SOLUTION_DIR / SOLUTION_POLICIES_DIR


def get_templates_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/templates/``."""
    return work_path / SOLUTION_DIR / SOLUTION_TEMPLATES_DIR


def get_diagrams_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/diagrams/``."""
    return work_path / SOLUTION_DIR / SOLUTION_DIAGRAMS_DIR


def get_plugins_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/plugins/``."""
    return work_path / SOLUTION_DIR / SOLUTION_PLUGINS_DIR


def get_lockfile_parsers_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/lockfile_parsers/``."""
    return work_path / SOLUTION_DIR / SOLUTION_LOCKFILE_PARSERS_DIR


def get_schemas_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/schemas/``."""
    return work_path / SOLUTION_DIR / SOLUTION_SCHEMAS_DIR


def get_locks_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/locks/``."""
    return work_path / SOLUTION_DIR / SOLUTION_LOCKS_DIR


def get_deployments_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/deployments/``."""
    return work_path / SOLUTION_DIR / SOLUTION_DEPLOYMENTS_DIR


def get_outputs_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/outputs/``."""
    return work_path / SOLUTION_DIR / SOLUTION_OUTPUTS_DIR


def get_build_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/build/``."""
    return work_path / SOLUTION_DIR / SOLUTION_BUILD_DIR


def get_deploy_log_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/deploy-log/``."""
    return work_path / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR


def get_cost_cache_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/cache/cost/``."""
    return work_path / SOLUTION_DIR / SOLUTION_COST_CACHE_DIR


def get_cost_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/cost/``."""
    return work_path / SOLUTION_DIR / SOLUTION_COST_DIR


def get_drift_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/drift/``."""
    return work_path / SOLUTION_DIR / SOLUTION_DRIFT_DIR


def get_drift_rules_path(work_path: Path) -> Path:
    """Return the path to ``.strata/drift_rules.yaml`` (workspace override)."""
    return work_path / SOLUTION_DIR / SOLUTION_DRIFT_RULES_FILE


def get_ai_cache_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/cache/ai/`` (AI response cache)."""
    return work_path / SOLUTION_DIR / SOLUTION_AI_CACHE_DIR


def get_ai_prompts_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/prompts/`` (operator prompt overrides)."""
    return work_path / SOLUTION_DIR / SOLUTION_PROMPTS_DIR


def get_model_cache_dir(work_path: Path) -> Path:
    """Return the path to ``.strata/cache/model/`` (resolved-model cache, ADR-0026)."""
    return work_path / SOLUTION_DIR / SOLUTION_MODEL_CACHE_DIR


def get_model_cache_db_path(work_path: Path) -> Path:
    """Return the path to ``.strata/cache/model/cache.db`` (ADR-0026)."""
    return get_model_cache_dir(work_path) / SOLUTION_MODEL_CACHE_DB_FILE
