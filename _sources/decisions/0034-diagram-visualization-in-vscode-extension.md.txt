# Diagram visualization in VS Code extension

- Status: proposed
- Date: 2026-07-11

## Context and Problem Statement

Today, the VS Code extension provides text-based tree views and code lens for exploring strata workspaces, but lacks visual representations of infrastructure topology, deployment orchestration, and version promotion flows. Users must mentally construct these relationships by navigating YAML files and tree views, which is cognitively expensive and error-prone.

The gaps:
- **No infrastructure topology visualization** — Users cannot see the hierarchy of workspaces → topologies → namespaces → modules → services at a glance.
- **No deployment stage flow diagram** — The execution order, dependencies, and provisioner assignments across deployment stages are not visually apparent.
- **No version promotion flow diagram** — Promotion rings (dev → test → qas → prd), gates, and policies lack visual representation; operators cannot see version progression across environments at a glance.
- **No service dependency diagram** — Cross-module and cross-namespace service dependencies are difficult to reason about from YAML alone.
- **Current dependency graph is file-focused** — `dependencyGraphProvider` shows YAML file references (`@repo/path` patterns) but doesn't visualize logical infrastructure relationships.
- **No user-composable diagrams** — Users cannot combine data sources to create custom views tailored to their specific needs.

This breaks the "visualize the YAML" principle: infrastructure configuration should be navigable as diagrams, not just as hierarchical text.

## Decision Drivers

- **Cognitive load reduction** — Visual diagrams reduce mental overhead for understanding complex deployments.
- **Operational confidence** — Seeing stage flows and promotion gates visually increases confidence before deployment.
- **Onboarding acceleration** — New team members learn infrastructure topology faster via diagrams than YAML exploration.
- **Compliance visibility** — Promotion gates and approval workflows must be immediately visible to auditors and operators.
- **Data availability** — The required infrastructure data is already present in `strata status` output; no new CLI commands needed.
- **Customizability** — Every workspace is different; users need to compose their own views, not be limited to a fixed set.

## Considered Options

### Option A: Fixed set of built-in diagrams only

Provide 3–5 hardcoded diagram types with no user customization.

**Pros:** Simple to implement, no configuration required.
**Cons:** Every workspace is different; fixed diagrams won't fit all needs.

### Option B: Comprehensive catalog with user-selectable diagrams

Provide a large catalog of diagram types (see Appendix A) that users can browse, select, and save as favorites.

**Pros:** Broad coverage, still manageable complexity.
**Cons:** Catalog grows stale; users are limited to what the developers thought of.

### Option C: Catalog + Diagram Builder (composable)

Everything in Option B, plus a **Diagram Builder** that lets users compose custom diagrams by picking data sources, layout types, and filters — then save them to the solution.

**Pros:** Infinite flexibility, user-owned diagrams, covers use cases we can't predict.
**Cons:** More complex UI, requires a diagram definition format.

## Decision Outcome

**Option C: Catalog + Diagram Builder.**

We provide:
1. **Built-in diagrams** — A curated set of "Top 10" diagrams available out of the box (no configuration).
2. **Diagram Catalog** — A browsable library of 185+ diagram types organized by category, any of which can be rendered on-demand.
3. **Diagram Builder** — A visual composer where users pick topics (data sources), choose layout, apply filters, and save custom diagrams to `.strata/diagrams/`.

### Rationale

1. **Maximum value surface**: 185 possible diagrams means users always find what they need.
2. **User ownership**: Saved diagrams become part of the solution — versioned, shared, team-accessible.
3. **Extensibility**: New data sources automatically create new diagram possibilities without code changes.
4. **Progressive disclosure**: Start with built-in diagrams, discover catalog over time, graduate to builder for power users.

---

## Implementation Status

Not yet started. This is a proposal for prioritization and roadmap planning.

---

## Part 1: Built-In Diagrams (Top 10)

These diagrams are available immediately with zero configuration — one click from command palette or chat.

| #   | Name                           | Chat Command    | Mermaid Type | Purpose                                                                          |
| --- | ------------------------------ | --------------- | ------------ | -------------------------------------------------------------------------------- |
| 1   | Infrastructure Topology        | `/topology`     | flowchart    | Workspace → topologies → namespaces → modules → services hierarchy               |
| 2   | Deployment Stage Flow          | `/stages`       | flowchart    | Stage execution order, dependencies, failure handling                            |
| 3   | Version Promotion Flow         | `/promote`      | flowchart LR | Ring progression with gates and current versions                                 |
| 4   | Network Topology               | `/network`      | flowchart    | Networks, subnets, peerings, firewalls, DNS zones combined                       |
| 5   | Service Dependency Graph       | `/services`     | flowchart    | Cross-module service dependencies and startup ordering                           |
| 6   | Environment Composition        | `/envs`         | flowchart    | Base + override merge hierarchy producing final config                           |
| 7   | Secret Resolution Chain        | `/secrets`      | flowchart    | Store → generate → resolve → inject lifecycle                                    |
| 8   | Deployment File Reference Tree | `/refs`         | flowchart    | All YAML files referenced by a deployment                                        |
| 9   | Stage Execution Timeline       | `/timeline`     | gantt        | Per-stage and per-step duration from deployment history                          |
| 10  | Full Platform Architecture     | `/architecture` | flowchart    | End-to-end: providers → topologies → resources → namespaces → modules → services |

---

## Part 2: Diagram Catalog (185 Types)

The full catalog is organized into 27 categories. Users browse the catalog from the Architecture Hub sidebar or via `/diagram list` in chat. Any catalog entry can be rendered on-demand.

### Category 1: Infrastructure Topology (12 diagrams)

| #   | Name                           | Data Source                                                        | Mermaid Type | Description                                                        |
| --- | ------------------------------ | ------------------------------------------------------------------ | ------------ | ------------------------------------------------------------------ |
| 1   | Workspace Topology Overview    | `workspace_model.WorkspaceTopologyModel`                           | flowchart    | Topologies, providers, provisioners, components, namespaces        |
| 2   | Resource Dependency Graph      | `workspace_model.WorkspaceResourceModel.depends_on`                | flowchart    | DAG of resource dependencies within a workspace                    |
| 3   | Resource-to-Module Binding     | `workspace_model.WorkspaceModuleReferenceModel`                    | flowchart    | Maps infrastructure resources to attached application modules      |
| 4   | Provider-Region-Zone Map       | `configuration_model.ConfigurationZoneModel` + provider properties | mindmap      | Hierarchical view of zones → regions → providers                   |
| 5   | Subnet Layout per Network      | `network_model.NetworkDefinitionModel.subnets`                     | flowchart    | Address space decomposition into subnets with CIDR blocks          |
| 6   | Network Peering Topology       | `network_model.PeeringReferenceModel`                              | flowchart    | Network-to-network peering relationships                           |
| 7   | Full Network Topology          | `network_model` + `firewall_model` + `dns_model`                   | flowchart    | Combined view: networks, subnets, peerings, firewalls, DNS zones   |
| 8   | Resource-Subnet Assignment     | `workspace_model.WorkspaceResourceModel.subnet`                    | flowchart    | Which resources sit in which subnet                                |
| 9   | Resource-Firewall Binding      | `workspace_model.WorkspaceResourceModel.firewalls`                 | flowchart    | Which NSG/firewall rulesets protect which resources                |
| 10  | Cross-Resource References      | `workspace_model.WorkspaceResourceModel.references`                | flowchart    | Value-passing links between resources (e.g. connection strings)    |
| 11  | Topology Component Composition | `configuration_model.ConfigurationTopologyModel.components`        | class        | Topology type + its required/optional component roles              |
| 12  | Multi-Topology Workspace       | `workspace_model.WorkspaceSpecModel.topology` (list)               | flowchart    | Multiple topologies per workspace with shared/dedicated namespaces |

