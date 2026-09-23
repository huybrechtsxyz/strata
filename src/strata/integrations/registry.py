#!/usr/bin/env python3
"""One lean, lazily-loaded registry (ADR-0021 D2): `type` string -> `Integration` class.

Collapses v1's `IntegrationFactory` (type -> class) + `IntegrationRegistry`
(name -> instance, process-wide singleton) + `IntegrationService` (loads from
Configuration, capability preflight) into one function. The lazy import is
kept — genuinely useful, not bloat: it's the only thing standing between
"add a Vault integration" and every `strata` invocation hard-depending on
`hvac`.

No instance cache lives here (ADR-0021 D3) — a process-wide singleton needs
`.reset()` calls sprinkled through a test suite to avoid cross-test
contamination (v1's own `IntegrationRegistry.reset()`), for a problem
(auth-reuse across a long deploy) `strata`'s short-lived commands don't have
yet. Callers that want reuse across a batch keep their own short-lived cache
(e.g. `value_controller.resolve_values()`), the same pattern `values get`
already used before this registry existed.
"""

from importlib import import_module
from importlib.metadata import entry_points
from typing import cast

from strata.integrations.base import Integration
from strata.integrations.errors import IntegrationError
from strata.models.integration_model import IntegrationModel

#: Entry point group a third-party package registers a custom integration
#: class under (ADR-0021 D10) — e.g. `[project.entry-points."strata.integrations"]`.
ENTRY_POINT_GROUP = "strata.integrations"

#: Built-in types: `type` string -> (module path, class name). Imported lazily,
#: only when `get()` actually needs the class. Every addition is one entry +
#: one small file — this dict's shape does not change.
_KNOWN: dict[str, tuple[str, str]] = {
    "infisical": ("strata.integrations.infisical_resolver", "InfisicalResolver"),
    "azure-keyvault": ("strata.integrations.azure_keyvault_resolver", "AzureKeyVaultResolver"),
    "azure-appconfig": ("strata.integrations.azure_appconfig_resolver", "AzureAppConfigResolver"),
    "terraform": ("strata.integrations.terraform", "TerraformIntegration"),
    "compose": ("strata.integrations.compose", "ComposeIntegration"),
    "helm": ("strata.integrations.helm", "HelmIntegration"),
    # ansible/git/bicep/opentofu/vault/consul/etcd/bitwarden/flagsmith/cost/
    # cve/siem/identity as each gets a real v2 consumer (ADR-0021 Phase 7+).
}

#: v1 types not yet ported to v2. Only used so "not built yet" (a real, known
#: type) reads differently from "not real" (a typo) in `get()`'s error.
_KNOWN_V1_TYPES = frozenset(
    {
        "opentofu",
        "ansible",
        "git",
        "bicep",
        "vault",
        "consul",
        "etcd",
        "bitwarden",
        "flagsmith",
        "docker",
        "docker-compose",
        "infracost",
        "kroki",
        "checkov",
        "opa",
        "azure-cli",
        "aws-cli",
        "gcloud-cli",
        "sentinel",
        "elk",
        "otel",
        "splunk",
        "webhook",
        "syslog",
    }
)


class IntegrationNotFoundError(IntegrationError):
    """No class is registered for a requested `type` — neither built-in nor an installed entry point."""


def get(integration_type: str, config: IntegrationModel | None = None) -> Integration:
    """Construct the `Integration` registered for `integration_type`.

    Args:
        integration_type: A `type` string (e.g. "terraform", "infisical") —
            matches `ProvisionerModel.tool`/a store's `store` field.
        config: The `Integration` document configuring this instance, when
            the solution declares one (D1's transport selection reads it).
            Omitted for a tool that needs nothing but PATH/environment
            variables — finding the document to pass is the *caller's* job:
            this module sits below `strata.services` and cannot reach the
            document index.

    Returns:
        A new instance — never cached or reused here (see module docstring).

    Raises:
        IntegrationNotFoundError: `integration_type` is registered neither
            as a built-in nor as an installed entry point, or collides
            between the two.
    """
    integration_class = _resolve_class(integration_type)
    return integration_class(config)


def _resolve_class(integration_type: str) -> type[Integration]:
    plugins = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == integration_type]

    if integration_type in _KNOWN:
        if plugins:
            raise IntegrationNotFoundError(
                f"'{integration_type}' is a built-in integration type; an installed plugin may not reuse "
                "a built-in name."
            )
        module_path, class_name = _KNOWN[integration_type]
        module = import_module(module_path)
        return cast(type[Integration], getattr(module, class_name))

    if plugins:
        if len(plugins) > 1:
            raise IntegrationNotFoundError(
                f"multiple installed plugins register type '{integration_type}': "
                f"{[plugin.value for plugin in plugins]}."
            )
        return cast(type[Integration], plugins[0].load())

    hint = " (a v1 integration type not yet ported to v2)" if integration_type in _KNOWN_V1_TYPES else ""
    raise IntegrationNotFoundError(f"no integration registered for type '{integration_type}'{hint}.")
