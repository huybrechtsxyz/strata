#!/usr/bin/env python3
"""
===============================================================================
Script Name   : capabilities.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Capability-based interfaces (Protocols) for integration classification.

Defines capability interfaces that integrations can implement to advertise
their supported operations. Uses structural subtyping (Protocols) for duck-typing
style capability detection.

Usage:
    from xyz_platform.integrations.capabilities import IVariableStore, ISecretStore

    # Check if integration supports capability
    if isinstance(integration, IVariableStore):
        value = integration.get_variable("key")

    # Query integrations by capability
    secret_stores = [i for i in integrations if isinstance(i, ISecretStore)]
===============================================================================
"""

from typing import Any, List, Optional, Protocol, runtime_checkable


__all__ = [
    # Protocol interfaces
    "IVariableStore",
    "ISecretStore",
    "IFeatureStore",
    "IKVStore",
    "IRepositoryTool",
    "IInfrastructureTool",
    "IContainerTool",
    # Registry and mapping
    "CAPABILITY_REGISTRY",
    "CAPABILITY_MAP",
    "VALID_CAPABILITY_NAMES",
    # Custom integration types
    "CUSTOM_INTEGRATION_TYPES",
    "CUSTOM_TYPE_CAPABILITY_MAP",
    # Helper functions
    "get_capability_name",
    "get_capability_protocol",
    "validate_capability_name",
    "is_custom_integration_type",
    "validate_integration_type",
]


@runtime_checkable
class IVariableStore(Protocol):
    """
    Capability: Integration supports variable storage operations.

    Integrations implementing this interface can retrieve, store, and list
    configuration variables (non-sensitive key-value data).

    Examples: HashiCorp Consul, Azure App Configuration, HashiCorp Vault
    """

    def get_variable(self, key: str, **kwargs) -> Optional[Any]:
        """Get a variable value from the store."""
        ...

    def set_variable(self, key: str, value: Any, **kwargs) -> bool:
        """Set a variable value in the store."""
        ...

    def list_variables(self, prefix: str = "", **kwargs) -> List[str]:
        """List available variable keys in the store."""
        ...


@runtime_checkable
class ISecretStore(Protocol):
    """
    Capability: Integration supports secret storage operations.

    Integrations implementing this interface can retrieve, store, and list
    sensitive data (passwords, API keys, certificates).

    Examples: HashiCorp Vault, Azure KeyVault, Bitwarden
    """

    def get_secret(self, key: str, **kwargs) -> Optional[str]:
        """Get a secret value from the store."""
        ...

    def set_secret(self, key: str, value: str, **kwargs) -> bool:
        """Set a secret value in the store."""
        ...

    def list_secrets(self, prefix: str = "", **kwargs) -> List[str]:
        """List available secret keys in the store."""
        ...


@runtime_checkable
class IFeatureStore(Protocol):
    """
    Capability: Integration supports feature flag operations.

    Integrations implementing this interface can retrieve, store, and list
    feature flags (boolean configuration switches).

    Examples: Azure App Configuration, HashiCorp Consul
    """

    def get_feature(self, key: str, **kwargs) -> Optional[bool]:
        """Get a feature flag value from the store."""
        ...

    def set_feature(self, key: str, value: bool, **kwargs) -> bool:
        """Set a feature flag value in the store."""
        ...

    def list_features(self, prefix: str = "", **kwargs) -> List[str]:
        """List available feature flag keys in the store."""
        ...


@runtime_checkable
class IKVStore(Protocol):
    """
    Capability: Integration supports generic key-value storage operations.

    Integrations implementing this interface provide direct key-value storage
    without specific semantic categorization (variables vs secrets).

    Examples: HashiCorp Consul, HashiCorp Vault (KV engine)
    """

    def get_kv(self, key: str, **kwargs) -> Optional[Any]:
        """Get a key-value pair from the store."""
        ...

    def set_kv(self, key: str, value: Any, **kwargs) -> bool:
        """Set a key-value pair in the store."""
        ...

    def list_kv(self, prefix: str = "", **kwargs) -> List[str]:
        """List available keys in the store."""
        ...


@runtime_checkable
class IRepositoryTool(Protocol):
    """
    Capability: Integration supports repository operations.

    Integrations implementing this interface can clone, pull, push, and
    manage version control repositories.

    Examples: Git, Mercurial, SVN
    """

    def clone(
        self, repo_url: str, target_dir: str, branch: Optional[str] = None, **kwargs
    ) -> Any:
        """Clone a repository."""
        ...


@runtime_checkable
class IInfrastructureTool(Protocol):
    """
    Capability: Integration supports infrastructure provisioning.

    Integrations implementing this interface can provision, plan, and
    destroy infrastructure resources.

    Examples: Terraform, Pulumi, CloudFormation
    """

    def init(self, working_dir: str, **kwargs) -> Any:
        """Initialize infrastructure tool in working directory."""
        ...

    def plan(self, working_dir: str, **kwargs) -> Any:
        """Plan infrastructure changes."""
        ...

    def apply(self, working_dir: str, **kwargs) -> Any:
        """Apply infrastructure changes."""
        ...