### Category 2: Deployment Orchestration (10 diagrams)

| #   | Name                           | Data Source                                                      | Mermaid Type | Description                                           |
| --- | ------------------------------ | ---------------------------------------------------------------- | ------------ | ----------------------------------------------------- |
| 13  | Deployment Stage Pipeline      | `deployment_model.DeploymentStageModel`                          | flowchart    | Sequential/parallel stage execution order             |
| 14  | Stage DAG (depends_on)         | `DeploymentStageModel.depends_on`                                | flowchart    | Full directed acyclic graph of stage dependencies     |
| 15  | Stage Failure Handling Flow    | `DeploymentStageModel.on_failure`                                | stateDiagram | State machine: stop/rollback/continue paths per stage |
| 16  | Deployment File Reference Tree | `DeploymentModel.spec` (workspace, environments, configurations) | flowchart    | All YAML files referenced by a deployment             |
| 17  | Stage-Provisioner Mapping      | `DeploymentStageModel.provisioner/topology`                      | flowchart    | Which provisioner runs each stage                     |
| 18  | Health Check Flow              | `DeploymentStageModel.health_checks`                             | sequence     | Post-deploy health check sequence per stage           |
| 19  | Approval Gate Flow             | `DeploymentApprovalModel` + stage approvals                      | flowchart    | Approval requirements per stage and approver routing  |
| 20  | Stage Timeout Budget           | `DeploymentStageTimeoutsModel`                                   | gantt        | Per-step timeout allocation within each stage         |
| 21  | Secret Access Allowlist        | `DeploymentStageModel.secrets`                                   | flowchart    | Which stages can access which secret keys             |
| 22  | Deployment Layers Hierarchy    | `deployment_model.spec.layers`                                   | flowchart    | Layer stack (tenant → zone → environment)             |

### Category 3: Version & Promotion Flows (11 diagrams)

| #   | Name                                | Data Source                                        | Mermaid Type | Description                                                 |
| --- | ----------------------------------- | -------------------------------------------------- | ------------ | ----------------------------------------------------------- |
| 23  | Progression Ring Sequence           | `promotion_model.ProgressionModel.rings`           | flowchart LR | Linear ring progression (dev → test → staging → prd)        |
| 24  | Ring-Wave Matrix                    | `promote matrix` output                            | quadrant     | Rings vs wave assignments with version status               |
| 25  | Version Pin Status Matrix           | `version_lock_model.VersionLockModel`              | flowchart    | Per-ring pinned versions for all targets                    |
| 26  | Promotion Strategy Flow             | `PromotionStrategyModel` (type, waves, gates)      | flowchart    | How an artifact type moves through waves within a ring      |
| 27  | Promotion Record Timeline           | `promotion_record_model`                           | timeline     | Chronological wave execution with commits and timestamps    |
| 28  | Gate Evaluation Sequence            | `PromotionGateResultModel`                         | sequence     | Gate checks performed before each promotion step            |
| 29  | Version Lock vs Manifest Layering   | version-lock + version-manifest resolution         | flowchart    | Resolution priority: manifest → lock → environment override |
| 30  | Promotion Wave Deployment Targeting | `PromotionWaveModel` + deployment wave assignments | flowchart    | Which deployments participate in canary/early/all waves     |
| 31  | Cross-Ring Quorum State             | `ProgressionRingModel.require` (any_one/all)       | stateDiagram | Quorum gate status across ring boundaries                   |
| 32  | Promotion Rollback Chain            | `PromotionRecordSpecModel.rollback_of`             | flowchart    | Links between promotions and their rollbacks                |
| 33  | Version Drift per Target            | `version_manifest` vs `version_lock` comparison    | flowchart    | Where manifest declares a version but lock differs          |

### Category 4: Network & Security (7 diagrams)

| #   | Name                              | Data Source                                           | Mermaid Type | Description                                               |
| --- | --------------------------------- | ----------------------------------------------------- | ------------ | --------------------------------------------------------- |
| 34  | Firewall Ruleset Matrix           | `firewall_model.FirewallRuleModel`                    | flowchart    | Inbound/outbound rules with allow/deny per direction      |
| 35  | Firewall Traffic Flow             | `FirewallRuleModel` (direction, from, to, port)       | sequence     | Traffic paths: source → firewall decision → destination   |
| 36  | DNS Zone Record Map               | `dns_model.DnsZoneModel.records`                      | class        | Zone hierarchy with record types and values               |
| 37  | DNS Resolution Chain              | `dns_model.DnsRecordModel` (value/var/secret sources) | flowchart    | How DNS record values are resolved                        |
| 38  | Security Store Policy             | `ConfigurationSecurityModel.allowed_secret_stores`    | pie          | Distribution of allowed vs blocked store types            |
| 39  | Authentication Model Map          | `auth_models` (OAuth2, AWS, GCP, APIKey, Certificate) | class        | Provider authentication mechanisms and credential sources |
| 40  | Network Address Space Utilization | `network_model` CIDR allocations                      | flowchart    | Address space breakdown and utilization percentage        |

### Category 5: Secret & Variable Resolution (8 diagrams)

