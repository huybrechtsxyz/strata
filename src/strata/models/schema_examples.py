#!/usr/bin/env python3
"""Valid example documents per kind, exported as each schema's root `examples`.

Python data rather than files because the installed wheel does not ship `config/`. Every example is
validated against its model and against its own exported schema by
`tests/strata/models/test_schema_export.py`, so one that drifts fails a test instead of misleading a reader.

Keep each example to what the kind is for: a minimal valid document first, then at most one typical one.
Names should read as obvious placeholders and must be valid `PlatformName`s.
"""

from typing import Any

from strata.models.common_models import PlatformKind

_API = "strata.huybrechts.xyz/v2"

EXAMPLES: dict[PlatformKind, list[dict[str, Any]]] = {
    PlatformKind.SOLUTION: [
        {
            "apiVersion": _API,
            "kind": "solution",
            "meta": {"name": "my-solution"},
            "spec": {"configuration": "config"},
        },
        {
            "apiVersion": _API,
            "kind": "solution",
            "meta": {"name": "my-solution"},
            "spec": {
                "configuration": "config",
                "discovery": {"exclude": ["templates/**"]},
                "remotes": [
                    {"name": "infra", "type": "git", "url": "https://example.com/org/infra.git", "reference": "v1.0.0"},
                    {"name": "charts", "type": "helm", "url": "https://charts.example.com"},
                ],
            },
        },
    ],
    PlatformKind.CONFIGURATION: [
        {
            "apiVersion": _API,
            "kind": "configuration",
            "meta": {"name": "my-config"},
            "spec": {"providers": ["azure"], "topologies": ["kubernetes"]},
        },
        {
            "apiVersion": _API,
            "kind": "configuration",
            "meta": {"name": "my-config"},
            "spec": {
                "providers": ["azure"],
                "topologies": ["kubernetes"],
                "security": {"allowed_secret_stores": ["azure-keyvault"]},
                "paths": [
                    {
                        "name": "tenant-path",
                        "scope": "deploy/tenants/**",
                        "pattern": "deploy/tenants/{code}",
                        "filename_pattern": "tenant.yaml",
                        "resolves": "tenant",
                    }
                ],
                "cve_allowed": [
                    {"id": "CVE-2024-0001", "reason": "Not reachable in our deployment", "expires": "2027-01-31"}
                ],
            },
        },
    ],
    PlatformKind.PROVIDERCONFIG: [
        {
            "apiVersion": _API,
            "kind": "providerconfig",
            "meta": {"name": "azure"},
            "spec": {
                "description": "Microsoft Azure provider type",
                "regions": [
                    {"name": "westeurope", "geography": "europe"},
                    {"name": "northeurope", "geography": "europe"},
                ],
                "resources": [
                    {"name": "storage_account", "category": "storage"},
                    {"name": "key_vault", "category": "security"},
                ],
            },
        }
    ],
    PlatformKind.TOPOLOGYCONFIG: [
        {
            "apiVersion": _API,
            "kind": "topologyconfig",
            "meta": {"name": "kubernetes"},
            "spec": {
                "description": "Kubernetes cluster",
                "components": [
                    {"role": "control-plane", "required": True, "min_count": 1, "max_count": 1},
                    {"role": "worker", "required": False, "min_count": 0, "max_count": 0},
                ],
            },
        }
    ],
    PlatformKind.ATTRIBUTECONFIG: [
        {
            "apiVersion": _API,
            "kind": "attributeconfig",
            "meta": {"name": "azure-storage-account-configuration"},
            "spec": {
                "target": {
                    "kind": "resource",
                    "block": "configuration",
                    "view": "document",
                    "selector": {"spec.provider_type": "azure", "spec.resource_type": "storage_account"},
                },
                "schema": {
                    "type": "object",
                    "required": ["account_tier"],
                    "properties": {"account_tier": {"type": "string", "enum": ["Standard", "Premium"]}},
                },
            },
        }
    ],
    PlatformKind.PROVIDER: [
        {
            "apiVersion": _API,
            "kind": "provider",
            "meta": {"name": "azure-main"},
            "spec": {"type": "azure", "region": "westeurope"},
        },
        {
            "apiVersion": _API,
            "kind": "provider",
            "meta": {"name": "azure-main"},
            "spec": {
                "type": "azure",
                "region": "westeurope",
                "authentication": {
                    "method": "oauth2",
                    "oauth2": {
                        "client_id": "azure_client_id",
                        "client_secret": "azure_client_secret",
                        "tenant_id": "azure_tenant_id",
                    },
                },
                "configuration": {"skip_provider_registration": True},
                "default_tags": {"owner": "platform"},
            },
        },
    ],
    PlatformKind.RESOURCE: [
        {
            "apiVersion": _API,
            "kind": "resource",
            "meta": {"name": "storage-account"},
            "spec": {
                "provider_type": "azure",
                "resource_type": "storage_account",
                "category": "storage",
                "configuration": {"account_tier": "Standard"},
            },
        },
        {
            "apiVersion": _API,
            "kind": "resource",
            "meta": {"name": "app-vm"},
            "spec": {
                "provider_type": "azure",
                "resource_type": "virtual_machine",
                "category": "compute",
                "storage": {
                    "disks": [{"name": "data", "size": 100, "label": "${.name}-data", "mount": "/data"}],
                    "volumes": [{"name": "app", "path": "/data/app"}],
                },
                "default_tags": {"owner": "platform"},
            },
        },
    ],
    PlatformKind.FIREWALL: [
        {
            "apiVersion": _API,
            "kind": "firewall",
            "meta": {"name": "web"},
            "spec": {
                "defaults": [
                    {"direction": "in", "permission": "deny"},
                    {"direction": "out", "permission": "allow"},
                ],
                "allow": [{"name": "https", "direction": "in", "proto": "tcp", "port": 443, "from": "0.0.0.0/0"}],
            },
        },
        {
            "apiVersion": _API,
            "kind": "firewall",
            "meta": {"name": "web"},
            "spec": {
                "allow": [
                    {"name": "ssh-admin", "direction": "in", "proto": "tcp", "port": 22, "from": "${var:admin_cidr}"},
                    {"name": "ping", "direction": "in", "proto": "icmp"},
                ],
                "deny": [{"name": "block-telnet", "direction": "in", "proto": "tcp", "port": 23}],
            },
        },
    ],
    PlatformKind.DNS: [
        {
            "apiVersion": _API,
            "kind": "dns",
            "meta": {"name": "example"},
            "spec": {"zones": [{"name": "example.com"}]},
        },
        {
            "apiVersion": _API,
            "kind": "dns",
            "meta": {"name": "example"},
            "spec": {
                "provider": "cloudflare",
                "zones": [
                    {
                        "name": "example.com",
                        "ttl": 3600,
                        "records": [
                            {"name": "@", "type": "A", "value": "203.0.113.10"},
                            {"name": "www", "type": "CNAME", "value": "example.com."},
                            {"name": "@", "type": "MX", "value": "mail.example.com.", "priority": 10},
                            {"name": "api", "type": "A", "value": "${var:api_ip}"},
                        ],
                    }
                ],
            },
        },
    ],
    PlatformKind.NETWORK: [
        {
            "apiVersion": _API,
            "kind": "network",
            "meta": {"name": "main"},
            "spec": {
                "networks": [
                    {
                        "name": "hub",
                        "address_space": ["10.0.0.0/16"],
                        "subnets": [{"name": "default", "cidr": "10.0.1.0/24"}],
                    }
                ]
            },
        },
        {
            "apiVersion": _API,
            "kind": "network",
            "meta": {"name": "main"},
            "spec": {
                "networks": [
                    {
                        "name": "hub",
                        "address_space": ["10.0.0.0/16"],
                        "subnets": [
                            {"name": "app", "cidr": "10.0.1.0/24"},
                            {"name": "data", "cidr": "10.0.2.0/24"},
                        ],
                        "peerings": [{"name": "hub-to-spoke", "target": "spoke"}],
                        "default_tags": {"environment": "prd"},
                    },
                    {
                        "name": "spoke",
                        "address_space": ["10.1.0.0/16"],
                        "subnets": [{"name": "app", "cidr": "10.1.1.0/24"}],
                    },
                ]
            },
        },
    ],
    PlatformKind.MODULE: [
        {
            "apiVersion": _API,
            "kind": "module",
            "meta": {"name": "authentik"},
            "spec": {
                "type": "helm",
                "source": {
                    "chart_repository": "https://charts.goauthentik.io",
                    "chart_name": "authentik",
                    "chart_version": "2024.12.0",
                },
            },
        },
        {
            "apiVersion": _API,
            "kind": "module",
            "meta": {"name": "app"},
            "spec": {
                "type": "compose",
                "source": {"source_path": "modules/app"},
                "services": [
                    {"name": "redis", "image": "redis:7-alpine"},
                    {
                        "name": "server",
                        "image": "ghcr.io/example/app:1.0.0",
                        "ports": ["8080:80"],
                        "environment": [
                            {"key": "DB_PASSWORD", "value": "${secret:DB_PASSWORD}"},
                            {"key": "TZ", "value": "Europe/Brussels"},
                        ],
                        "depends_on": ["redis"],
                        "mounts": [{"name": "data", "volume_ref": "app-data", "target_path": "/data"}],
                    },
                ],
            },
        },
    ],
    PlatformKind.NAMESPACE: [
        {
            "apiVersion": _API,
            "kind": "namespace",
            "meta": {"name": "apps"},
            "spec": {"modules": [{"name": "authentik", "module": "authentik"}]},
        },
        {
            "apiVersion": _API,
            "kind": "namespace",
            "meta": {"name": "apps"},
            "spec": {
                "type": "shared",
                "modules": [
                    {"name": "authentik", "module": "authentik"},
                    {"name": "app", "module": "app", "enabled": False},
                ],
                "default_labels": {"team": "platform"},
            },
        },
    ],
    PlatformKind.WORKSPACE: [
        {
            "apiVersion": _API,
            "kind": "workspace",
            "meta": {"name": "main"},
            "spec": {
                "providers": ["azure-main"],
                "provisioners": [{"name": "infra", "tool": "terraform", "source": {"source_path": "terraform/main"}}],
                "resources": [{"name": "storage", "resource": "storage-account"}],
                "execution": [{"name": "infra", "provisioner": "infra", "resources": ["storage"]}],
            },
        },
        {
            "apiVersion": _API,
            "kind": "workspace",
            "meta": {"name": "main"},
            "spec": {
                "providers": [{"slot": "primary", "provider": "azure-main"}],
                "provisioners": [
                    {
                        "name": "infra",
                        "tool": "terraform",
                        "source": {"source_path": "terraform/main"},
                        "backend": {
                            "type": "azurerm",
                            "configuration": {"storage_account_name": "${var:state_account}"},
                        },
                    },
                    {
                        "name": "apps",
                        "tool": "argocd",
                        "gitops": {"remote": "gitops", "output_file": "apps/values.yaml"},
                    },
                ],
                "resources": [
                    {"name": "cluster", "resource": "aks-cluster", "role": "control-plane"},
                    {
                        "name": "workers",
                        "resource": "aks-nodes",
                        "role": "worker",
                        "count": 3,
                        "firewalls": ["web"],
                        "subnet": {"network": "main", "subnet": "app"},
                    },
                ],
                "namespaces": ["apps"],
                "firewalls": ["web"],
                "dns_zones": ["example"],
                "networks": ["main"],
                "topology": [
                    {
                        "name": "aks",
                        "type": "kubernetes",
                        "components": [{"resource": "cluster"}, {"resource": "workers"}],
                        "namespaces": [{"namespace": "apps"}],
                    }
                ],
                "execution": [
                    {"name": "infra", "provisioner": "infra", "resources": ["cluster", "workers"], "scope": "infra"},
                    {
                        "name": "apps",
                        "provisioner": "apps",
                        "namespaces": ["apps"],
                        "depends_on": ["infra"],
                        "scope": "apps",
                    },
                ],
                "cve_policy": {"max_severity": "HIGH"},
            },
        },
    ],
    PlatformKind.INTEGRATION: [
        {
            "apiVersion": _API,
            "kind": "integration",
            "meta": {"name": "terraform"},
            "spec": {"type": "terraform", "capabilities": ["infrastructure"]},
        },
        {
            "apiVersion": _API,
            "kind": "integration",
            "meta": {"name": "azure-keyvault"},
            "spec": {
                "type": "azure-keyvault",
                "capabilities": ["secrets", "key_value"],
                "endpoints": {"address": "https://example.vault.azure.net"},
                "authentication": {"method": "cli", "cli": {}},
            },
        },
    ],
    PlatformKind.ENVIRONMENT: [
        {
            "apiVersion": _API,
            "kind": "environment",
            "meta": {"name": "prd"},
            "spec": {"variables": [{"key": "region", "store": "constant", "value": "westeurope"}]},
        },
        {
            "apiVersion": _API,
            "kind": "environment",
            "meta": {"name": "prd"},
            "spec": {
                "properties": {"deploy_tier": "prd"},
                "variables": [
                    {"key": "region", "store": "constant", "value": "westeurope"},
                    {"key": "replicas", "store": "constant", "value": 3, "type": "number"},
                    {"key": "api_image_tag", "store": "artifact", "value": "api", "field": "image_tag"},
                ],
                "secrets": [
                    {
                        "key": "db_password",
                        "store": "azure-keyvault",
                        "value": "db-password",
                        "generate": {"type": "password", "length": 32},
                        "rotate": {"max_age": 90, "policy": "rotate"},
                    },
                    {"key": "admin_token", "store": "environment", "value": "ADMIN_TOKEN"},
                ],
                "features": [{"key": "new_ui", "store": "constant", "value": True}],
            },
        },
    ],
    PlatformKind.TENANT: [
        {
            "apiVersion": _API,
            "kind": "tenant",
            "meta": {"name": "c0001"},
            "spec": {"display_name": "Acme Corporation"},
        },
        {
            "apiVersion": _API,
            "kind": "tenant",
            "meta": {"name": "c0001"},
            "spec": {
                "display_name": "Acme Corporation",
                "geographies": ["europe"],
                "onboarded": "2026-03-15",
                "environments": ["tenant-defaults"],
                "properties": {"deploy_tier": "prd"},
                "configuration": {"crm_id": "12345"},
            },
        },
    ],
    PlatformKind.DEPLOYMENT: [
        {
            "apiVersion": _API,
            "kind": "deployment",
            "meta": {"name": "prd"},
            "spec": {"workspace": "main", "environments": ["prd"]},
        },
        {
            "apiVersion": _API,
            "kind": "deployment",
            "meta": {"name": "prd-c0001"},
            "spec": {
                "extends": "prd-base",
                "tenant": "c0001",
                "environments": ["prd-c0001"],
                "version": "prd",
                "providers": [{"slot": "primary", "provider": "azure-north"}],
                "stages": [{"step": "infra", "timeouts": {"apply": 3600}}],
            },
        },
    ],
    PlatformKind.VERSION: [
        {
            "apiVersion": _API,
            "kind": "version",
            "meta": {"name": "prd"},
            "spec": {"pins": {"images": {"caddy": "caddy:2-alpine"}}},
        },
        {
            "apiVersion": _API,
            "kind": "version",
            "meta": {"name": "prd"},
            "spec": {
                "workspace": "main",
                "promotion": {"ring": "prd", "order": 3},
                "pins": {
                    "images": {
                        "db": {
                            "version": "docker.io/library/postgres:16-alpine",
                            "status": "held",
                            "available": "18.6-alpine",
                            "reason": "postgres majors need pg_upgrade or a dump and restore",
                            "reviewed": "2026-09-08",
                        }
                    },
                    "charts": {"authentik": "2024.12.0"},
                    "artifacts": {"api": "1.4.2"},
                },
            },
        },
    ],
    PlatformKind.ARTIFACT: [
        {
            "apiVersion": _API,
            "kind": "artifact",
            "meta": {"name": "api"},
            "spec": {"image_name": "example/api"},
        },
        {
            "apiVersion": _API,
            "kind": "artifact",
            "meta": {"name": "api"},
            "spec": {
                "image_name": "example/api",
                "image_tag": "1.4.2",
                "registry": "registry.example.com",
                "description": "The public API image",
            },
        },
    ],
    PlatformKind.ASSET: [
        {
            "apiVersion": _API,
            "kind": "asset",
            "meta": {"name": "api-default"},
            "spec": {"source_path": "assets/api/openapi.json"},
        },
        {
            "apiVersion": _API,
            "kind": "asset",
            "meta": {"name": "c0001-api"},
            "spec": {
                "location": {"url": "https://example.com/c0001/assets.git", "reference": "main"},
                "source_path": "assets/{deploy_tier}/openapi/*.json",
            },
        },
    ],
}
