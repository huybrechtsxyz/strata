"""Capability Protocol interfaces for integration classification (variables, secrets, features, KV, repo, infra, container, cost)."""

from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from strata.utils.secret_metadata import SecretMetadata

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
    "ICveScanner",
    "IIacSecurityScanner",
    "ICostEstimator",
    "IAzureTool",
    "IAWSTool",
    "IGCloudTool",
    "IIdentityProvider",
    "IDiagramRenderer",
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
        """Get a variable value from the store.

        Returns:
            The value, or ``None`` if the key does not exist.

        Raises:
            SecretStoreUnavailableError: if the store cannot be reached or
                authentication fails. Never return ``None`` for this case.
        """
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
        """Get a secret value from the store.

        Returns:
            The secret value, or ``None`` if the key does not exist.

        Raises:
            SecretStoreUnavailableError: if the store cannot be reached or
                authentication fails. Never return ``None`` for this case —
                callers (e.g. ``ValueController``) rely on the distinction to
                avoid unsafe fallback behaviour such as generate-on-missing.
        """
        ...

    def set_secret(self, key: str, value: str, **kwargs) -> bool:
        """Set a secret value in the store."""
        ...

    def list_secrets(self, prefix: str = "", **kwargs) -> List[str]:
        """List available secret keys in the store."""
        ...

    def get_secret_metadata(self, key: str, **kwargs) -> Optional[SecretMetadata]:
        """Return metadata (timestamps, version) for a secret, or None if unsupported."""
        ...

    def update_secret(self, key: str, value: str, **kwargs) -> bool:
        """Replace an existing secret value (rotation only — explicit overwrite)."""
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
        """Get a feature flag value from the store.

        Returns:
            The value, or ``None`` if the key does not exist.

        Raises:
            SecretStoreUnavailableError: if the store cannot be reached or
                authentication fails. Never return ``None`` for this case.
        """
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


@runtime_checkable
class ICveScanner(Protocol):
    """
    Capability: Integration supports CVE vulnerability scanning.

    Integrations implementing this interface can scan CycloneDX SBOM files
    and return structured vulnerability findings.

    Examples: Trivy, Grype
    """

    def scan_sbom(self, sbom_path, severity_threshold: str = "MEDIUM", timeout: int = 300):
        """Scan a CycloneDX SBOM file for vulnerabilities.

        Returns:
            CveAuditResultModel with findings and severity counts.
        """
        ...


@runtime_checkable
class IGCloudTool(Protocol):
    """
    Capability: Integration provides Google Cloud CLI operations.

    Integrations implementing this interface can check gcloud CLI
    availability, retrieve project context, obtain access tokens,
    and run arbitrary ``gcloud`` subcommands.

    Examples: Google Cloud CLI (gcloud)
    """

    def ensure_available(self) -> tuple:
        """Check that gcloud is installed, authenticated, and has an active project."""
        ...

    def get_project(self):
        """Return active GCP project ID."""
        ...

    def get_access_token(self):
        """Return a cached bearer token from gcloud auth print-access-token."""
        ...


@runtime_checkable
class IAWSTool(Protocol):
    """
    Capability: Integration provides AWS CLI operations.

    Integrations implementing this interface can check AWS CLI
    availability, retrieve identity context (account, region),
    and run arbitrary ``aws`` subcommands.

    Examples: AWS CLI (aws)
    """

    def ensure_available(self) -> tuple:
        """Check that aws is installed and authenticated."""
        ...

    def get_identity(self):
        """Return active identity (Account, UserId, Arn)."""
        ...

    def get_region(self):
        """Return the active AWS region."""
        ...


@runtime_checkable
class IAzureTool(Protocol):
    """
    Capability: Integration provides Azure CLI operations.

    Integrations implementing this interface can check Azure CLI
    availability, retrieve subscription context, obtain access tokens,
    and run arbitrary ``az`` subcommands.

    Examples: Azure CLI (az)
    """

    def ensure_available(self) -> tuple:
        """Check that az is installed and authenticated."""
        ...

    def get_subscription(self):
        """Return active subscription metadata (id, name, tenantId)."""
        ...

    def get_access_token(self, resource: str = "https://management.azure.com"):
        """Return a cached bearer token for the given resource scope."""
        ...


@runtime_checkable
class IIdentityProvider(Protocol):
    """
    Capability: Integration authenticates a human or service to an OIDC/OAuth2-protected
    system via the strata CLI itself (device-code / Authorization Code flow), rather than
    delegating to an already-authenticated external tool.

    This is distinct from ``IAWSTool``/``IAzureTool``/``IGCloudTool``, whose
    ``ensure_available()`` only *checks* whether an external CLI (``az``/``aws``/``gcloud``)
    is already logged in. An ``IIdentityProvider`` integration has no external tool to
    delegate to — strata itself must drive the login and persist the resulting session.

    Not restricted to any single service: a workspace may declare more than one
    ``identity``-capable integration (e.g. one for a strata control plane, another for
    some unrelated OIDC-protected service the CLI needs to call).

    Picked up automatically by ``strata sln doctor --deep`` (via ``check_auth()``) and
    ``strata sln doctor --login`` (via ``login()``) — see ADR-0067.

    Examples: generic OIDC provider (device-code grant), Auth0, GitHub OAuth App
    """

    def check_auth(self) -> tuple:
        """Check whether a cached session is valid, refreshing it silently if possible.

        Returns:
            (True, detail) if a valid session exists (after silent refresh if needed).
            (False, detail) if not authenticated or refresh failed — detail should name
            the fix (e.g. "run with --login to sign in").
        """
        ...

    def login(self) -> tuple:
        """Drive an interactive login (e.g. OIDC device-code flow) and cache the result.

        Returns:
            (True, detail) on success, (False, error) otherwise.
        """
        ...

    def get_access_token(self):
        """Return the current cached bearer token, or None if not authenticated."""
        ...


