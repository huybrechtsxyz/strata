# GitHub Copilot Instructions for xyz-platform

## Project Overview

XYZ Platform is a Python-based Infrastructure as Code (IaC) orchestration platform using a **multi-repository architecture** for audit-proof, compliance-ready deployments. It manages virtual machine workspaces with cluster orchestration through declarative YAML configurations.

**Key Architecture Principles:**

- Separation of concerns: Platform, modules, configs, and deployments in separate repos
- Version-controlled everything: Git commits create immutable audit trails
- Declarative configuration: All infrastructure defined in YAML
- Deployment manifests: Capture exact state for regulatory compliance (NIS2, ISAE 3402 Type 2)

## Project Structure

```
xyz-platform/
├── src/xyz_platform/           # Core platform Python code
│   ├── cli.py                  # Click-based CLI entry point
│   └── __main__.py             # Package entry point
├── config/                     # Configuration examples and defaults
│   ├── providers/              # Cloud provider configs (Kamatera, AWS, etc.)
│   ├── workspaces/             # Workspace topology definitions
│   ├── deployments/            # Deployment manifests
│   ├── environments/           # Environment-specific configs
│   ├── resources/              # VM/resource specifications
│   ├── firewalls/              # Security rules
│   ├── modules/                # Application modules (Traefik, etc.)
│   ├── namespaces/             # Logical groupings
│   └── configurations/         # Platform configurations
├── tests/                      # Test suite (mirrors src/ structure)
├── docs/                       # Documentation
└── scripts/                    # Utility scripts
```

## YAML Configuration Schema

All configuration files follow a consistent Kubernetes-inspired structure:

```yaml
apiVersion: platform.huybrechts.xyz/v1 # Schema version
kind: <resource-type> # workspace|provider|deployment|etc.
meta:
  name: resource_name # Must match: ^[a-z][a-z0-9_]*$
  annotations:
    description: "Human-readable description"
  labels:
    version: "1.0.0"
    key: value
  tags: ["tag1", "tag2"] # For categorization
spec:
  # Resource-specific configuration
  properties: {}
  # ... varies by kind
```

### Naming Rules (CRITICAL)

**All resource names MUST follow this pattern:**

- Format: `^[a-z][a-z0-9_]*$`
- Start with lowercase letter
- Only lowercase letters, numbers, underscores
- No hyphens, spaces, or special characters
- **Rationale**: Ensures compatibility with Terraform, Ansible, shell scripts, and IaC tools

### Configuration Types

- **workspace**: Defines infrastructure topology, components, and volumes
- **provider**: Cloud provider connection (type, region)
- **deployment**: Links workspace + environment + configuration
- **environment**: Environment-specific settings (production, staging, etc.)
- **resource**: VM specifications (CPU, memory, disk)
- **firewall**: Network security rules
- **module**: Application module configuration
- **namespace**: Logical resource grouping

## Development Workflow

### Running the CLI

```bash
# Development mode
python -m xyz_platform --help

# After installation
xyz --help
xyz version
```

### Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=xyz_platform --cov-report=html

# Run specific test file
pytest tests/test_cli.py
```

### Code Quality

```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Type checking
mypy src/

# Linting
flake8 src/ tests/
```

## Coding Conventions

### File Headers

Always include this header in new Python files:

```python
# !/usr/bin/env python3
"""
===============================================================================
Script Name   : filename.py
Author        : Vincent Huybrechts
Created       : YYYY-MM-DD
Last Updated  : YYYY-MM-DD
Version       : 1.0.0
Python Version: 3.12+
Description   : Brief description of the file's purpose.
===============================================================================
"""
```

### Import Organization

```python
# Standard library imports
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Third-party imports
import click
import yaml

# Local imports (use absolute imports from xyz_platform)
from xyz_platform.cli import main
```

### CLI Development (Click Framework)

The platform uses Click for CLI commands. Follow these patterns:

```python
@click.group()
def main():
    """XYZ Platform CLI entry point."""
    pass

@main.command()
@click.option("--raw", "-r", is_flag=True, help="Output raw format")
@click.argument("name", required=True)
def command_name(name: str, raw: bool):
    """Command description for help text."""
    # Implementation
    click.echo(f"Result: {name}")
```

**Key patterns:**

- Use `@click.group()` for command groups
- Use `@click.command()` for individual commands
- Provide clear help text in docstrings
- Use `click.echo()` for output (not print)
- Handle errors with `click.ClickException` or `click.UsageError`

### Windows Compatibility

The CLI includes UTF-8 console configuration for Windows:

```python
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```

**Always test CLI output on Windows** - emoji and special characters require UTF-8 handling.

### Testing Patterns

The platform uses **two test frameworks**:

**Python Tests (pytest)** - Unit tests for models, services, and utilities:

- Tests mirror source structure in `tests/xyz_platform/`
- Test file naming: `test_<module>.py` (e.g., `test_cli.py`)
- Use pytest for all testing
- Mock external dependencies and file system with `pytest` fixtures or `unittest.mock`
- Test both success and failure cases
- Use parametrized tests for multiple scenarios:

```python
import pytest