| #   | Name                           | Data Source                                             | Mermaid Type | Description                                                  |
| --- | ------------------------------ | ------------------------------------------------------- | ------------ | ------------------------------------------------------------ |
| 41  | Variable Resolution Chain      | `store_models.VariableStoreModel` + environment merging | flowchart    | Resolution path: constant → environment → appconfig → consul |
| 42  | Secret Store Distribution      | `store_models.SecretStoreModel` (store types)           | pie          | Breakdown of secrets by backend type                         |
| 43  | Secret Generation & Rotation   | `SecretGenerateSpec` + `SecretRotateSpec`               | stateDiagram | Lifecycle: generate → active → warn/rotate                   |
| 44  | Module Reference Requirements  | `ModuleReferenceModel` (variables, secrets, features)   | class        | What each module needs from the environment                  |
| 45  | Feature Flag State Map         | `FeatureStoreModel` entries                             | flowchart    | Feature toggles with their on/off state per environment      |
| 46  | Value Source Traceability      | `strata values list` output                             | sankey       | Flow from store backends through environments to deployment  |
| 47  | Environment Variable Injection | `ModuleServiceEnvironmentModel`                         | flowchart    | How each container env var gets its value                    |
| 48  | Store Backend Integration Map  | `store_models.*StoreType` → `IntegrationModel`          | flowchart    | Which integrations back which store types                    |

### Category 6: Dependency & Reference Chains (8 diagrams)

| #   | Name                                      | Data Source                                                 | Mermaid Type | Description                                            |
| --- | ----------------------------------------- | ----------------------------------------------------------- | ------------ | ------------------------------------------------------ |
| 49  | Cross-Repository File References          | `@repo_name/path` references in all YAML                    | flowchart    | Inter-repo file dependency graph                       |
| 50  | Remote Source Graph                       | `repository_model.RemoteModel` (gitops, bundled, container) | flowchart    | All remote sources with their types and references     |
| 51  | Resource Capability Dependencies          | `resource_model.ResourceDependencyModel`                    | flowchart    | Category/subcategory dependency declarations           |
| 52  | Configuration Inheritance Chain           | configuration → workspace → environment → deployment        | flowchart    | Full config merge hierarchy                            |
| 53  | Namespace-Module Containment              | `namespace_model.NamespaceModuleModel`                      | flowchart    | Which modules live in which namespaces                 |
| 54  | Module Service Dependency Graph           | `ModuleServiceModel.depends_on`                             | flowchart    | Intra-module and cross-module service startup ordering |
| 55  | Deployment-Workspace-Environment Assembly | `DeploymentModel.spec` references                           | flowchart    | How a deployment composes its workspace + environments |
| 56  | Provider Resource Type Catalog            | `ConfigurationProviderResourceModel`                        | class        | Available resource types per provider                  |

### Category 7: Lifecycle & Hooks (6 diagrams)

| #   | Name                           | Data Source                                       | Mermaid Type | Description                                             |
| --- | ------------------------------ | ------------------------------------------------- | ------------ | ------------------------------------------------------- |
| 57  | Build Lifecycle Hook Sequence  | `CommonLifecycleModel` phases                     | sequence     | Script execution during build (before/after each phase) |
| 58  | Deploy Lifecycle Hook Sequence | deploy phases (setup, check, plan, apply)         | sequence     | Script execution during deploy with scope               |
| 59  | Script Scope Execution Model   | `ScriptPathModel.scope` + `priority`              | flowchart    | How scripts fan out by scope                            |
| 60  | Provisioner Step Pipeline      | provisioner steps (setup→check→plan→apply→output) | flowchart LR | Step sequence per deployer type                         |
| 61  | Workspace Onboarding Workflow  | `workflow_model.WorkflowStep`                     | flowchart    | The 8-phase guide workflow with dependencies            |
| 62  | Integration Lifecycle Hooks    | `IntegrationModel.lifecycle`                      | sequence     | Hook points for external integrations                   |

### Category 8: Resource & Provider Relationships (7 diagrams)

| #   | Name                                | Data Source                                        | Mermaid Type | Description                                              |
| --- | ----------------------------------- | -------------------------------------------------- | ------------ | -------------------------------------------------------- |
| 63  | Provider-Topology-Resource Triangle | workspace spec                                     | flowchart    | Provider → Topology → Resources → Modules                |
| 64  | Resource Configuration Schema       | `ConfigurationProviderResourceModel.configuration` | class        | Schema fields per resource type                          |
| 65  | Resource Storage Layout             | `resource_model.ResourceStorageModel`              | flowchart    | Disk and volume mounts per resource                      |
| 66  | Resource Count Distribution         | `WorkspaceResourceModel.count`                     | pie          | Instance count distribution across resources             |
| 67  | Conditional Resource Inclusion      | `WorkspaceResourceModel.condition` + `enabled`     | flowchart    | Conditionally deployed resources                         |
| 68  | Module Slot Types                   | `WorkspaceModuleReferenceModel.slot_type`          | flowchart    | Slot distribution (main, staging, canary, sidecar, init) |
| 69  | Provider Engine Versions            | `ProviderPropertiesModel.engine` + `version`       | class        | IaC engine and version constraints per provider          |

### Category 9: Environment Composition (8 diagrams)

| #   | Name                                     | Data Source                          | Mermaid Type | Description                                              |
| --- | ---------------------------------------- | ------------------------------------ | ------------ | -------------------------------------------------------- |
| 70  | Environment Override Merge               | `EnvironmentOverridesModel`          | flowchart    | How env overrides layer onto workspace config            |
| 71  | Environment Remote Pinning               | `EnvironmentRemoteOverrideModel`     | flowchart    | Per-environment version pins for remotes                 |
| 72  | Environment Module Overrides             | `EnvironmentModuleOverrideModel`     | class        | Module-level overrides (chart_version, images, enabled)  |
| 73  | Environment Resource Overrides           | `EnvironmentResourceOverrideModel`   | class        | Resource overrides (count, config, enabled) per env      |
| 74  | Environment Include Merging              | `EnvironmentIncludeModel`            | flowchart    | Terraform file merging during build                      |
| 75  | Multi-Environment Deployment Composition | deployment `environments[]` list     | flowchart    | How multiple environment files compose into final config |
| 76  | Environment Scope Annotation             | `DeploymentEnvironmentRef.scope`     | flowchart    | Promotion wave targeting by environment scope            |
| 77  | Deployment Properties Merge              | tenant + deployment + env properties | flowchart    | Property override precedence chain                       |

### Category 10: Audit & History (9 diagrams)

| #   | Name                           | Data Source                            | Mermaid Type | Description                                      |
| --- | ------------------------------ | -------------------------------------- | ------------ | ------------------------------------------------ |
| 78  | Deployment History Timeline    | `deploy_log_model.DeployLogModel`      | timeline     | Chronological deployments with success/failure   |
| 79  | Stage Execution Duration       | `DeployLogStageModel.duration_seconds` | gantt        | Per-stage timing breakdown                       |
| 80  | Step-Level Execution Waterfall | `DeployLogStepModel` per stage         | gantt        | Granular step timing (setup, check, plan, apply) |
| 81  | Deployment Success Rate        | deploy history aggregation             | pie          | Success vs failure ratio over time               |
| 82  | Audit Event Flow               | `AuditConfigModel` → `AuditSinkModel`  | flowchart    | Event types routing to different sinks           |
| 83  | Audit Policy Coverage          | `AuditPolicyModel.events`              | pie          | Enabled vs disabled audit event types            |
| 84  | PR Enrichment Chain            | `DeployLogPullRequestModel`            | sequence     | Deploy → commit → PR → approvers → issues        |
| 85  | Execution Error Distribution   | `DeployLogModel.errors` aggregation    | pie          | Error types and frequency                        |
| 86  | Promotion Activity Log         | `promotion_record_model`               | timeline     | Promotion executions with outcomes               |

