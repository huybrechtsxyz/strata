# Gap 5 — Terraform output capture for the registry

- Status: completed
- Date: 2026-08-06
- Parent: [ADR 0063 — Team-owned Terraform module support](0063-vct-owned-terraform-module-support.md)

## Problem

The registration contract expects every deployment to produce a JSON document
describing what infrastructure it created — resource IDs, endpoints, connection strings,
etc. This data is exactly what Terraform's `output {}` blocks declare.

Today, teams either:
1. Write a lifecycle script (`deploy_apply_after`) that runs `terraform output -json`
   and formats it for the registry.
2. Skip registration entirely, leaving gaps in the service catalog.

Both options are unreliable. Option 1 duplicates logic across every team. Option 2
defeats the purpose of the registry.

## Current state in strata

Strata **already captures outputs** in two places:

1. **`TerraformDeployer.collect_outputs()`** — Runs `terraform output -json` after apply,
   splits into non-sensitive and sensitive dicts, stores in `ResolvedValues.stage_outputs`.
2. **`TerraformDeployer._write_outputs_cache()`** — Writes `<stage>.tf-outputs.json` to
   the build directory for offline access.

And the **deployment manifest** already has:

3. **`ManifestStageModel.outputs`** — Non-sensitive outputs embedded in the manifest.
4. **`ManifestStageModel.outputs_artifact`** — Reference to a durable outputs file
   (`ManifestOutputsReferenceModel` with path, stage, version, timestamp).

**What's missing:**
- A standardized, registry-consumable outputs artifact with a defined schema.
- Configuration to control what gets captured and how it's formatted.
- Automatic population — currently the manifest records outputs only if the deploy
  pipeline is wired to do so (it is for stages, but not as a standalone artifact).
- A contract definition that registries can rely on.

## Design

### Outputs artifact

After a successful deploy, strata produces a `deployment-outputs.json` file alongside
the deployment manifest:

```json
{
  "apiVersion": "strata.huybrechts.xyz/v1",
  "kind": "deployment-outputs",
  "meta": {
    "name": "deploy-prd",
    "deployment": "my-workspace-prd",
    "version": "1.5.0",
    "deployed_at": "2026-08-06T14:30:00Z",
    "workspace": "my-workspace",
    "environment": "production",
    "tenant": "contoso"
  },
  "outputs": {
    "platform_baseline": {
      "vnet_id": "/subscriptions/.../virtualNetworks/vnet-prod",
      "subnet_ids": {
        "default": "/subscriptions/.../subnets/snet-default",
        "aks": "/subscriptions/.../subnets/snet-aks"
      },
      "cluster_id": "/subscriptions/.../managedClusters/aks-prod",
      "cluster_fqdn": "aks-prod-abc123.hcp.westeurope.azmk8s.io"
    },
    "team_module": {
      "app_gateway_ip": "20.93.45.67",
      "key_vault_uri": "https://kv-team-prod.vault.azure.net/",
      "storage_account_name": "stteamprod001"
    }
  },
  "sensitive_keys": ["platform_baseline.cluster_admin_password"],
  "provenance": {
    "manifest_path": "build/deploy-prd/manifest.yaml",
    "stages_completed": ["platform_baseline", "team_module"]
  }
}
```

### Schema model

```python
# src/strata/models/deployment_outputs_model.py

class DeploymentOutputsMetaModel(PlatformBaseModel):
    """Metadata for the outputs artifact."""
    name: PlatformName
    deployment: str
    version: str
    deployed_at: str  # ISO-8601
    workspace: str
    environment: Optional[str] = None
    tenant: Optional[str] = None


class DeploymentOutputsModel(PlatformBaseModel):
    """Registry-consumable outputs artifact produced after a successful deploy."""
    apiVersion: PlatformVersion = Field(default="strata.huybrechts.xyz/v1")
    kind: Literal["deployment-outputs"] = "deployment-outputs"
    meta: DeploymentOutputsMetaModel
    outputs: Dict[str, Dict[str, Any]] = Field(
        description="Outputs keyed by provisioner/stage name, then by output name"
    )
    sensitive_keys: List[str] = Field(
        default_factory=list,
        description="Dot-notation paths of sensitive outputs (values omitted from this file)"
    )
    provenance: Dict[str, Any] = Field(
        default_factory=dict,
        description="Traceability: manifest path, completed stages, commit SHAs"
    )
```

### Configuration

Control output capture via the deployment or workspace YAML:

```yaml
# workspace.yaml or deployment.yaml
spec:
  outputs:
    enabled: true              # default: true
    path: outputs.json         # default: deployment-outputs.json
    format: json               # json (default) | yaml
    include_sensitive: false    # default: false (sensitive keys listed but values omitted)
    stages:                    # optional: limit to specific stages
      - platform_baseline
      - team_module
    transform:                 # optional: rename/restructure for registry contract
      cluster_endpoint: platform_baseline.cluster_fqdn
      app_ip: team_module.app_gateway_ip
```

### Model addition

```python
# src/strata/models/workspace_model.py (or deployment_model.py)

class OutputCaptureModel(PlatformBaseModel):
    """Configuration for automatic output capture after deploy."""

    enabled: bool = Field(default=True, description="Enable automatic output capture")
    path: str = Field(
        default="deployment-outputs.json",
        description="Output file path relative to the build directory"
    )
    format: Literal["json", "yaml"] = Field(default="json")
    include_sensitive: bool = Field(
        default=False,
        description="Include sensitive output values. When false, sensitive keys are listed but values are omitted."
    )
    stages: Optional[List[str]] = Field(
        None,
        description="Limit capture to these stages. When omitted, all stages are captured."
    )
    transform: Optional[Dict[str, str]] = Field(
        None,
        description="Key mapping for registry contract. Values are dot-notation source paths (stage.output_name)."
    )
```

