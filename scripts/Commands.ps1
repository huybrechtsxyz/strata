<#
  Commands.ps1  Manual test reference for xyz-platform CLI
  
  Two sections:
    [FLOW]      End-to-end workflow test  run top to bottom to validate the full session lifecycle
    [REFERENCE] Per-command variations  pick individual lines to test specific flags
#>

$app = ".app"

# ==============================================================================
# [FLOW] End-to-end session lifecycle
# ==============================================================================

New-Item -Path $app -ItemType Directory -Force

.\scripts\Run.ps1 version

.\scripts\Run.ps1 session init   --name platform --work-path $app --editor vscode
.\scripts\Run.ps1 session add    --name xyz_configuration --work-path $app --url "../repo/xyz_configuration"
.\scripts\Run.ps1 session add    --work-path $app --config-file "xyz_configuration/config/xyz-config.yaml"
.\scripts\Run.ps1 session fetch  --work-path $app --dry-run
.\scripts\Run.ps1 session fetch  --work-path $app
.\scripts\Run.ps1 session list   --work-path $app
.\scripts\Run.ps1 session status --work-path $app
.\scripts\Run.ps1 session sync   --work-path $app
.\scripts\Run.ps1 session clean  --work-path $app --dry-run

.\scripts\Run.ps1 tools status --work-path $app

.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/config/xyz-config.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-dc-eu-fr.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-fw-base.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-md-traefik.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-ns-base.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-rx-vm-infra.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-rx-vm-manager.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-rx-vm-worker.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-ws-platform.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/environments/xyz-env-prd.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml

.\scripts\Run.ps1 build run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
.\scripts\Run.ps1 build run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --no-hooks

.\scripts\Run.ps1 build clean --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
.\scripts\Run.ps1 build clean --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml

Remove-Item -Path $app -Recurse -Force

# ==============================================================================
# [REFERENCE] Basic commands
# ==============================================================================

.\scripts\Run.ps1 -h

# version
.\scripts\Run.ps1 version -h
.\scripts\Run.ps1 version
.\scripts\Run.ps1 version --output text
.\scripts\Run.ps1 version --output json
.\scripts\Run.ps1 version --output raw

# help
.\scripts\Run.ps1 help
.\scripts\Run.ps1 help -h
.\scripts\Run.ps1 help terraform
.\scripts\Run.ps1 help tf         # topic not found

# ==============================================================================
# [REFERENCE] session
# ==============================================================================

.\scripts\Run.ps1 session -h

# session init
.\scripts\Run.ps1 session init -h
.\scripts\Run.ps1 session init --name platform --work-path $app
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --output json
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --output text
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --verbose
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --quiet

# session show
.\scripts\Run.ps1 session show -h
.\scripts\Run.ps1 session show --work-path $app
.\scripts\Run.ps1 session show --work-path $app --output json
.\scripts\Run.ps1 session show --work-path $app --output text
.\scripts\Run.ps1 session show --work-path $app --verbose

# session add  url mode
.\scripts\Run.ps1 session add -h
.\scripts\Run.ps1 session add --name xyz_configuration --work-path $app --url "../repo/xyz_configuration"
.\scripts\Run.ps1 session add --name xyz_configuration --work-path $app --url "../repo/xyz_configuration" --output json
.\scripts\Run.ps1 session add --name xyz_configuration --work-path $app --url "../repo/xyz_configuration" --output text
.\scripts\Run.ps1 session add --name xyz_configuration --work-path $app --url "../repo/xyz_configuration" --verbose
.\scripts\Run.ps1 session add --name xyz_configuration --work-path $app --url "../repo/xyz_configuration" --quiet
.\scripts\Run.ps1 session add --name xyz_svc_traefik   --work-path $app --url "https://github.com/huybrechtsxyz/xyz-traefik.git" --branch main