### Category 11: Drift Detection (6 diagrams)

| #   | Name                        | Data Source                                        | Mermaid Type | Description                                       |
| --- | --------------------------- | -------------------------------------------------- | ------------ | ------------------------------------------------- |
| 87  | Drift Severity Distribution | `drift_model.DriftSummary`                         | pie          | Critical/High/Medium/Low/Info breakdown           |
| 88  | Drift Entry Detail          | `drift_model.DriftEntry` (before/after)            | class        | Individual resource drift with changed attributes |
| 89  | Drift Timeline              | `DriftEntry.first_detected` + `consecutive_checks` | timeline     | When drift was first detected and persistence     |
| 90  | Drift by Resource Type      | `DriftEntry.resource_type` aggregation             | pie          | Which resource types drift most                   |
| 91  | Drift by Stage              | `DriftEntry.stage` grouping                        | pie          | Which stages experience most drift                |
| 92  | Drift Action Distribution   | `DriftEntry.action` (update/delete/create)         | pie          | Types of unplanned changes detected               |

### Category 12: Policy Enforcement (6 diagrams)

| #   | Name                       | Data Source                                 | Mermaid Type | Description                                                     |
| --- | -------------------------- | ------------------------------------------- | ------------ | --------------------------------------------------------------- |
| 93  | Policy Evaluation Pipeline | `PolicyModel.phase` ordering                | flowchart    | When each policy evaluates (validate→build→plan→deploy)         |
| 94  | Policy Enforcement Levels  | `PolicyModel.enforcement` (deny/warn/audit) | pie          | Distribution of enforcement strictness                          |
| 95  | Policy Type Coverage       | `PolicyModel.type` catalog                  | mindmap      | All policy types: tenant_zone, required_tags, naming, CVE, etc. |
| 96  | Policy-Phase Matrix        | policy × phase                              | quadrant     | Which policies fire at which lifecycle phase                    |
| 97  | CVE Severity Distribution  | `sbom_model.CveAuditResultModel`            | pie          | Critical/High/Medium/Low/Unknown findings                       |
| 98  | CVE Allowlist Coverage     | `CveAllowedEntryModel` vs findings          | flowchart    | Which CVEs are suppressed with justifications                   |

### Category 13: Multi-Tenant Distribution (7 diagrams)

| #   | Name                           | Data Source                           | Mermaid Type | Description                               |
| --- | ------------------------------ | ------------------------------------- | ------------ | ----------------------------------------- |
| 99  | Tenant-Zone Assignment         | `tenant_model.TenantSpecModel.zones`  | flowchart    | Which tenants are allowed in which zones  |
| 100 | Tenant Environment Composition | `TenantSpecModel.environments`        | flowchart    | Tenant-injected base environment files    |
| 101 | Tenant Property Inheritance    | `TenantSpecModel.properties/custom`   | flowchart    | How tenant defaults flow into deployments |
| 102 | Tenant Onboarding Timeline     | `TenantSpecModel.onboarded` dates     | timeline     | Tenant onboarding chronology              |
| 103 | Namespace Ownership Model      | `NamespaceType` (dedicated vs shared) | flowchart    | Tenant-dedicated vs shared namespaces     |
| 104 | Tenant-Deployment Wave Map     | tenant scope + promotion waves        | flowchart    | Which tenants are in canary vs all waves  |
| 105 | Zone-Region-Tenant Hierarchy   | zones → regions → providers → tenants | mindmap      | Full data residency hierarchy             |

### Category 14: Service Architecture (9 diagrams)

| #   | Name                              | Data Source                                       | Mermaid Type | Description                                                      |
| --- | --------------------------------- | ------------------------------------------------- | ------------ | ---------------------------------------------------------------- |
| 106 | Module Service Composition        | `ModuleServiceModel` (image, ports, env)          | class        | Container services within a module                               |
| 107 | Port Exposure Map                 | `ModuleServiceModel.ports`                        | flowchart    | Host:container port mappings across all services                 |
| 108 | Service Health Check Matrix       | `ModuleCheckModel` + healthcheck                  | class        | Health check types and targets per service                       |
| 109 | Service Environment Variable Map  | `ModuleServiceEnvironmentModel`                   | flowchart    | Env var sources (literal, variable, secret, feature) per service |
| 110 | Service Mount Topology            | `ModuleMountModel` (volume_ref vs storage_class)  | flowchart    | Volume and PVC mounts per service                                |
| 111 | Module Endpoint Catalog           | `ModuleEndpointModel` (url, type, port, protocol) | class        | All exposed endpoints across modules                             |
| 112 | Service Image Registry Map        | `ModuleServiceModel.image`                        | pie          | Container images by registry                                     |
| 113 | Cross-Module Service Dependencies | `ModuleServiceModel.depends_on` (@module/service) | flowchart    | Inter-module container startup ordering                          |
| 114 | Compose vs Helm Distribution      | `ModuleSpecModel.type`                            | pie          | Module deployment type distribution                              |

### Category 15: Repository & Remote Structure (6 diagrams)

| #   | Name                             | Data Source                                   | Mermaid Type | Description                                     |
| --- | -------------------------------- | --------------------------------------------- | ------------ | ----------------------------------------------- |
| 115 | Solution Repository Map          | `solution_model.SolutionSpecRepositoryModel`  | flowchart    | All registered repos with types and branches    |
| 116 | Remote Type Distribution         | `RemoteModel.type` (bundled/gitops/container) | pie          | Remote source types breakdown                   |
| 117 | Remote Version Pinning           | `RemoteModel.reference` per environment       | flowchart    | How remote versions vary across environments    |
| 118 | Repository-to-File Reference Map | `@repo_name/` references across all YAML      | sankey       | Flow of file references from repos to consumers |
| 119 | Git Context at Deploy Time       | `DeployLogModel.commit_sha/message/author`    | timeline     | Git commit trail of deployments                 |
| 120 | Profile-ConfigPath Binding       | `SolutionSpecProfileModel` path types         | class        | Profile → config/env/data/secret file mappings  |

### Category 16: Build Artifact Structure (10 diagrams)

