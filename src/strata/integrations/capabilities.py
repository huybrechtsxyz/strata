"""Capability Protocol interfaces for integration classification (variables, secrets, features, KV, repo, infra, container)."""

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
    "ISiemSink",
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

    def clone(self, repo_url: str, target_dir: str, branch: Optional[str] = None, **kwargs) -> Any:
        """Clone a repository."""
        ...


@runtime_checkable
class IInfrastructureTool(Protocol):
    """
    Capability: Integration supports infrastructure provisioning and configuration management.

    Integrations implementing this interface can provision, plan, and
    destroy infrastructure resources, or apply configuration management
    to existing hosts (e.g. Ansible playbooks, Salt states).

    Use ``infrastructure`` as the capability name for any IaC or configuration
    management tool — Terraform, OpenTofu, Ansible, Pulumi, CloudFormation, etc.
    There is no separate ``configuration`` capability; ``infrastructure`` is the
    umbrella term for all infrastructure and configuration-as-code tools.

    Examples: Terraform, OpenTofu, Ansible, Pulumi, CloudFormation
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
    Capability: Integration supports container and container-orchestration operations.

    Integrations implementing this interface can build, run, push, pull, and
    manage container images and containers, or deploy packaged applications
    to container platforms (e.g. Helm chart releases to Kubernetes).

    Use ``container`` as the capability name for any container runtime or
    container-native deployment tool — Docker, Podman, Helm, etc.  There is
    no separate ``deployment`` capability; ``container`` is the umbrella term
    for all container and container-orchestration tooling.

    Examples: Docker, Podman, Helm, Containerd
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


@runtime_checkable
class ISiemSink(Protocol):
    """
    Capability: Integration supports forwarding structured events to an immutable audit store.

    Integrations implementing this interface can receive structured audit events
    and forward them to external immutable storage (e.g. Azure Sentinel, ELK, OTel).

    Used by: AuditController (deploy logs), application audit logger (CLI actions),
    PolicyController (violations), ValueController (secret access), and future producers.

    Examples: Azure Sentinel, ELK/Logstash, OpenTelemetry collector
    """

    def send_event(self, log_type: str, payload: dict, **kwargs) -> bool:
        """Send a single structured event to the sink.

        Args:
            log_type: Event category (deploy_audit, cli_action, policy_violation, etc.)
            payload:  Structured JSON-serialisable dict for the event.

        Returns:
            True if the event was delivered, False otherwise.
        """
        ...

    def send_batch(self, log_type: str, payloads: List[dict], **kwargs) -> bool:
        """Send a batch of structured events to the sink.

        Returns:
            True if all events were delivered, False otherwise.
        """
        ...


# Capability registry for metadata
# NOTE: ISiemSink is intentionally absent from CAPABILITY_REGISTRY — it is a
# platform-wide forwarding protocol, not a data-store capability.  Sinks are
# resolved by the AuditController at runtime from the environment audit config,
# not through the standard integration factory capability lookup.
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
        "description": "Infrastructure provisioning and configuration management operations (IaC, CM)",
        "methods": ["init", "plan", "apply"],
        "examples": ["Terraform", "OpenTofu", "Ansible", "Pulumi"],
    },
    "IContainerTool": {
        "description": "Container management and container-native deployment operations",
        "methods": ["build", "run", "push", "pull"],
        "examples": ["Docker", "Podman", "Helm"],
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
    "audit": ISiemSink,
}


# Valid capability names users can specify in config
VALID_CAPABILITY_NAMES = frozenset(CAPABILITY_MAP.keys()) | {"api"}


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
        "customaudit",  # Generic SIEM/audit sink wrapper
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
    "customaudit": "audit",
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
    return integration_type in CUSTOM_INTEGRATION_TYPES or integration_type in registered_types