@runtime_checkable
class IContainerTool(Protocol):
    """
    Capability: Integration supports container operations.

    Integrations implementing this interface can build, run, push, pull, and
    manage container images and containers.

    Examples: Docker, Podman, Containerd
    """

    def build(self, context_dir: str, tag: str, **kwargs) -> Any:
        """Build a container image."""
        ...

    def run(self, image: str, **kwargs) -> Any:
        """Run a container."""
        ...

    def push(self, image: str, **kwargs) -> Any:
        """Push a container image to a registry."""
        ...

    def pull(self, image: str, **kwargs) -> Any:
        """Pull a container image from a registry."""
        ...


# Capability registry for metadata
CAPABILITY_REGISTRY = {
    "IVariableStore": {
        "description": "Configuration variable storage operations",
        "methods": ["get_variable", "set_variable", "list_variables"],
        "examples": ["HashiCorp Consul", "Azure App Configuration"],
    },
    "ISecretStore": {
        "description": "Secure secret storage operations",
        "methods": ["get_secret", "set_secret", "list_secrets"],
        "examples": ["HashiCorp Vault", "Azure KeyVault", "Bitwarden"],
    },
    "IFeatureStore": {
        "description": "Feature flag operations",
        "methods": ["get_feature", "set_feature", "list_features"],
        "examples": ["Azure App Configuration", "HashiCorp Consul"],
    },
    "IKVStore": {
        "description": "Generic key-value storage",
        "methods": ["get_kv", "set_kv", "list_kv"],
        "examples": ["HashiCorp Consul", "HashiCorp Vault"],
    },
    "IRepositoryTool": {
        "description": "Version control repository operations",
        "methods": ["clone"],
        "examples": ["Git", "Mercurial"],
    },
    "IInfrastructureTool": {
        "description": "Infrastructure provisioning operations",
        "methods": ["init", "plan", "apply"],
        "examples": ["Terraform", "Pulumi"],
    },
    "IContainerTool": {
        "description": "Container management operations",
        "methods": ["build", "run", "push", "pull"],
        "examples": ["Docker", "Podman"],
    },
}


# Mapping from user-friendly capability names to Protocol classes
CAPABILITY_MAP = {
    "variables": IVariableStore,
    "secrets": ISecretStore,
    "features": IFeatureStore,
    "keyvalue": IKVStore,
    "repository": IRepositoryTool,
    "infrastructure": IInfrastructureTool,
    "container": IContainerTool,
}


# Valid capability names users can specify in config
VALID_CAPABILITY_NAMES = frozenset(CAPABILITY_MAP.keys())


# Custom integration types
# These are generic wrappers for CLI tools/APIs without dedicated Python classes
CUSTOM_INTEGRATION_TYPES = frozenset(
    [
        "customsecret",  # Generic secret store CLI wrapper
        "customvariable",  # Generic variable store CLI wrapper
        "customkeyvalue",  # Generic KV store CLI wrapper
        "customfeature",  # Generic feature flag store CLI wrapper
        "customrepository",  # Generic VCS tool wrapper
        "custominfrastructure",  # Generic IaC tool wrapper
        "customcontainer",  # Generic container tool wrapper
        "customapi",  # Generic REST API wrapper
    ]
)


# Mapping of custom types to their expected primary capability
CUSTOM_TYPE_CAPABILITY_MAP = {
    "customsecret": "secrets",
    "customvariable": "variables",
    "customkeyvalue": "keyvalue",
    "customfeature": "features",
    "customrepository": "repository",
    "custominfrastructure": "infrastructure",
    "customcontainer": "container",
    "customapi": None,  # Can have any capability
}


def get_capability_name(capability: type) -> str:
    """Get the name of a capability protocol class."""
    return capability.__name__


def get_capability_protocol(name: str) -> Optional[type]:
    """
    Get the Protocol class for a capability name.

    Args:
        name: Short capability name (e.g., 'secrets', 'variables')

    Returns:
        Protocol class (e.g., ISecretStore) or None if not found
    """
    return CAPABILITY_MAP.get(name)


def validate_capability_name(name: str) -> bool:
    """
    Check if a capability name is valid.

    Args:
        name: Capability name to validate

    Returns:
        True if valid, False otherwise
    """
    return name in VALID_CAPABILITY_NAMES


def is_custom_integration_type(integration_type: str) -> bool:
    """
    Check if an integration type is a custom type.

    Args:
        integration_type: Type string to check

    Returns:
        True if it's a custom type (customsecret, customvariable, etc.)
    """
    return integration_type in CUSTOM_INTEGRATION_TYPES


def validate_integration_type(integration_type: str, registered_types: set) -> bool:
    """
    Validate that integration type is either a custom type or registered built-in.

    Args:
        integration_type: Type string to validate
        registered_types: Set of registered built-in type names from factory

    Returns:
        True if type is valid (custom or registered)
    """
    return (
        integration_type in CUSTOM_INTEGRATION_TYPES
        or integration_type in registered_types
    )