| #   | Name                            | Data Source                                    | Mermaid Type | Description                                    |
| --- | ------------------------------- | ---------------------------------------------- | ------------ | ---------------------------------------------- |
| 121 | Platform Artifact Model         | `platform_artifact_model`                      | class        | Complete platform.json structure               |
| 122 | Build Output Profile            | `OutputProfileModel` (emit categories)         | flowchart    | Which tfvars file categories are emitted       |
| 123 | Output File Generation          | `OutputFileModel` (single/multi/script modes)  | flowchart    | Custom output file generation flows            |
| 124 | SBOM Component Breakdown        | `SbomComponentModel` by type                   | pie          | SBOM composition by component type             |
| 125 | SBOM Collector Pipeline         | `sbom/` collectors                             | flowchart    | Which collectors produce which SBOM components |
| 126 | Build Artifact Directory Tree   | builder output paths                           | mindmap      | Generated file/directory structure per builder |
| 127 | Deployment Manifest BOM         | `ManifestArtifactsModel`                       | class        | Platform hash, repos, images, providers        |
| 128 | Manifest Storage Flow           | `ConfigurationManifestModel` (local vs gitops) | flowchart    | Where manifests are persisted                  |
| 129 | Terraform Backend Configuration | `WorkspaceIacBackendModel`                     | class        | State storage backends per provisioner         |
| 130 | Builder Type Pipeline           | terraform → helm → compose → ansible → script  | flowchart LR | Builder execution order                        |

### Category 17: Integration & Tooling (5 diagrams)

| #   | Name                             | Data Source                     | Mermaid Type | Description                                     |
| --- | -------------------------------- | ------------------------------- | ------------ | ----------------------------------------------- |
| 131 | Integration Capability Map       | `IntegrationModel.capabilities` | mindmap      | Integrations grouped by capability              |
| 132 | Integration Availability Status  | `strata tools status`           | class        | Required vs optional, enabled vs available      |
| 133 | Integration Authentication Types | `AuthenticationModel` types     | class        | Auth methods per integration                    |
| 134 | Provisioner Plugin Registry      | `ProvisionerManifestModel`      | class        | Plugin metadata, supported steps, health checks |
| 135 | Provisioner Type Capabilities    | `ProvisionerType` enum + steps  | class        | What each provisioner type can do               |

### Category 18: Workspace & Solution State (5 diagrams)

| #   | Name                         | Data Source                             | Mermaid Type | Description                                       |
| --- | ---------------------------- | --------------------------------------- | ------------ | ------------------------------------------------- |
| 136 | Solution State Overview      | `strata status` output                  | mindmap      | Solution identity, profiles, repos, health        |
| 137 | Workspace Guide Progress     | `workflow_model.WorkflowStep` statuses  | flowchart    | 8-phase onboarding checklist with ok/warn/pending |
| 138 | Profile Configuration Matrix | `SolutionSpecProfileModel` × path types | class        | Profiles with their config/env/data/secret paths  |
| 139 | Deployment Registration Map  | `SolutionSpecDeploymentModel`           | flowchart    | Registered deployment files in the solution       |
| 140 | Configuration Kind Hierarchy | all `PlatformKind` values               | mindmap      | The full YAML kind taxonomy                       |

### Category 19: Deployment Values & Resolution (5 diagrams)

| #   | Name                              | Data Source                                | Mermaid Type | Description                                                |
| --- | --------------------------------- | ------------------------------------------ | ------------ | ---------------------------------------------------------- |
| 141 | Value Resolution Waterfall        | `strata values list` (layered)             | flowchart    | Configuration → workspace → environment → deployment merge |
| 142 | Resolved Variables Table          | `strata deploy values` output              | class        | Final resolved variable/secret/feature values              |
| 143 | CidrSource Resolution             | `CidrSourceModel` (value/var/secret)       | flowchart    | How network CIDRs resolve                                  |
| 144 | DNS Record Value Resolution       | `DnsRecordModel` (value/var/secret)        | flowchart    | How DNS record values are resolved                         |
| 145 | Provider Configuration Resolution | workspace + environment provider overrides | flowchart    | How provider properties get final values                   |

### Category 20: Deployment Manifest & Compliance (6 diagrams)

| #   | Name                            | Data Source                                  | Mermaid Type | Description                                |
| --- | ------------------------------- | -------------------------------------------- | ------------ | ------------------------------------------ |
| 146 | Manifest Stage Execution Report | `ManifestStageModel`                         | gantt        | Per-stage success/failure with timing      |
| 147 | Image Digest Audit              | `ManifestArtifactImageModel.digest`          | class        | Image references with SHA verification     |
| 148 | Repository Commit Pinning       | `ManifestRepositoryModel` (ref → commit SHA) | class        | Requested ref vs resolved commit           |
| 149 | Manifest Output References      | `ManifestOutputsReferenceModel`              | flowchart    | Durable output artifacts per stage/version |
| 150 | Lock State Audit Trail          | `ManifestLockReferenceModel`                 | timeline     | State lock acquisition/release events      |
| 151 | Deployment Action Distribution  | manifest `action` field aggregation          | pie          | Deploy vs destroy vs plan action breakdown |

### Category 21: Cost & Resource Allocation (4 diagrams)

| #   | Name                           | Data Source                           | Mermaid Type | Description                          |
| --- | ------------------------------ | ------------------------------------- | ------------ | ------------------------------------ |
| 152 | Resource Instance Distribution | `WorkspaceResourceModel.count` × type | pie          | Resource instance counts by type     |
| 153 | Storage Allocation Map         | `ResourceDiskModel.size` + volumes    | pie          | Disk sizes across all resources      |
| 154 | Provider Region Distribution   | topology → provider → region          | pie          | Resource distribution across regions |
| 155 | Tenant Resource Footprint      | tenant × resources via deployments    | pie          | Estimated allocation per tenant      |

### Category 22: Multi-Deployment Fleet (5 diagrams)

| #   | Name                              | Data Source                         | Mermaid Type | Description                                            |
| --- | --------------------------------- | ----------------------------------- | ------------ | ------------------------------------------------------ |
| 156 | Fleet Deployment Map              | all registered deployments          | flowchart    | All deployments with their workspaces and environments |
| 157 | Shared vs Dedicated Namespace Map | `NamespaceType` across deployments  | flowchart    | Cross-deployment namespace sharing                     |
| 158 | Deployment-Environment Matrix     | deployments × environments          | quadrant     | Which deployments target which environments            |
| 159 | Version Matrix Heatmap            | `promote matrix` across all targets | class        | Current pinned versions across all rings/targets       |
| 160 | Fleet Health Dashboard            | aggregate deploy history            | pie          | Fleet-wide success/failure/drift status                |

### Category 23: Configuration Schema & Validation (5 diagrams)