@pytest.mark.parametrize("input,expected", [
    ("valid_name", True),
    ("Invalid-Name", False),
    ("123invalid", False),
])
def test_name_validation(input, expected):
    assert validate_name(input) == expected
```

**PowerShell Tests (Pester)** - CLI integration tests:

- Located in `tests/scripts/Commands.Tests.ps1`
- Test CLI commands end-to-end with real file I/O
- Use Pester v5+ framework with contexts and tags
- Manual test suite in `tests/scripts/test-commands.ps1` for quick validation

```powershell
Describe "CLI Command" -Tag "command" {
    Context "Valid Input" {
        It "Should succeed with exit code 0" {
            $result = & python -m xyz_platform command --file tests/data/file.yaml
            $LASTEXITCODE | Should -Be 0
        }
    }
}

# Run tests
Invoke-Pester -Path .\tests\scripts\Commands.Tests.ps1 -Tag "command"
```

**Testing Guidelines:**

- Python tests: Focus on logic, models, validators, services
- Pester tests: Focus on CLI UX, exit codes, file handling, output formatting
- Both: Test success cases, error handling, and edge cases

### Validation Patterns

The platform uses **two-phase validation**:

**Phase 1: Model Validation** - Structural and uniqueness checks using Pydantic validators:

```python
from pydantic import model_validator
from xyz_platform.utils.config_validators import (
    validate_unique_variable_keys,
    validate_unique_secret_keys,
    validate_unique_feature_keys,
    validate_store_security_policy,
)

@model_validator(mode="after")
def validate_unique_keys(self) -> "YourModel":
    """Validate unique keys for variables, secrets, and features."""
    validate_unique_variable_keys(self.spec.variables)
    validate_unique_secret_keys(self.spec.secrets)
    validate_unique_feature_keys(self.spec.features)
    return self
```

**Phase 2: Service Validation** - Cross-reference and security policy checks:

```python
def validate_your_config(config: YourModel, context: ValidationContext) -> None:
    """Validate configuration with cross-references."""
    # Validate store security policies
    if config.spec.variables:
        validate_store_security_policy(config.spec.variables, "variable")
    if config.spec.secrets:
        validate_store_security_policy(config.spec.secrets, "secret")
    if config.spec.features:
        validate_store_security_policy(config.spec.features, "feature")

    # Additional dynamic validation...
```

**Store Models (Variables, Secrets, Features):**

All configuration models can include stores for variables, secrets, and features:

```python
from xyz_platform.models.stores import VariableStoreModel, SecretStoreModel, FeatureStoreModel

class YourSpecModel(BaseModel):
    variables: List[VariableStoreModel] = []
    secrets: List[SecretStoreModel] = []
    features: List[FeatureStoreModel] = []
```

**Store Types** (enum-based):

- **variable**: `environment`, `file`, `http`, `https`
- **secret**: `vault`, `azure_keyvault`, `aws_secrets_manager`, `environment`, `file`
- **feature**: `environment`, `file`, `http`, `https`, `api`

**Models with validation:** DeploymentModel, EnvironmentModel, WorkspaceModel, ModuleModel, ProviderModel, NamespaceModel, ResourceModel

### Type Hints

- Use `typing` module for Python 3.9+ compatibility
- All function parameters and return values should have type hints:

```python
from typing import Dict, List, Optional, Tuple

def process_config(
    config_path: Path,
    validate: bool = True
) -> Tuple[bool, Optional[str]]:
    """Process configuration file."""
    # Implementation
    return True, None
