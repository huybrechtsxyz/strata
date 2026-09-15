"""Terraform input validator — cross-checks declared inputs against module variables.

Parses variable declarations from .tf files in a provisioner's source directory
and compares them against the variable/feature keys declared in environment YAML.
Catches typos (undeclared inputs) and missing required variables before deploy.
"""

from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import hcl2

from strata.logger import get_logger

logger = get_logger(__name__)

# Keys that strata injects automatically — should not trigger "undeclared" errors.
STRATA_INJECTED_KEYS: frozenset = frozenset(
    {
        # Platform-level structural variables emitted by the builder
        "workspace_name",
        "workspace_version",
        "deployment_name",
        "environment",
        "platform_version",
        "labels",
        "metadata",
        "platform_providers",
        "topologies",
        "modules",
        "strata_tenant",
    }
)


@dataclass
class TerraformVariable:
    """Parsed variable declaration from a .tf file."""

    name: str
    type_expr: Optional[str] = None
    has_default: bool = False
    default_value: Any = None
    nullable: bool = True
    description: Optional[str] = None
    sensitive: bool = False
    validation_rules: int = 0


@dataclass
class InputCheckResult:
    """Result of cross-checking declared inputs against module variables."""

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


def parse_variables_tf(source_path: Path) -> Dict[str, TerraformVariable]:
    """Parse all variable declarations from .tf files in a directory.

    Scans all *.tf files in source_path (root level only — Terraform only reads
    variables from the root module directory).

    Args:
        source_path: Directory containing .tf files.

    Returns:
        Dict keyed by variable name → TerraformVariable.
    """
    variables: Dict[str, TerraformVariable] = {}

    if not source_path.exists() or not source_path.is_dir():
        return variables

    for tf_file in sorted(source_path.glob("*.tf")):
        try:
            with open(tf_file, "r", encoding="utf-8") as f:
                parsed = hcl2.load(f)
        except Exception as exc:
            logger.debug("Failed to parse HCL file for input validation", file=str(tf_file), error=str(exc))
            continue

        for var_block in parsed.get("variable", []):
            # python-hcl2 parses variable blocks as [{name: {body...}}]
            if not isinstance(var_block, dict):
                continue
            for var_name, var_body in var_block.items():
                # python-hcl2 may preserve HCL string quoting — strip it
                clean_name = _strip_quotes(var_name)
                if not isinstance(var_body, dict):
                    var_body = {}
                variables[clean_name] = TerraformVariable(
                    name=clean_name,
                    type_expr=_extract_type(var_body),
                    has_default="default" in var_body,
                    default_value=var_body.get("default"),
                    nullable=var_body.get("nullable", True),
                    description=_extract_description(var_body),
                    sensitive=var_body.get("sensitive", False),
                    validation_rules=len(var_body.get("validation", [])),
                )

    return variables


def check_inputs(
    injected_keys: Set[str],
    module_variables: Dict[str, TerraformVariable],
    *,
    environment_keys: Optional[Set[str]] = None,
    excluded_keys: Optional[Set[str]] = None,
) -> InputCheckResult:
    """Cross-check injected input keys against module variable declarations.

    Args:
        injected_keys: Variable/feature/secret keys that will actually be emitted
            to tfvars for this provisioner. Under ADR-0078 scoping this is the
            union of every declaring component's ``spec.references``; otherwise
            it is every key declared anywhere in the environment (today's
            behaviour, unchanged).
        module_variables: Parsed variables from the module's .tf files.
        environment_keys: The full set of keys declared anywhere in the
            environment, unnarrowed by scoping. Used only to distinguish an
            optional variable nobody supplied anywhere (info) from one the
            environment supplies but scoping excluded (warning) — see rules
            3a/3b in ADR-0078. Defaults to ``injected_keys`` when omitted,
            which makes every optional-variable case fall through to the
            info branch — i.e. today's behaviour, unchanged.
        excluded_keys: Keys to skip (e.g. strata-injected platform variables).

    Returns:
        InputCheckResult with errors (undeclared), warnings (unsupplied required,
        or unsupplied-but-available-in-the-environment), and info (optional,
        genuinely unsupplied anywhere).
    """
    excluded = excluded_keys or set()
    effective_environment_keys = environment_keys if environment_keys is not None else injected_keys
    result = InputCheckResult()

    module_var_names = set(module_variables.keys())

    # 1. Find undeclared inputs (typo detection)
    for key in sorted(injected_keys):
        if key in excluded:
            continue
        if key not in module_var_names:
            suggestion = _find_closest(key, module_var_names)
            msg = f"Input '{key}' is not declared in variables.tf"
            if suggestion:
                msg += f" (did you mean '{suggestion}'?)"
            result.errors.append(msg)

    # 2. Find required variables not supplied
    for var_name in sorted(module_variables.keys()):
        var = module_variables[var_name]
        if var_name in excluded:
            continue
        if not var.has_default and var_name not in injected_keys:
            result.warnings.append(f"Required variable '{var_name}' (no default) is not supplied by any input")

    # 3a/3b. Optional variables not supplied. 3a (info): nobody supplied a value
    # anywhere — the module's default is exactly what's intended, the common,
    # unremarkable case. 3b (warning, only reachable when scoping actually
    # narrowed injected_keys below environment_keys): the environment DOES have
    # a value, but no component's references names it, so it was excluded.
    for var_name in sorted(module_variables.keys()):
        var = module_variables[var_name]
        if var_name in excluded:
            continue
        if var.has_default and var_name not in injected_keys:
            default_repr = repr(var.default_value) if var.default_value is not None else "null"
            if var_name in effective_environment_keys:
                result.warnings.append(
                    f"Optional variable '{var_name}' not supplied — Terraform will use its "
                    f"default={default_repr}. '{var_name}' has a value in the environment, but "
                    "no component of this scoped provisioner references it, so it was not injected."
                )
            else:
                result.info.append(f"Variable '{var_name}' has default={default_repr} and is not overridden")

    return result


def _find_closest(key: str, candidates: Set[str], cutoff: float = 0.6) -> Optional[str]:
    """Find the closest matching variable name for typo suggestions."""
    matches = get_close_matches(key, sorted(candidates), n=1, cutoff=cutoff)
    return matches[0] if matches else None


def _strip_quotes(s: str) -> str:
    """Strip surrounding double quotes from a string (python-hcl2 artifact)."""
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def _extract_type(var_body: Dict[str, Any]) -> Optional[str]:
    """Extract the type expression from a variable body.

    python-hcl2 parses type expressions as strings or nested structures.
    We store it as a string for informational purposes.
    """
    type_val = var_body.get("type")
    if type_val is None:
        return None
    if isinstance(type_val, str):
        return _strip_quotes(type_val)
    # Complex type expressions (e.g. object({...})) may be parsed as dicts
    return str(type_val)


def _extract_description(var_body: Dict[str, Any]) -> Optional[str]:
    """Extract description from a variable body."""
    desc = var_body.get("description")
    if isinstance(desc, str):
        return _strip_quotes(desc)
    return None