| #   | Name                         | Data Source                               | Mermaid Type | Description                                      |
| --- | ---------------------------- | ----------------------------------------- | ------------ | ------------------------------------------------ |
| 161 | Layer Hierarchy Definition   | `ConfigurationLayerModel` ordered list    | flowchart LR | Deployment path layering                         |
| 162 | Provider Region Allowlist    | `ConfigurationProviderModel.regions`      | class        | Allowed regions per provider                     |
| 163 | Resource Schema Constraints  | `ConfigurationSchemaField`                | class        | Validation patterns for resource configs         |
| 164 | Topology Component Blueprint | `ConfigurationTopologyModel` + components | class        | Required/optional components with min/max counts |
| 165 | Deployment Property Schema   | `ConfigurationDeploymentModel.properties` | class        | Required/optional properties with patterns       |

### Category 24: Output & Outputs Architecture (3 diagrams)

| #   | Name                         | Data Source                              | Mermaid Type | Description                                    |
| --- | ---------------------------- | ---------------------------------------- | ------------ | ---------------------------------------------- |
| 166 | Output Artifact Storage Flow | `ConfigurationOutputsModel`              | flowchart    | How Terraform outputs are persisted            |
| 167 | Stage Output Chaining        | outputs from stage A → inputs to stage B | sequence     | How downstream stages consume upstream outputs |
| 168 | Sensitive Output Handling    | `SensitiveOutputHandling` (redact/omit)  | stateDiagram | Sensitive value treatment in stored artifacts  |

### Category 25: Locking & Concurrency (2 diagrams)

| #   | Name                             | Data Source               | Mermaid Type | Description                        |
| --- | -------------------------------- | ------------------------- | ------------ | ---------------------------------- |
| 169 | Deployment State Lock Lifecycle  | lock mechanism + manifest | stateDiagram | Lock acquire → hold → release flow |
| 170 | Concurrent Deployment Prevention | lock mechanism            | sequence     | How concurrent deploys are blocked |

### Category 26: SBOM & Supply Chain (7 diagrams)

| #   | Name                         | Data Source                        | Mermaid Type | Description                                 |
| --- | ---------------------------- | ---------------------------------- | ------------ | ------------------------------------------- |
| 171 | SBOM Dependency Tree         | `SbomComponentModel` relationships | flowchart    | Full bill of materials with package URLs    |
| 172 | Image Source Distribution    | image PURL breakdown               | pie          | Where container images come from            |
| 173 | Terraform Module Sources     | terraform collector output         | flowchart    | Terraform module registry/git sources       |
| 174 | Helm Chart Sources           | helm collector output              | flowchart    | Helm chart repositories and versions        |
| 175 | Dependency Lockfile Coverage | deps_collector per language        | pie          | Language breakdown of scanned lockfiles     |
| 176 | SBOM Ignore Coverage         | `SbomIgnorePathRuleModel`          | flowchart    | Excluded paths with justifications          |
| 177 | CVE Finding → Component Map  | `CveFindingModel` → component      | flowchart    | Which components have which vulnerabilities |

### Category 27: Composite / Cross-Cutting Views (8 diagrams)

| #   | Name                                      | Data Source                                           | Mermaid Type | Description                                                                      |
| --- | ----------------------------------------- | ----------------------------------------------------- | ------------ | -------------------------------------------------------------------------------- |
| 178 | Full Platform Architecture                | all models combined                                   | flowchart    | End-to-end: providers → topologies → resources → namespaces → modules → services |
| 179 | Deployment-to-Infrastructure Traceability | deployment → stages → provisioners → resources        | sankey       | How a deploy command flows to infrastructure changes                             |
| 180 | Secret Lifecycle End-to-End               | store → generate → rotate → resolve → inject → deploy | sequence     | Complete secret journey                                                          |
| 181 | Configuration Kind Dependency Graph       | all YAML files with cross-references                  | flowchart    | All file dependencies across all kinds                                           |
| 182 | Strata CLI Command Taxonomy               | all CLI groups and commands                           | mindmap      | Full CLI command tree                                                            |
| 183 | Build → Deploy → Manifest Pipeline        | build → deploy → manifest write                       | sequence     | End-to-end pipeline from code to evidence                                        |
| 184 | Environment Composition Stack             | tenant envs + deployment envs + overrides + versions  | flowchart    | Full merge stack producing final resolved config                                 |
| 185 | Deployment Readiness Checklist            | guide + policy + health + drift                       | mindmap      | All gates before a deployment is "ready"                                         |

---

## Part 3: Diagram Builder

The Diagram Builder is a visual composer that lets users create custom diagrams by combining data sources, choosing layouts, and applying filters. Custom diagrams are saved to the solution and become team-shared artifacts.

### Concept

Users should not be limited to the 185 catalog diagrams. Every workspace has unique questions:
- "Show me only the modules that changed in the last deployment"
- "Show the network topology but only for the production environment"
- "Combine the stage flow with the drift status so I can see which stages have drifted resources"
- "Show me all the resources connected to tenant X across all workspaces"

The Diagram Builder lets users **compose** diagrams from building blocks.

### Building Blocks

A custom diagram is defined by:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: diagram
meta:
  name: my-production-topology
  annotations:
    description: "Production infrastructure with drift indicators"
  labels:
    category: topology
    environment: prd
spec:
  # What data to include
  sources:
    - type: topology
      filter:
        workspace: platform
        topology: kubernetes
    - type: drift
      filter:
        severity: [critical, high]
    - type: modules
      filter:
        namespace: core
        deployer: helm

  # How to render it
  layout:
    type: flowchart       # flowchart | sequence | gantt | pie | mindmap | class | stateDiagram | timeline | quadrant | sankey
    direction: TD         # TD | LR | BT | RL

  # What to emphasize
  style:
    color_by: drift_status    # drift_status | provisioner | deployer_type | status | environment | tenant
    highlight:
      - condition: "drift.severity == critical"
        style: "fill:#ff0000,stroke:#900"
    group_by: namespace       # namespace | topology | stage | ring | tenant

  # Interactions (optional)
  actions:
    on_click: open_file       # open_file | show_values | show_history | none
    on_hover: show_tooltip    # show_tooltip | none