### Capture flow

```
deploy run
  ├── stage: platform_baseline
  │   ├── apply ✓
  │   └── collect_outputs() → {vnet_id: ..., cluster_id: ...}
  │
  ├── stage: team_module
  │   ├── apply ✓
  │   └── collect_outputs() → {app_gateway_ip: ..., key_vault_uri: ...}
  │
  └── post-deploy
      ├── write manifest.yaml (existing)
      ├── write deployment-outputs.json (NEW)
      └── optionally push to registry (future)
```

### Implementation in deploy pipeline

```python
# In the deploy orchestrator, after all stages complete:

def _write_outputs_artifact(
    self,
    deployment_service: DeploymentService,
    build_path: Path,
    stage_results: List[StageResult],
    resolved_values: ResolvedValues,
    config: OutputCaptureModel,
) -> Optional[Path]:
    """Write the registry-consumable outputs artifact."""
    if not config.enabled:
        return None

    outputs: Dict[str, Dict[str, Any]] = {}
    sensitive_keys: List[str] = []

    for result in stage_results:
        if result.status != "success":
            continue
        if config.stages and result.name not in config.stages:
            continue

        # Non-sensitive outputs
        stage_outputs = result.non_sensitive_outputs or {}
        outputs[result.name] = dict(stage_outputs)

        # Track sensitive keys (values not included)
        for key in (result.sensitive_outputs or {}).keys():
            sensitive_keys.append(f"{result.name}.{key}")

        # Include sensitive values only if explicitly opted in
        if config.include_sensitive:
            for key, value in (result.sensitive_outputs or {}).items():
                outputs[result.name][key] = value

    # Apply transform if configured
    if config.transform:
        transformed = {}
        for target_key, source_path in config.transform.items():
            stage_name, output_name = source_path.split(".", 1)
            if stage_name in outputs and output_name in outputs[stage_name]:
                transformed[target_key] = outputs[stage_name][output_name]
        outputs["_registry"] = transformed

    # Build the artifact
    artifact = DeploymentOutputsModel(
        meta=DeploymentOutputsMetaModel(
            name=deployment_service.deployment_name,
            deployment=deployment_service.deployment_name,
            version=deployment_service.version or "unknown",
            deployed_at=datetime.now(timezone.utc).isoformat(),
            workspace=deployment_service.workspace_name,
            environment=deployment_service.environment_name,
            tenant=deployment_service.tenant_name,
        ),
        outputs=outputs,
        sensitive_keys=sensitive_keys,
        provenance={
            "manifest_path": str(deployment_service.get_manifest_path(build_path)),
            "stages_completed": [r.name for r in stage_results if r.status == "success"],
        },
    )

    # Write
    output_path = deployment_service.get_build_path(build_path) / config.path
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(artifact.model_dump(exclude_none=True), f, indent=2, default=str)

    return output_path
```

### Manifest integration

The existing `ManifestStageModel.outputs_artifact` field is already designed for this.
Enhance it to also reference the combined outputs artifact:

```python
# In ManifestModel (top-level)
outputs_artifact: Optional[ManifestOutputsReferenceModel] = Field(
    None,
    description="Reference to the combined deployment-outputs.json artifact"
)
```

### Registry integration (future)

The outputs artifact is the **contract** between strata and the service registry.
Future work can:
1. Push the artifact to an API endpoint after deploy.
2. Store it in a git repository (GitOps pattern).
3. Upload to Azure App Configuration / Consul for service discovery.

This ADR only covers **generation**. Delivery is out of scope.

### Interaction with `deploy destroy`

On destroy:
- The outputs artifact is deleted (or marked with `status: destroyed`).
- Registry consumers should handle the absence of the file as "not deployed".

### Migration

- **Fully backward compatible**: `outputs` config is optional; defaults to `enabled: true`.
- Existing deployments that don't configure `outputs` will automatically get the
  artifact on their next successful deploy.
- The artifact is write-only — it doesn't affect any existing behavior.

### Test cases

1. Successful deploy with 2 stages → outputs file contains both stages' outputs.
2. Deploy with `outputs.enabled: false` → no artifact written.
3. Deploy with `outputs.stages: [team_module]` → only team outputs captured.
4. Deploy with `include_sensitive: false` → sensitive keys listed, values absent.
5. Deploy with `include_sensitive: true` → sensitive values included.
6. Deploy with `transform` → `_registry` key contains mapped values.
7. Partial deploy (one stage fails) → only successful stages captured.
8. Destroy → artifact deleted/marked destroyed.
9. Deploy with no Terraform outputs → empty `outputs` dict (still valid artifact).

## Files to change

| File                                              | Change                                                  |
| ------------------------------------------------- | ------------------------------------------------------- |
| `src/strata/models/deployment_outputs_model.py`   | New model for the outputs artifact                      |
| `src/strata/models/workspace_model.py`            | Add `OutputCaptureModel`                                |
| `src/strata/deployers/terraform_deployer.py`      | Ensure `collect_outputs()` returns data to orchestrator |
| `src/strata/controllers/deploy_controller.py`     | Call `_write_outputs_artifact()` post-deploy            |
| `src/strata/models/deployment_manifest_model.py`  | Top-level `outputs_artifact` ref                        |
| `tests/strata/controllers/test_deploy_outputs.py` | Integration tests                                       |
| `docs/config/deployment.md`                       | Document `spec.outputs` configuration                   |
| `docs/guides/registry-integration.md`             | Guide for consuming the artifact                        |