# session add  config-file / config-path mode
.\scripts\Run.ps1 session add --work-path $app --config-file "repo/xyz_configuration/config/xyz-config.yaml"
.\scripts\Run.ps1 session add --name xyz_configuration --work-path $app --config-file "repo/xyz_configuration/config/xyz-config.yaml"
.\scripts\Run.ps1 session add --work-path $app --config-path "repo/xyz_configuration/config"
.\scripts\Run.ps1 session add --work-path $app --config-file "repo/xyz_configuration/config/xyz-config.yaml" --output json
.\scripts\Run.ps1 session add --work-path $app --config-file "repo/xyz_configuration/config/xyz-config.yaml" --output text
.\scripts\Run.ps1 session add --work-path $app --config-file "repo/xyz_configuration/config/xyz-config.yaml" --verbose
.\scripts\Run.ps1 session add --work-path $app --config-file "repo/xyz_configuration/config/xyz-config.yaml" --quiet

# session fetch
.\scripts\Run.ps1 session fetch -h
.\scripts\Run.ps1 session fetch --work-path $app
.\scripts\Run.ps1 session fetch --work-path $app --dry-run
.\scripts\Run.ps1 session fetch --work-path $app --force
.\scripts\Run.ps1 session fetch --work-path $app --name xyz_infrastructure
.\scripts\Run.ps1 session fetch --work-path $app --output json
.\scripts\Run.ps1 session fetch --work-path $app --output text
.\scripts\Run.ps1 session fetch --work-path $app --verbose
.\scripts\Run.ps1 session fetch --work-path $app --quiet

# session sync
.\scripts\Run.ps1 session sync -h
.\scripts\Run.ps1 session sync --work-path $app
.\scripts\Run.ps1 session sync --work-path $app --force
.\scripts\Run.ps1 session sync --work-path $app --output json
.\scripts\Run.ps1 session sync --work-path $app --output text
.\scripts\Run.ps1 session sync --work-path $app --verbose
.\scripts\Run.ps1 session sync --work-path $app --quiet

# session list
.\scripts\Run.ps1 session list -h
.\scripts\Run.ps1 session list --work-path $app
.\scripts\Run.ps1 session list --work-path $app --output json
.\scripts\Run.ps1 session list --work-path $app --output text
.\scripts\Run.ps1 session list --work-path $app --verbose

# session status
.\scripts\Run.ps1 session status -h
.\scripts\Run.ps1 session status --work-path $app
.\scripts\Run.ps1 session status --work-path $app --output json
.\scripts\Run.ps1 session status --work-path $app --output text
.\scripts\Run.ps1 session status --work-path $app --verbose

# session logs
.\scripts\Run.ps1 session logs -h
.\scripts\Run.ps1 session logs --work-path $app
.\scripts\Run.ps1 session logs --work-path $app --lines 100
.\scripts\Run.ps1 session logs --work-path $app --level ERROR
.\scripts\Run.ps1 session logs --work-path $app --last-exec
.\scripts\Run.ps1 session logs --work-path $app --output json
.\scripts\Run.ps1 session logs --work-path $app --output text
.\scripts\Run.ps1 session logs --work-path $app --verbose

# session remove  repo mode
.\scripts\Run.ps1 session remove -h
.\scripts\Run.ps1 session remove --name my-repo --work-path $app
.\scripts\Run.ps1 session remove --name my-repo --work-path $app --dry-run
.\scripts\Run.ps1 session remove --name my-repo --work-path $app --delete
.\scripts\Run.ps1 session remove --name my-repo --work-path $app --delete --dry-run
.\scripts\Run.ps1 session remove --name my-repo --work-path $app --output json
.\scripts\Run.ps1 session remove --name my-repo --work-path $app --quiet

# session remove  config mode
.\scripts\Run.ps1 session remove --config --name my-config --work-path $app
.\scripts\Run.ps1 session remove --config --name my-config --work-path $app --dry-run
.\scripts\Run.ps1 session remove --config --name my-config --work-path $app --output json
.\scripts\Run.ps1 session remove --config --name my-config --work-path $app --quiet