```

### Data Source Types (composable)

Users pick from these data sources and combine them freely:

| Source Type    | What It Provides                   | Combinable With                   |
| -------------- | ---------------------------------- | --------------------------------- |
| `topology`     | Workspaces, topologies, components | modules, drift, network           |
| `modules`      | Namespaces, modules, services      | topology, secrets, endpoints      |
| `stages`       | Deployment stages, dependencies    | drift, history, approvals         |
| `promotion`    | Rings, gates, version locks        | history, tenants                  |
| `network`      | Networks, subnets, peerings        | firewalls, dns, resources         |
| `firewalls`    | Firewall rules, NSG bindings       | network, resources                |
| `dns`          | DNS zones, records                 | network                           |
| `secrets`      | Secret stores, resolution chains   | modules, environments             |
| `variables`    | Variable stores, resolution        | modules, environments             |
| `features`     | Feature flags, state               | modules, environments             |
| `drift`        | Drift entries, severity            | topology, stages, resources       |
| `history`      | Deploy logs, durations             | stages, promotion                 |
| `policies`     | Policy evaluations, enforcement    | stages, deployments               |
| `tenants`      | Tenant assignments, zones          | topology, promotion, environments |
| `environments` | Environment composition, overrides | topology, modules, promotion      |
| `repositories` | Repos, remotes, tags               | references, versions              |
| `sbom`         | Components, dependencies, CVEs     | modules, images                   |
| `resources`    | Resource instances, dependencies   | topology, network, firewalls      |
| `approvals`    | Approval status, reviewers         | stages, promotion                 |
| `locks`        | Lock state, holders, TTL           | stages, deployments               |
| `outputs`      | Stage outputs, chaining            | stages                            |
| `values`       | Resolved values, sources           | modules, environments             |

### Filter Operators

Filters narrow data sources to specific subsets:

```yaml
filter:
  # Equality
  workspace: platform
  environment: prd
  tenant: acme-corp

  # Lists (OR)
  namespace: [core, apps, monitoring]
  provisioner: [terraform, helm]

  # Negation
  not:
    status: disabled
    deployer: script

  # Conditional
  where:
    - "resource.count > 1"
    - "module.slot_type == canary"

  # Time-based (for history/audit)
  since: 7d          # 7 days, 2w, 1m, etc.
  until: now
```

### Builder UX in VS Code

The builder provides a visual interface in the Architecture Hub panel:

```
┌─────────────────────────────────────────────────────────────────┐
│  📊 Diagram Builder                               [Save] [Reset]│
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─ Data Sources ──────────────────────────────────┐            │
│  │ [+ Add Source]                                   │            │
│  │                                                  │            │
│  │  ☑ topology    workspace: platform ▼  [filter]  │            │
│  │  ☑ modules     namespace: core ▼     [filter]   │            │
│  │  ☑ drift       severity: high+ ▼    [filter]    │            │
│  │  ☐ network     (click to configure)             │            │
│  │  ☐ secrets     (click to configure)             │            │
│  └──────────────────────────────────────────────────┘           │
│                                                                  │
│  ┌─ Layout ────────────────────────────────────────┐            │
│  │  Type: [flowchart ▼]   Direction: [TD ▼]       │            │
│  │  Color by: [drift_status ▼]                     │            │
│  │  Group by: [namespace ▼]                        │            │
│  └──────────────────────────────────────────────────┘           │
│                                                                  │
│  ┌─ Preview ───────────────────────────────────────┐            │
│  │                                                  │            │
│  │         ┌─────────────┐                         │            │
│  │         │  core       │                         │            │
│  │         │  ┌───────┐  │                         │            │
│  │         │  │api-gw │  │                         │            │
│  │         │  └───────┘  │                         │            │
│  │         │  ┌───────┐  │                         │            │
│  │         │  │  db   │  │  (live Mermaid render)  │            │
│  │         │  └───────┘  │                         │            │
│  │         └─────────────┘                         │            │
│  └──────────────────────────────────────────────────┘           │
│                                                                  │
│  Name: [my-production-topology    ]                              │
│  [💾 Save to Solution]  [📋 Copy Mermaid]  [📷 Export PNG]      │
└─────────────────────────────────────────────────────────────────┘
```

### Storage: `.strata/diagrams/`

Custom diagrams are saved as YAML files in the solution:

```
.strata/
├── solution.json
├── diagrams/
│   ├── production-topology.yaml
│   ├── stage-flow-with-drift.yaml
│   ├── tenant-distribution.yaml
│   └── weekly-deploy-timeline.yaml
└── ...
```

Benefits:
- **Versioned** — stored in git alongside the infrastructure config.
- **Team-shared** — everyone on the team sees the same diagrams.
- **Profile-aware** — diagrams can reference `${profile}` to adapt to the active profile.
- **Refreshable** — opening a saved diagram re-renders with current data (not a snapshot).

### CLI Integration

```bash
# Render a saved diagram
strata diagram render .strata/diagrams/production-topology.yaml --output mermaid
strata diagram render .strata/diagrams/production-topology.yaml --output svg
strata diagram render .strata/diagrams/production-topology.yaml --output png

# List saved diagrams
strata diagram list --output json

# Create from template
strata diagram new --template topology --name my-topology

# Validate diagram definition
strata validate .strata/diagrams/my-diagram.yaml
```

### Chat Integration

```
@strata /diagram list              → Show saved diagrams
@strata /diagram show <name>       → Render a saved diagram
@strata /diagram build             → Open the Diagram Builder
@strata /diagram "show me all modules with drift in production"
                                   → AI-assisted: infer sources + filters, render
```

The last form is the most powerful: users describe what they want in natural language, and the chat participant generates a diagram definition, renders it, and optionally saves it.

### AI-Assisted Diagram Generation

The chat participant can generate diagram definitions from natural language:

```
User: "show me how secrets flow from Key Vault to the api-gateway module"

@strata interprets this as:
  sources: [secrets, modules]
  filter:
    secrets.store_type: azure_keyvault
    modules.name: api-gateway
  layout:
    type: flowchart
    direction: LR
    color_by: source_type
```

This makes the full 185-diagram catalog accessible without users needing to know the YAML schema.

### Predefined Templates

For quick starts, the builder offers templates matching the Top 10 built-in diagrams plus common variations:

| Template       | Starting Sources               | Starting Layout |
| -------------- | ------------------------------ | --------------- |
| `topology`     | topology + modules             | flowchart TD    |
| `stages`       | stages                         | flowchart LR    |
| `promotion`    | promotion + history            | flowchart LR    |
| `network`      | network + firewalls + dns      | flowchart TD    |
| `services`     | modules (services focus)       | flowchart TD    |
| `secrets`      | secrets + variables            | flowchart LR    |
| `timeline`     | history                        | gantt           |
| `drift`        | drift + topology               | flowchart TD    |
| `fleet`        | all deployments + environments | flowchart TD    |
| `supply-chain` | sbom + modules                 | flowchart TD    |

---

## Technical Architecture

### `ArchitectureHubProvider`

```typescript
export class ArchitectureHubProvider implements vscode.Disposable {
    private _panel: vscode.WebviewPanel | undefined;
    private _activeTab: string;

    // Built-in diagram renderers
    private _renderers: Map<string, DiagramRenderer>;

    // Diagram builder state
    private _builderState: DiagramBuilderState | undefined;

