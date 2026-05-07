# config/

Live workspace configuration for the XYZ Platform deployment. This folder is the primary `work_path` consumed by the CLI — it contains the YAML documents that describe what the platform runs and where.

Each sub-folder is an independent xyz workspace (has its own `.platform/` state directory).

## Sub-workspaces

### `xyz-configuration/`

Platform configuration documents: providers, namespaces, firewalls, modules, workspaces, and the cluster stack. These files are the source of truth for what services run on the platform.

```
xyz-configuration/
├── config/             # CLI preferences and logging profile
│   ├── xyz-config.yaml
│   └── xyz-logging.yaml
├── environments/       # Environment overlays (prd, stg, …)
│   └── xyz-env-prd.yaml
└── stack/              # Per-service configuration documents
    ├── xyz-dc-eu-fr.yaml    # kind: provider  — Kamatera EU-FR datacenter
    ├── xyz-fw-base.yaml     # kind: firewall   — base firewall rules
    ├── xyz-md-traefik.yaml  # kind: module     — Traefik ingress module
    ├── xyz-ns-base.yaml     # kind: namespace  — base namespace
    ├── xyz-rx-vm-infra.yaml # kind: resource   — infra VM
    ├── xyz-rx-vm-manager.yaml
    ├── xyz-rx-vm-worker.yaml
    └── xyz-ws-platform.yaml # kind: workspace  — platform workspace
```

### `xyz-infrastructure/`

Terraform infrastructure and deployment configuration for the underlying cloud resources.

```
xyz-infrastructure/
├── deployments/        # Deployment YAML documents consumed by xyz deploy
│   └── xyz-deploy-prd.yaml
├── scripts/            # Operational helper scripts
│   └── deploy.ps1
└── terraform/          # Terraform root module
    ├── main.tf
    ├── variables.tf
    ├── outputs.tf
    ├── locals.tf
    └── backends/       # Per-environment backend configurations
```

### `xyz-svc-traefik/`

Static configuration for the Traefik reverse proxy service running on the platform.

## Usage

```powershell
# Validate all configuration documents
uv run xyz-platform validate --work-path config/xyz-configuration

# Check workspace status
uv run xyz-platform status --work-path config/xyz-configuration
```