# session clean
.\scripts\Run.ps1 session clean -h
.\scripts\Run.ps1 session clean --work-path $app
.\scripts\Run.ps1 session clean --work-path $app --dry-run
.\scripts\Run.ps1 session clean --work-path $app --no-logs
.\scripts\Run.ps1 session clean --work-path $app --output json
.\scripts\Run.ps1 session clean --work-path $app --quiet

# session schemas
.\scripts\Run.ps1 session schemas -h
.\scripts\Run.ps1 session schemas --work-path $app
.\scripts\Run.ps1 session schemas --work-path $app --editor vscode
.\scripts\Run.ps1 session schemas --work-path $app --output-dir .xyz-platform/schemas
.\scripts\Run.ps1 session schemas --work-path $app --editor vscode --output-dir .xyz-platform/schemas
.\scripts\Run.ps1 session schemas --work-path $app --output json
.\scripts\Run.ps1 session schemas --work-path $app --output text
.\scripts\Run.ps1 session schemas --work-path $app --verbose
.\scripts\Run.ps1 session schemas --work-path $app --quiet
.\scripts\Run.ps1 session schemas --work-path $app --editor vscode --quiet

# ==============================================================================
# [REFERENCE] tools
# ==============================================================================

.\scripts\Run.ps1 tools -h

# tools status
.\scripts\Run.ps1 tools status -h
.\scripts\Run.ps1 tools status
.\scripts\Run.ps1 tools status --work-path $app
.\scripts\Run.ps1 tools status --work-path $app --output json
.\scripts\Run.ps1 tools status --work-path $app --output text
.\scripts\Run.ps1 tools status --work-path $app --verbose

# ==============================================================================
# [REFERENCE] validate
# ==============================================================================

.\scripts\Run.ps1 validate -h
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/config/xyz-config.yaml
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-dc-eu-fr.yaml --no-hooks --output json
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-fw-base.yaml --no-hooks --output text
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-md-traefik.yaml --no-hooks --verbose
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-ns-base.yaml --no-hooks --quiet
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-rx-vm-infra.yaml --no-hooks
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-rx-vm-manager.yaml --no-hooks
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-rx-vm-worker.yaml --no-hooks
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/stack/xyz-ws-platform.yaml --no-hooks
.\scripts\Run.ps1 validate --work-path $app --file @xyz_configuration/environments/xyz-env-prd.yaml --no-hooks
.\scripts\Run.ps1 validate --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --no-hooks

# ==============================================================================
# [REFERENCE] build
# ==============================================================================

.\scripts\Run.ps1 build -h

.\scripts\Run.ps1 build run -h
.\scripts\Run.ps1 build run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
.\scripts\Run.ps1 build run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml
.\scripts\Run.ps1 build run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --no-hooks
.\scripts\Run.ps1 build run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --output json
.\scripts\Run.ps1 build run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --output text
.\scripts\Run.ps1 build run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --verbose
.\scripts\Run.ps1 build run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --quiet

.\scripts\Run.ps1 build clean -h
.\scripts\Run.ps1 build clean --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml
.\scripts\Run.ps1 build clean --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
.\scripts\Run.ps1 build clean --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --no-logs
.\scripts\Run.ps1 build clean --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --output json
.\scripts\Run.ps1 build clean --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --quiet

# ==============================================================================
# [REFERENCE] deploy
# ==============================================================================

.\scripts\Run.ps1 deploy -h

.\scripts\Run.ps1 deploy run -h
.\scripts\Run.ps1 deploy run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
.\scripts\Run.ps1 deploy run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml
.\scripts\Run.ps1 deploy run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --no-hooks
.\scripts\Run.ps1 deploy run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --output json
.\scripts\Run.ps1 deploy run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --output text
.\scripts\Run.ps1 deploy run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --verbose
.\scripts\Run.ps1 deploy run --work-path $app --file @xyz_infrastructure/deployments/xyz-deploy-prd.yaml --quiet