    async show(tab?: string): Promise<void>;
    async showDiagram(definition: DiagramDefinition): Promise<void>;
    async openBuilder(template?: string): Promise<void>;
    async update(status: WorkspaceStatus): Promise<void>;
    dispose(): void;
}
```

### `DiagramRenderer` Interface

```typescript
interface DiagramRenderer {
    /** Generate Mermaid code from data sources + filters */
    render(sources: DataSourceResult[], options: LayoutOptions): string;

    /** Handle click events on diagram nodes */
    handleClick(nodeId: string): DiagramAction;

    /** Get tooltip content for hover */
    getTooltip(nodeId: string): string;
}
```

### `DiagramDefinition` Schema

```typescript
interface DiagramDefinition {
    apiVersion: string;
    kind: 'diagram';
    meta: {
        name: string;
        annotations?: { description?: string };
        labels?: Record<string, string>;
    };
    spec: {
        sources: DataSourceConfig[];
        layout: LayoutConfig;
        style?: StyleConfig;
        actions?: ActionConfig;
    };
}

interface DataSourceConfig {
    type: string;         // topology | modules | stages | drift | etc.
    filter?: Record<string, any>;
}

interface LayoutConfig {
    type: MermaidDiagramType;
    direction?: 'TD' | 'LR' | 'BT' | 'RL';
}

interface StyleConfig {
    color_by?: string;
    highlight?: HighlightRule[];
    group_by?: string;
}
```

### Data Source Resolution

Each data source type maps to a CLI command or cached workspace status data:

```typescript
const SOURCE_RESOLVERS: Record<string, (filter: any) => Promise<DataSourceResult>> = {
    topology:     (f) => client.getStatus().then(s => filterTopology(s, f)),
    modules:      (f) => client.getStatus().then(s => filterModules(s, f)),
    stages:       (f) => client.getStageFlow(f.deployment).then(s => filterStages(s, f)),
    promotion:    (f) => client.getPromotionFlow().then(p => filterPromotion(p, f)),
    drift:        (f) => client.runDrift(f.deployment).then(d => filterDrift(d, f)),
    history:      (f) => client.getAuditChanges(f).then(h => filterHistory(h, f)),
    network:      (f) => client.getStatus().then(s => filterNetwork(s, f)),
    secrets:      (f) => client.getValues().then(v => filterSecrets(v, f)),
    // ... etc.
};
```

### Mermaid Rendering

- Use `mermaid.initialize({ startOnLoad: true, theme: 'default' })` to respect VS Code theme
- Responsive sizing: diagram fills webview panel width, respects zoom levels
- Click handlers use `window.vscode.postMessage()` to communicate back to extension
- Builder provides live preview: re-render on every source/filter/layout change

---

## Open Questions

1. Should the builder support **composed diagrams** (multiple sub-diagrams in a single view, e.g., topology + stage flow side by side)?
2. Should saved diagrams support **parameterization** (e.g., `${environment}` resolved from active profile)?
3. Should the builder support **comparison mode** (e.g., "show topology for dev vs prd side by side")?
4. Should diagrams be **publishable** (export to Confluence, GitHub wiki, or docs folder)?
5. Should we provide a **diagram gallery** in the extension marketplace for community-shared diagram definitions?
6. Should there be a **dashboard mode** (pin 4–6 diagrams to a persistent panel, auto-refresh)?
7. Should we cache diagram data or re-resolve on every render?
8. How do we handle very large topologies (50+ modules, 200+ services)? Collapse/expand? Pagination? Zoom?
9. Should the AI chat generate diagrams proactively (e.g., after a deployment, auto-show stage timeline)?
10. Should we support Mermaid alternatives (D3.js, Elk.js) for complex layouts that Mermaid handles poorly?
11. Should diagram definitions support **inheritance** (extend a base diagram with additional sources/filters)?
12. Maximum Mermaid node count before performance degrades? Need benchmarks.

---

## Implementation Roadmap

### Phase 1: Architecture Hub + Top 3 Built-In Diagrams (v1.1.0)
- [ ] Create `ArchitectureHubProvider` with tabbed webview
- [ ] Implement Topology, Stage Flow, and Promotion renderers
- [ ] Wire into chat commands (`/topology`, `/stages`, `/promote`)
- [ ] Add command palette entry ("Strata: Show Architecture")
- [ ] Test with example workspaces in `config/`

### Phase 2: Full Top 10 Built-In Diagrams (v1.2.0)
- [ ] Add Network, Services, Environments, Secrets, Refs, Timeline, Full Architecture
- [ ] Add catalog browser in sidebar (search + filter by category)
- [ ] Add `/diagram list` and `/diagram show` chat commands

### Phase 3: Diagram Builder MVP (v1.3.0)
- [ ] Implement `DiagramDefinition` YAML schema (add to `strata schema get diagram`)
- [ ] Implement builder UI (sources picker, layout selector, live preview)
- [ ] Implement `strata diagram render` CLI command
- [ ] Save/load from `.strata/diagrams/`
- [ ] Add templates for quick-start

### Phase 4: AI-Assisted + Full Catalog (v1.4.0)
- [ ] Natural language → diagram definition in chat
- [ ] Full 185 catalog browsable with search
- [ ] Diagram filtering and highlighting rules
- [ ] Export to SVG/PNG/Mermaid markdown

### Phase 5: Advanced Builder Features (v1.5.0+)
- [ ] Diagram parameterization (`${profile}`, `${environment}`)
- [ ] Comparison mode (two diagrams side by side)
- [ ] Dashboard mode (pin multiple diagrams)
- [ ] Community diagram gallery
- [ ] Performance optimization for large topologies

---

## Related ADRs

- [ADR 0015: Flow command dependency graph](0015-flow-command-dependency-graph.md) — existing `strata flow` for CLI dependency visualization
- [ADR 0011: Promotion strategies for version progression](0011-promotion-strategies-for-version-progression.md) — version-lock and promotion policy design
- [ADR 0032: Approval workflows and gates](0032-approval-workflows-and-gates.md) — approval and policy engine
- [ADR 0008: Infrastructure drift detection](0008-infrastructure-drift-detection.md) — drift model data source
- [ADR 0018: Deployment audit traceability](0018-deployment-audit-traceability.md) — audit/history data source
- [ADR 0009: SBOM extended sources and inventory](0009-sbom-extended-sources-and-inventory.md) — supply chain data source

## Next Steps

1. Validate this design with early users — which of the 185 diagrams do they reach for first?
2. Prototype Phase 1 (Architecture Hub + Top 3) with `config/azure-aks` example
3. Benchmark Mermaid rendering performance at scale (50+ nodes, 200+ edges)
4. Design the Diagram Builder UX — mockups and interaction patterns
5. Define the `diagram` kind schema for `strata validate` support
6. Plan CLI `strata diagram` command group implementation
7. Evaluate AI-assisted diagram generation accuracy (natural language → YAML definition)