@runtime_checkable
class IIacSecurityScanner(Protocol):
    """
    Capability: Integration supports IaC static security analysis.

    Integrations implementing this interface can scan Infrastructure-as-Code
    artifacts (Terraform, CloudFormation, Kubernetes, Helm, Dockerfile, etc.)
    for misconfigurations and security policy violations.

    Examples: Checkov
    """

    def scan(self, terraform_dir, framework: str = "terraform", **kwargs):
        """Scan an IaC artifact directory for security findings.

        Returns:
            Structured scan result with findings and severity counts.
        """
        ...


@runtime_checkable
class ICostEstimator(Protocol):
    """
    Capability: Integration supports cost estimation for infrastructure.

    Integrations implementing this interface can estimate monthly costs
    for infrastructure configurations (Terraform plans, resource definitions).

    Examples: Infracost
    """

    def ensure_available(self) -> Tuple[bool, str]:
        """Check if this integration is available and meets version requirements."""
        ...

    def breakdown(self, terraform_path: str, **kwargs) -> Dict[str, Any]:
        """Get cost breakdown for terraform configuration at the given path.

        Args:
            terraform_path: Path to terraform directory (must contain .terraform/)

        Returns:
            Parsed Infracost JSON output with monthly cost estimates per resource.
        """
        ...

    def diff(self, terraform_path: str, plan_file: str, **kwargs) -> Dict[str, Any]:
        """Get cost diff between current state and a terraform plan.

        Args:
            terraform_path: Path to terraform directory
            plan_file: Path to terraform plan JSON file (from terraform plan -json)

        Returns:
            Parsed Infracost JSON output with before/after costs and monthly delta.
        """
        ...


@runtime_checkable
class IDiagramRenderer(Protocol):
    """
    Capability: Integration renders diagram text (e.g. Mermaid) to an image format.

    Integrations implementing this interface convert diagram source text into
    rendered bytes (SVG/PNG) for export from `strata diagram show --format`.

    Examples: Kroki
    """

    def ensure_available(self) -> Tuple[bool, str]:
        """Check if this integration is available and meets version requirements."""
        ...

    def render(self, diagram_source: str, diagram_type: str, output_format: str) -> bytes:
        """Render diagram_source (e.g. Mermaid text) to output_format (e.g. 'svg'/'png') bytes.

        Args:
            diagram_source: Raw diagram text (e.g. Mermaid syntax).
            diagram_type: Diagram language the source is written in (e.g. 'mermaid').
            output_format: Desired output image format (e.g. 'svg', 'png').

        Returns:
            Rendered image bytes.

        Raises:
            RuntimeError: If the renderer is unreachable or returns an error.
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
    "ICveScanner": {
        "description": "CVE vulnerability scanning via CycloneDX SBOM",
        "methods": ["scan_sbom"],
        "examples": ["Trivy", "Grype"],
    },
    "IIacSecurityScanner": {
        "description": "IaC static security analysis (Terraform, CloudFormation, Kubernetes, etc.)",
        "methods": ["scan"],
        "examples": ["Checkov"],
    },
    "ICostEstimator": {
        "description": "Infrastructure cost estimation",
        "methods": ["breakdown", "diff"],
        "examples": ["Infracost"],
    },
    "IDiagramRenderer": {
        "description": "Diagram text (e.g. Mermaid) to image (SVG/PNG) rendering",
        "methods": ["render"],
        "examples": ["Kroki"],
    },
    "IAzureTool": {
        "description": "Azure CLI operations: auth check, subscription context, access tokens, az subcommands",
        "methods": ["ensure_available", "get_subscription", "get_access_token"],
        "examples": ["Azure CLI (az)"],
    },
    "IAWSTool": {
        "description": "AWS CLI operations: auth check, identity context, region, aws subcommands",
        "methods": ["ensure_available", "get_identity", "get_region"],
        "examples": ["AWS CLI (aws)"],
    },
    "IGCloudTool": {
        "description": "Google Cloud CLI operations: auth check, project context, access tokens, gcloud subcommands",
        "methods": ["ensure_available", "get_project", "get_access_token"],
        "examples": ["Google Cloud CLI (gcloud)"],
    },
    "IIdentityProvider": {
        "description": "OIDC/OAuth2 login for the CLI itself: session check, interactive login, bearer token",
        "methods": ["check_auth", "login", "get_access_token"],
        "examples": ["Generic OIDC provider", "Auth0", "GitHub OAuth App"],
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
    "cve_scanner": ICveScanner,
    "iac_security": IIacSecurityScanner,
    "cost": ICostEstimator,
    "azure": IAzureTool,
    "aws": IAWSTool,
    "gcloud": IGCloudTool,
    "identity": IIdentityProvider,
    "diagram_render": IDiagramRenderer,
}


# Valid capability names users can specify in config
VALID_CAPABILITY_NAMES = frozenset(CAPABILITY_MAP.keys()) | {"api", "sync"}


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
    "customcost": "cost",
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