```

### Error Handling

- Use specific exception types
- Provide clear, actionable error messages with context
- For CLI: use `click.ClickException` or `click.UsageError`
- Include file paths, line numbers, or config names in error messages

### Naming Conventions

- **Classes**: PascalCase (e.g., `WorkspaceService`, `ClusterNodeType`)
- **Functions/Methods**: snake_case (e.g., `get_manager_resource`, `validate_cluster_topology`)
- **Constants**: UPPER_SNAKE_CASE (e.g., `CONFIG_PATH`, `MANAGER_TYPES`)
- **Files**: snake_case (e.g., `workspace_service.py`, `cluster_model.py`)

**Command Classes and Files:**
- **Command classes**: Use `TopicGroupCommand` pattern (e.g., `StatusSessionCommand`, `LogsSessionCommand`, `TopicHelpCommand`)
- **Command files**: Use `topic_group_command.py` pattern (e.g., `status_session_command.py`, `logs_session_command.py`, `topic_help_command.py`)
- **Base command classes**: Use `BaseGroupCommand` pattern (e.g., `BaseSessionCommand`, `BaseHelpCommand`)
- **Base command files**: Use `base_group_command.py` pattern (e.g., `base_session_command.py`, `base_help_command.py`)
- **CLI wiring files**: Use `cli_group.py` pattern for Click decorators (e.g., `cli_session.py`, `cli_build.py`)
- **CLI common utilities**: Use `cli_common.py` for shared Click decorators
- **Variables**: snake_case (e.g., `workspace_name`, `topology_config`)
- **Private Members**: Prefix with underscore (e.g., `_validate_config`, `_internal_state`)
- **Modules/Packages**: snake_case (e.g., `xyz_platform`, `common_utils`)

### Python File and Class Organization

**Complete reference:** See `docs/examples/file_structure.py` for detailed examples.

**File Structure (Module Level):**

1. **Shebang and docstring** (if executable)
2. **Imports** (in order with blank lines between):
   - Standard library imports
   - Third-party imports
   - Local/application imports
3. **Module-level constants** (ALL_CAPS)
4. **Module-level variables** (prefix with `_` for private)
5. **Module-level functions** (public first, then `_private`)
6. **Classes**
7. **Main execution block** (`if __name__ == "__main__":`)

**Class Member Order:**

1. **Class variables/constants**
2. **Special methods** (`__init__`, `__repr__`, `__str__`, `__eq__`, etc.)
3. **Class methods** (`@classmethod`)
4. **Static methods** (`@staticmethod`)
5. **Properties** (`@property`, `@setter`)
6. **Public methods** (alphabetically or by logical grouping)
7. **Protected/private methods** (prefixed with `_`, at the end)

**Visibility Conventions:**

- `method_name()` - **Public**: Part of the external API
- `_method_name()` - **Protected**: Internal use, accessible to subclasses (PREFERRED)
- `__method_name()` - **Private**: Name mangling, rarely used (avoid unless necessary)

**Example:**

```python
import sys
from pathlib import Path
from typing import Dict, List

import click

from xyz_platform.logger import get_logger

logger = get_logger(__name__)
DEFAULT_TIMEOUT = 30

def public_function() -> bool:
    """Public utility function."""
    return _private_helper()

def _private_helper() -> bool:
    """Internal helper function."""
    return True

class MyService:
    """Example service class."""

    MAX_RETRIES = 3

    def __init__(self, name: str):
        self.name = name
        self._internal_state = None

    def __repr__(self) -> str:
        return f"MyService(name={self.name!r})"

    @classmethod
    def from_config(cls, config: Dict):
        return cls(name=config["name"])

    @property
    def state(self):
        return self._internal_state

    def execute(self) -> bool:
        """Public method."""
        return self._validate()

    def _validate(self) -> bool:
        """Internal validation method."""
        return True
```

**Key Points:**

- Always use `_` prefix for internal methods (not `__`)
- Methods like `_start_execution()` are correctly named (used by subclasses)
- Group related methods together for readability
- Keep public API minimal and well-documented

## Multi-Repository Architecture

### Repository Types

The platform uses a naming convention to indicate repository purpose:

- **`xyz_platform`** - Core platform code (this repo)
- **`xyz_{type}_{name}`** - Supporting repositories where:
  - `{type}` = `module`, `config`, or `deploy`
  - `{name}` = descriptive identifier

**Examples:**

- `xyz_module_traefik` - Traefik reverse proxy module
- `xyz_module_monitoring` - Monitoring stack module
- `xyz_config_production` - Production configurations
- `xyz_deploy_customer_a` - Customer A deployment instances

### Audit Trail Requirements

When creating deployment functionality, **always capture**:

- Git commit SHA for all configuration sources
- Version tags for platform and modules
- Timestamp (ISO 8601 format)
- User/deployer information
- Complete resource configuration snapshot
- Approval references (PR numbers, tickets)

**Example deployment manifest structure:**

```yaml
deployment_manifest:
  timestamp: "2026-01-19T10:30:00Z"
  platform_version: "1.0.0"
  platform_commit: "abc123def456"
  sources:
    - repo: "xyz_config_production"
      commit: "789ghi012jkl"
      path: "workspaces/prod-workspace.yaml"
  deployed_by: "user@example.com"
  approval: "PR-123"
```

### Configuration Source References

In YAML configs, source references track versioning:

```yaml
source:
  type: local|git|http
  repository: <repo-url-or-path>
  reference: <branch|tag|commit>
  source_path: <path-to-file>
  deploy_path: <destination-path>
```

## Domain-Specific Knowledge

### Topology Components

Workspaces define **topology** with these component types:

- **manager**: Cluster control plane nodes (Docker Swarm managers, K8s control plane)
- **worker**: Compute/workload nodes
- **infra**: Infrastructure services (monitoring, logging, storage)

**Role assignment matters:**

- Managers need odd numbers for quorum (1, 3, 5)
- Workers scale horizontally
- Infrastructure nodes typically replicated for HA

### Volume Types

Docker Swarm workspace volumes:

- **replicated**: Data replicated across nodes
- **local**: Node-local storage
- **distributed**: Distributed filesystem (GlusterFS, Ceph)

### Provider Types

Currently supported:

- `kamatera` - Kamatera cloud provider

**When adding new providers**, ensure:

- Terraform provider compatibility
- Region/zone mapping
- API credential management
- Network configuration templates
