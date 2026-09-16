# Checkov as a first-class integration

- Status: partially-implemented — Phase 1 done (bug found 2026-09-16, fix designed, implementation pending), Phase 2 not started
- Date: 2026-07-22
- Revised: 2026-07-23
- Revised: 2026-09-16 — artifact path resolution bug + provisioner `scope` config (see bottom of document)
- Supersedes: Partial aspects of ADR-0006 (policy-engine-for-deployment-guardrails)

## Remaining Work

- Plan-phase scanning via `terraform show -json` — deferred to Phase 2
- `DeploymentManifestModel.compliance_findings[]` — requires manifest model changes; deferred

## Context and Problem Statement

strata currently supports Checkov via the `script` policy type (ADR-0006), which runs Checkov as a subprocess against Terraform files. However, Checkov (by Snyk/Bridgecrew) is an industry-standard IaC security scanning tool used across DevSecOps workflows.

**Current limitations of the script-type approach:**

- Each Checkov invocation re-scans artifacts from disk; no caching or incremental analysis
- Subprocess overhead on every evaluation
- Limited integration with strata's build output (Checkov consumes `.tf` files, not strata's structured `platform.json`)
- No native reporting into strata's audit/compliance pipeline
- No policy customization feedback loop — Checkov results are not reconciled with strata's tenant/workspace context
- Duplicate scanning — users may run Checkov both in CI and in strata

**Opportunity:**

A first-class Checkov integration would:
- Run Checkov against strata's generated Terraform artifacts during build/plan phases
- Stream results into strata's policy framework and deployment manifest
- Map Checkov findings to strata resources (tenant, workspace, environment)
- Enable customization (silence rules per tenant, require passes for certain severity levels)
- Reduce duplication (single scan serves both strata and CI pipelines)

## Considered Options

### Option A: Keep script-type only

Rationale: Checkov is CLI-native; no need for deeper integration.

Consequences:
- ✅ No strata code changes (Checkov is standalone)
- ❌ Subprocess per evaluation (scan-time overhead)
- ❌ No caching (redundant scans of same artifacts)
- ❌ Limited integration (results not mapped to strata resources)
- ❌ No compliance audit trail in deployment manifest
- ❌ Misses opportunity for customization per tenant

### Option B: Embedded Checkov (Python library)

Rationale: Import Checkov as a Python package, call directly.

Consequences:
- ✅ No subprocess overhead
- ✅ Direct access to Checkov's internal data model
- ✅ Results available for immediate processing/filtering
- ❌ Hard dependency on Checkov (adds to strata's dependency tree)
- ❌ Version coupling (strata tied to specific Checkov versions)
- ❌ Maintenance burden (Checkov API compatibility)
- ❌ Not all Checkov features available via Python API (some CLI-only)

### Option C: First-class integration with Checkov CLI + local caching

Rationale: Invoke Checkov CLI, cache results, map findings to strata resources.

Consequences:
- ✅ Leverages Checkov's CLI (latest features, no API coupling)
- ✅ Local caching (avoid redundant scans of unchanged artifacts)
- ✅ Results mapped to strata resource model
- ✅ Findings recorded in deployment manifest + audit trail
- ✅ Customization per tenant (silence rules, set severity gates)
- ✅ Graceful degradation (soft dependency; falls back if Checkov unavailable)
- ⚠️ Subprocess overhead still exists (once per cache miss)
- ⚠️ Cache invalidation logic (when to re-scan)
- ⚠️ Mapping logic (translating Checkov findings to strata resources)

### Option D: Hybrid — option C + embedded library for performance-critical scans

Rationale: CLI for full scans, library for incremental/delta checks.

Consequences:
- ✅ Best of both worlds (CLI features + direct library access)
- ❌ Maintenance burden (two code paths, version tracking)
- ❌ Increased complexity (dual integration)
- ❌ Inconsistent behavior (CLI vs library may diverge)

## Decision Outcome

**Chosen: Option C — First-class integration with Checkov CLI + local caching + resource mapping.**

Rationale:
1. **CLI-native** (leverages Checkov's latest features, no Python API coupling)
2. **Caching** (avoid redundant scans of unchanged Terraform artifacts)
3. **Resource mapping** (findings tied to strata resources, tenant context)
4. **Audit trail** (findings recorded in deployment manifest, searchable)
5. **Customization** (silence rules per tenant, severity gates)
6. **Soft dependency** (graceful fallback if Checkov unavailable)
7. **Aligns with DevSecOps** (many teams already use Checkov in CI)

## Data Pipeline: strata → Checkov

### Startup Phase

1. **Bootstrap check:** `strata policy activate checkov` (explicit opt-in)
2. **Discover Checkov:** Look for `checkov` CLI in PATH
3. **Verify setup:** `checkov --version`, optionally run `checkov --list | grep framework`
4. **Load custom rules:** Optionally bootstrap with `.strata/checkov/` custom checks

### Build Phase: Terraform Artifact Scan

```
strata build run deploy/deploy-prd.yaml
  ↓
  Build controller generates Terraform artifacts
    ├─ build/terraform/main.tf
    ├─ build/terraform/providers.tf
    ├─ build/terraform/variables.tf
    └─ build/terraform/outputs.tf
  ↓
  PolicyEngine detects Checkov-type policies
    ↓
    Checkov integration computes artifact hash
      ├─ Hash unchanged? Return cached results
      └─ Hash changed? Proceed to scan
    ↓
    Run Checkov:
      checkov \
        --framework terraform \
        --directory build/terraform/ \
        --compact \
        --output json \
        --skip-check CKV_DOCKER_* \  # optional: tenant-specific silences
        > build/checkov-results.json
    ↓
    Cache results:
      .strata/cache/
        ├─ terraform-{hash}.json      # Checkov results, keyed by artifact hash
        └─ manifest.json              # hash -> (timestamp, tenure)
    ↓
    Parse Checkov output (JSON)
    ↓
    Map findings to strata resources:
      For each finding in checkov-results.json:
        - Extract file path (e.g., "build/terraform/network.tf")
        - Find strata resource in platform.json that generated this file
        - Enrich finding with resource metadata (tenant, workspace, environment)
        - Store as PolicyResult + ManifestFinding
    ↓
    Aggregate statistics
      ├─ Total checks: 142
      ├─ Passed: 128
      ├─ Failed: 14 (6 critical, 5 high, 3 medium)
      └─ Skipped: 0
```

### Plan Phase: Detect Resource Changes

```
strata build plan deploy/deploy-prd.yaml
  ↓
  Compute Terraform plan JSON: terraform show -json
  ↓
  Extract resource changes (create, update, delete)
  ↓
  Run Checkov on plan:
      checkov \
        --framework terraform \
        --check-id CKV_AWS_* \         # scan only on changes
        --input <(terraform show -json) \
        > build/checkov-plan.json
  ↓
  Map findings to resource deltas (new resources, modified resources)
```

### Deploy Phase: Compliance Gate

```
strata deploy run deploy/deploy-prd.yaml --dry-run
  ↓
  Deployment manifest includes Checkov findings from build phase
  ↓
  Evaluate policy enforcement:
    ├─ enforcement: deny  → abort if critical/high found
    ├─ enforcement: warn  → log violations, continue
    └─ enforcement: audit → record only
  ↓
  Record in deployment manifest:
    ├─ compliance_findings[]
    ├─ compliance_passed: true/false
    ├─ timestamp
    └─ scanned_artifact_hash
```

## Data Formats Provided to Checkov

### Input: Terraform Artifacts

Checkov consumes Terraform files directly:

```
build/terraform/
  ├─ main.tf                    # strata-generated resource definitions
  ├─ providers.tf               # provider configurations
  ├─ variables.tf               # Checkov validates variable constraints
  ├─ outputs.tf                 # optional: output validation
  └─ terraform.tfvars.json      # optional: variable values for checks
```

strata does NOT serialize internal models to HCL — Checkov consumes the native `.tf` files.

### Output: Checkov JSON

Checkov produces JSON that strata consumes:

```json
{
  "framework": "terraform",
  "checks": [
    {
      "id": "CKV_AWS_144",
      "name": "Ensure S3 bucket versioning is enabled",
      "check_type": "resource",
      "results": {
        "passed_checks": [
          {
            "resource": "aws_s3_bucket.example",
            "file_path": "/build/terraform/storage.tf",
            "file_line_range": [1, 12],
            "check_id": "CKV_AWS_144",
            "code_block": [["resource \"aws_s3_bucket\" \"example\" {", "...}"]]
          }
        ],
        "failed_checks": [
          {
            "resource": "aws_s3_bucket.no_versioning",
            "file_path": "/build/terraform/storage.tf",
            "file_line_range": [20, 28],
            "check_id": "CKV_AWS_144",
            "code_block": [["resource \"aws_s3_bucket\" \"no_versioning\" {", "...}"]]
          }
        ]
      }
    }
  ]
}
```

### Enriched: strata Compliance Finding

After mapping to strata resources:

```json
{
  "checkov_check_id": "CKV_AWS_144",
  "checkov_check_name": "Ensure S3 bucket versioning is enabled",
  "severity": "medium",
  "status": "failed",
  "resource": {
    "strata_kind": "resource",
    "strata_name": "example-bucket",
    "strata_file_path": "build/terraform/storage.tf",
    "strata_file_line_range": [20, 28]
  },
  "context": {
    "tenant": "acme",
    "workspace": "prod-us-east-1",
    "environment": "us-east-1"
  },
  "remediation": "Add 'versioning { enabled = true }' to the resource"
}
```

## Revised Scope (2026-07-23)

The original implementation approach was over-engineered. Comparing against the existing patterns
in the codebase (`CveScannerIntegration` + `CveMaxSeverityPolicy`, `InfracostIntegration` +
`CostThresholdPolicy`), the actual integration is straightforward:

**What the original ADR over-specified:**
- File-based `.strata/cache/checkov/` cache — not needed; Checkov is fast for typical Terraform
  artifact sizes, and neither `cve_scanner` nor `infracost` use file-based caching
- New CLI commands (`strata policy activate checkov`, `strata policy checkov scan/cache/silence/report`) — no
  other integration has dedicated CLI commands; policy YAML handles all configuration
- `ComplianceFinding` data model enriching Checkov results with strata resource context — useful
  eventually but out of scope for first-class integration; `PolicyResult.details` is sufficient
- `DeploymentManifestModel.compliance_findings[]` — requires manifest model changes; deferred
- Plan phase scanning via `terraform show -json` pipe — deferred to Phase 2

**What is actually needed (Phase 1 — buildable in one session):**

1. `src/strata/integrations/checkov.py` — `CheckovIntegration(BaseIntegration)` with:
   - `COMMAND = "checkov"`
   - `scan(terraform_dir, skip_checks, include_checks, custom_checks_dir, timeout)` → `CheckovScanResult`
   - `_parse_output(raw_json)` → `CheckovScanResult`
   - `get_version_command()`, `parse_version()`, `get_setup_info()`, `ensure_available()`

2. `src/strata/integrations/checkov_models.py` — `CheckovFinding` + `CheckovScanResult` dataclasses

3. `src/strata/validators/policies/checkov_policy.py` — `CheckovPolicy(BasePolicy)` with:
   - Resolves terraform artifact dir from `context.build_path`
   - Instantiates `CheckovIntegration`, calls `scan()`
   - Applies `severity_gate` and `skip_checks` from `policy.configuration`
   - Returns `PolicyResult` with violations and details

4. Register `"checkov"` in `IntegrationFactory._BUILTIN_CLASS_MAP` and `PolicyEngine._create()`

5. Tests — integration unit tests + policy unit tests (mock subprocess)

**Configuration (unchanged from original ADR):**

```yaml
policies:
  - name: terraform_security_baseline
    type: checkov
    phase: build
    enforcement: deny
    configuration:
      framework: terraform           # default: terraform
      severity_gate: high            # fail if high or critical found (critical|high|medium|low)
      skip_checks:                   # CKV IDs to suppress
        - CKV_AWS_1
        - CKV_AWS_20
      custom_checks_dir: ".strata/checkov/custom/"  # optional
      timeout: 120                   # seconds, default 120
```

**`CheckovScanResult` (minimal, no strata resource mapping):**

```python
@dataclass
class CheckovFinding:
    check_id: str          # e.g. "CKV_AWS_144"
    check_name: str
    severity: str          # CRITICAL | HIGH | MEDIUM | LOW | UNKNOWN
    resource: str          # e.g. "aws_s3_bucket.example"
    file_path: str
    guideline: str         # remediation URL from Checkov

@dataclass
class CheckovScanResult:
    passed: int
    failed: int
    skipped: int
    findings: List[CheckovFinding]
    scanner_version: str
    framework: str
```

**Graceful degradation (same pattern as cve_scanner):**
- Checkov not installed → `PolicyResult(passed=True, details={"skipped": "checkov not found"})`
- Build path unavailable → skip
- Terraform artifacts don't exist → skip
- Scan subprocess fails → skip with warning (non-fatal)

---

## Implementation Approach

### New Components

1. **`integrations/checkov.py`** — Checkov CLI integration

```python
class CheckovIntegration(BaseIntegration):
    """Wrapper around Checkov CLI."""
    
    def __init__(self, framework: str = "terraform"):
        self.framework = framework
        self.enabled = self._check_availability()
        self.cache = {}  # {artifact_hash: results}
    
    def scan(self, artifact_path: str, custom_checks: Optional[str] = None) -> Dict[str, Any]:
        """Run Checkov on artifacts; return JSON results."""
        artifact_hash = self._compute_hash(artifact_path)
        
        if artifact_hash in self.cache:
            return self.cache[artifact_hash]
        
        result = self._run_checkov(artifact_path, custom_checks)
        self.cache[artifact_hash] = result
        return result
    
    def _run_checkov(self, path: str, custom_checks: Optional[str]) -> Dict[str, Any]:
        """Invoke: checkov --framework terraform --directory <path> --output json"""
    
    def map_findings_to_resources(
        self,
        checkov_results: Dict[str, Any],
        platform_artifact: PlatformArtifactModel,
        context: PolicyContext
    ) -> List[ComplianceFinding]:
        """Map Checkov findings to strata resources."""
```

2. **`validators/policies/checkov_policy.py`** — Checkov policy type

```python
class CheckovPolicy(BasePolicy):
    """Evaluate Checkov security scan results."""
    
    def __init__(self, policy_model: PolicyModel):
        super().__init__(policy_model)
        self.checkov_integration = integrations.get("checkov")
    
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        """Run Checkov, map findings, return policy result."""
        artifact_dir = context.build_path / "terraform"
        checkov_results = self.checkov_integration.scan(str(artifact_dir))
        
        findings = self.checkov_integration.map_findings_to_resources(
            checkov_results,
            context.platform_artifact,
            context
        )
        
        # Apply filters: severity gate, skipped checks per tenant
        filtered = self._apply_tenant_filters(findings, context)
        
        # Determine pass/fail
        has_critical = any(f.severity == "critical" for f in filtered)
        has_high = any(f.severity == "high" for f in filtered)
        
        passed = not (has_critical or (has_high and self.policy.enforcement == "deny"))
        
        return PolicyResult(
            passed=passed,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=[f.remediation for f in filtered if not f.passed],
            details={"findings": [f.model_dump() for f in filtered]}
        )
    
    def _apply_tenant_filters(self, findings: List[ComplianceFinding], context: PolicyContext) -> List[ComplianceFinding]:
        """Silence rules per tenant, adjust severity gates."""
```

3. **`models/compliance_finding_model.py`** — ComplianceFinding model

```python
class ComplianceFinding(BaseModel):
    """Represents a single Checkov finding mapped to a strata resource."""
    checkov_check_id: str
    checkov_check_name: str
    severity: str  # critical | high | medium | low
    status: str    # passed | failed
    resource: ComplianceResourceContext
    context: ComplianceTenantContext
    remediation: str
    mapped_at: datetime
```

4. **CLI commands**

```bash
strata policy activate checkov --framework terraform
strata policy checkov scan <path>              # Run Checkov, cache results
strata policy checkov cache clear              # Clear scan cache
strata policy checkov silence CKV_AWS_1 --tenant acme  # Silence check per tenant
strata policy checkov report                   # Compliance summary across all scans
```

### Integration Points

- `PolicyEngine.register_type("checkov", CheckovPolicy)` — register Checkov as a built-in type
- `run_build_command` — scan Terraform artifacts after generation
- `run_plan_command` — optional: scan plan JSON for resource deltas
- `DeploymentManifestModel.compliance_findings` — store findings in manifest
- `ManifestPolicyResultModel` — record Checkov results as policy verdicts

### Configuration

```yaml
# configuration.spec.policies
- name: terraform_security_baseline
  type: checkov
  phase: build
  enforcement: deny
  description: "Scan Terraform for CIS AWS Foundations Benchmark violations"
  configuration:
    framework: terraform
    severity_gate: "high"           # fail if high or critical found
    skip_checks:                    # tenant-specific silences
      - "CKV_AWS_1"  # S3 versioning (false positive for this customer)
      - "CKV_AWS_20"
    include_checks: ~               # if empty, run all checks
    custom_checks_dir: ".strata/checkov/custom/"  # optional: custom rules
    timeout: 120
```

## Cache and Invalidation

Cache is stored locally:

```
.strata/cache/
  ├─ checkov/
  │   ├─ terraform-{hash}.json         # Checkov results keyed by artifact hash
  │   ├─ {hash}.meta.json              # { timestamp, artifact_paths, tenant, workspace }
  │   └─ manifest.json                 # Cache index
```

**Invalidation triggers:**
- Checkov version changes (detected via CLI version output)
- Terraform artifacts changed (detected via file hash)
- Custom checks modified (detected via custom rules dir hash)
- Manual cache clear: `strata policy checkov cache clear`

**TTL:** Cache persists indefinitely until one of the above triggers invalidation.

## Trade-Offs and Consequences

### Positive

- ✅ Leverages Checkov's full feature set (CLI is primary interface)
- ✅ Caching reduces scan overhead for unchanged artifacts
- ✅ Findings mapped to strata resources (context-aware audit trail)
- ✅ Customization per tenant (silence rules, severity gates)
- ✅ Compliance findings recorded in deployment manifest
- ✅ Teams already using Checkov see consistent results
- ✅ Graceful fallback if Checkov unavailable
- ✅ No hard dependency (soft, like other integrations)

### Negative

- ❌ Subprocess overhead on first scan (cache miss)
- ❌ Cache invalidation logic (when to re-scan; complexity)
- ❌ Mapping logic (translating Checkov findings to strata resources; parsing JSON)
- ❌ Terraform-specific (strata can use other IaC, but Checkov scanning limited to Terraform initially)
- ❌ Maintenance burden (tracking Checkov API changes, new frameworks)

### Neutral

- ~ Additional storage for cache (minimal; ~few MB per scan)
- ~ Learning curve for Checkov rule syntax (users familiar with DevSecOps already know it)

## Future Considerations

1. **Multi-framework support** — Extend to CloudFormation, Kubernetes, Helm, Dockerfile (Checkov supports all)
2. **Remote caching** — Share cache across distributed builds (e.g., S3-backed cache)
3. **Trend analysis** — Track compliance findings over time (build->build delta detection)
4. **Integration with approval workflows** — ADR-0032 could gate approvals on Checkov severity thresholds
5. **Custom check marketplace** — Community Checkov checks for strata resources

## Related Decisions

- **ADR-0006** — Policy engine (native policies + script escape hatch)
- **ADR-0031** — Cost estimation (similar model: generate artifact, analyze, cache results)
- **ADR-0032** — Approval workflows (findings could become approval conditions)
- **ADR-0033** — GitHub PR integration (findings could be posted as PR comments)
- **ADR-0003** — Layered architecture (integrations layer, where Checkov lives)

## Glossary

- **Checkov** — Open-source IaC security scanning tool by Snyk/Bridgecrew
- **CKV_*** — Checkov Check ID (e.g., CKV_AWS_144)
- **Framework** — IaC tool Checkov scans (terraform, cloudformation, kubernetes, helm, dockerfile, etc.)
- **ComplianceFinding** — Enriched Checkov result with strata context (tenant, workspace, resource)
- **Artifact hash** — SHA256 of Terraform artifact directory; used to invalidate/refresh cache

---

## Revised: 2026-09-16 — artifact path resolution bug + provisioner `scope`

### Problem

The Phase 1 scope note above (2026-07-23) explicitly specified `CheckovPolicy` would resolve
"terraform artifact dir from `context.build_path`" — as literally shipped, `_resolve_terraform_dir()`
hand-rolls three guessed candidate directories (`{build_path}/{deployment_name}/terraform/`,
`{build_path}/terraform/`, `{build_path}/` itself), none of which consult a provisioner's
`source.source_path` / `source.target_path`. For any workspace whose terraform provisioner uses a
nested `source_path` (e.g. `control/terraform`) — the normal case for any multi-provisioner
workspace — none of the three candidates match where the builder actually copies IaC source, and
the policy returns `PolicyResult(passed=True, details={"skipped": "no Terraform artifacts found in
build path"})`. A `deny`-enforcement security policy silently passes with nothing scanned.

Compounding this, `run_build_command.py`'s `_evaluate_build_policies()` — the code path `strata
build run` actually uses — never passed `deployment_service` into `PolicyContext` at all, so even
the policy's own best-case candidate (`context.deployment_service.get_build_path(...) / "terraform"`)
was dead code in production; only the two flatter, still-wrong candidates were ever tried.

ADR-0071 already established the fix for exactly this class of bug for the builder/deployer pair:
`SolutionController.get_provisioner_path()` is the single source of truth for a provisioner's build
output directory (`target_path` if set, else `source_path`, joined onto
`deployment_service.get_build_path(build_path)`), used by both `TerraformBuilder` (copy destination)
and `TerraformDeployer` (working directory). `CheckovPolicy` was never updated to use it — this
revision closes that gap.

### Decision

1. **Path resolution** — `CheckovPolicy` resolves each scanned provisioner's directory via
   `solution_controller.get_provisioner_path(deployment_service, build_path, prov)`, mirroring
   `TerraformDeployer._get_working_dir()`'s fallback shape when no `solution_controller` is present
   (library/test use). `PolicyContext` gains a `solution_controller: Optional[Any] = None` field
   (same `Any`-typed convention as `deployment_service`, to avoid a circular import), populated from
   `self._solution_controller` — already unconditionally constructed in `BaseCommand.__init__` — at
   every `PolicyContext(...)` call site (`run_build_command.py`, `check_policy_command.py`,
   `run_deploy_command.py`, `base_deploy_command.py`). `run_build_command.py` also starts passing
   `deployment_service`, fixing the dead-code candidate noted above.

2. **No duplicate reachability logic, and one standardized resolution behavior** — determining
   which terraform provisioner(s) to scan reuses the stage-reachability resolution that already
   exists, duplicated, in `BaseDeployer._resolve_iac_model()` (stage → provisioner) and
   `TerraformBuilder._stages_for_provisioner()` (provisioner → matching stages). Both are
   extracted into one shared helper (`strata/utils/provisioner_resolution.py` —
   `resolve_stage_provisioner_name()` + `stage_reachable_provisioner_names(workspace_model,
   stages)`) that all three call sites, including the new `CheckovPolicy` scope filter, import —
   per the "one implementation, not copies" rule for introducing new conventions.

   **The two existing implementations were not actually equivalent before this fix**, found while
   extracting the shared helper:
   - `BaseDeployer._resolve_iac_model()` falls through across all three priorities even after an
     explicit-but-invalid reference — a typo'd `stage.provisioner` name still recovers via
     `stage.topology` or the sole-provisioner fallback, logging only a `warning` (easy to miss).
   - `TerraformBuilder._stages_for_provisioner()` treats the three priorities as mutually
     exclusive (`if`/`elif`/`elif`) — a typo'd `stage.provisioner` name resolves to nothing, with
     no warning at all (this copy only feeds secret-scoping, not a hard resolution path).

   **Standardized on the strict (mutually-exclusive) behavior everywhere**: the first applicable
   priority — `stage.provisioner`, else `stage.topology`, else the sole workspace provisioner —
   wins outright; an explicit-but-unresolvable `stage.provisioner`/`stage.topology` reference is a
   hard resolution failure, never a silent fallback to a different provisioner. Silent recovery
   from a wrong/typo'd explicit reference is the same class of bug this whole revision exists to
   fix (a quiet fallback masking a real config error), so `BaseDeployer` loses its fall-through
   rather than the new code inheriting it.

   **Compatibility note:** this changes real `BaseDeployer` behavior — used by every deployer
   (terraform/bicep/ansible), not just Checkov. A workspace that today has a typo'd
   `stage.provisioner` which happens to still resolve via topology or a sole-provisioner fallback
   (with only a buried warning log) will, after this change, fail `validate_workspace()` outright
   with the existing "cannot resolve a terraform provisioner" error instead of silently deploying
   against a different provisioner than named. This is the intended, correct outcome, but it is a
   behavior change worth its own `CHANGELOG.md` callout ("may surface previously-silent
   stage/provisioner config errors") rather than folding it silently into the Checkov fix.

3. **New `configuration.scope` field** — controls which terraform provisioner(s) are scanned when a
   workspace declares more than one (e.g. a root `control_infra` provisioner plus a shared
   `core_modules` module-library provisioner that no stage targets directly):

   - `staged` (default) — provisioners reachable from at least one deployment stage, via
     `stage.provisioner` or `stage.topology → topology.provisioner` (the same reachability set
     `stage_reachable_provisioner_names()` computes). Named `staged`, not `root`, because there is
     no actual parent/child hierarchy in the schema — the name describes the literal mechanism
     instead of implying a tree that doesn't exist.
   - `all` — every `provisioner: terraform` entry in the workspace, staged or not.
   - `<stage-name>` (e.g. `infrastructure`) — provisioner(s) reachable from that one named stage
     only. Feasible without making `build` stage-aware at runtime: the full static stage list
     (`deployment_service.model.spec.stages`) is already available at build time, so this is just a
     narrower filter over the same data, not a runtime binding to "when that stage deploys." An
     unrecognized stage name degrades gracefully (skip + warning listing valid stage names), same
     as an invalid `severity_gate` today.
   - `configuration.scope` is unrelated to `stages[].scope` (an existing, different, free-form
     CLI-filter label) despite the shared field name — different namespace
     (`policies[].configuration.scope` vs `stages[].scope`), called out explicitly here to avoid
     confusion.

   Aggregation across multiple selected provisioners: any single provisioner breaching
   `severity_gate` denies the whole policy result (AND semantics, matching how a single
   `enforcement: deny` policy already behaves) — findings are still reported per provisioner in
   `PolicyResult.details` and violation strings are prefixed with the provisioner name
   (e.g. `[control_infra] CKV_AWS_1: ...`).

4. **Silent skip becomes a visible warning** — `PolicyResult` gains a `warnings: List[str]` field
   (mirrors the `stage_warnings` pattern from the 1.10.0 deploy-output work). All four of
   `CheckovPolicy`'s graceful-degradation paths (no artifacts found, Checkov not installed, scan
   subprocess failed, invalid `severity_gate`/`scope`) populate an explicit, actionable message.
   Every `PolicyContext` call site echoes `result.warnings` as `⚠` lines unconditionally — even when
   `passed=True` — so a skipped enforcement policy is never silent again.

### Not in scope: multi-framework support

Checkov itself scans far more than Terraform (CloudFormation, Kubernetes, Helm, Dockerfile, ARM/Bicep,
Serverless Framework, ...), and `CheckovIntegration.scan()` already accepts a `framework` parameter
that defaults to `"terraform"`. In practice strata's integration is Terraform-only end-to-end today:
the parameter is named `terraform_dir`, and `CheckovPolicy`'s path resolution only ever looks for
`.tf` files. Setting `configuration.framework` to anything else currently finds nothing and skips —
that gap is unrelated to this revision's bug and is left as the pre-existing "Multi-framework
support" item under Future Considerations above; this fix stays scoped to `provisioner: terraform`
entries only (same scope `get_provisioner_path()` and the new `scope` config both assume).

### Implemented (2026-09-16 follow-up): Bicep and Ansible support

Verified against Checkov's actual supported frameworks (CLI/README): Terraform, Terraform Plan,
CloudFormation, AWS SAM, Kubernetes, Helm, Kustomize, Dockerfile, Serverless Framework, Ansible,
Bicep, ARM, and OpenTofu. Cross-referenced against strata's own provisioner types and how each one's
build artifacts are actually laid out on disk:

| strata provisioner | Checkov framework?                               | Build-time path shape                                                                                                | Verdict                                                                          |
| ------------------ | ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `terraform`        | `terraform`                                      | `get_provisioner_path()` — flat, one dir per provisioner                                                             | done                                                                             |
| `bicep`            | `bicep`                                          | `get_provisioner_path()` — flat, one dir (confirmed `bicep_builder.py` copies source there, same shape as terraform) | done                                                                             |
| `ansible`          | `ansible`                                        | `get_provisioner_path()` — flat, one dir (confirmed `ansible_builder.py` does the same)                              | done                                                                             |
| `helm`             | `helm`                                           | `get_module_build_path(namespace, module)` — **one dir per namespace+module pair**, not one dir per provisioner      | structurally different — needs its own design pass, not a drop-in generalization |
| `compose`          | none — Compose is not a Checkov framework at all | —                                                                                                                    | not supported by Checkov, period                                                 |
| `argocd` / `flux`  | `kubernetes` (of the rendered manifests)         | no local build-time source — these "render from the platform artifact" per the schema                                | nothing on disk at build time to point Checkov at, normally                      |
| `script`           | —                                                | arbitrary user script, not IaC                                                                                       | not applicable                                                                   |

**Implemented:**

1. `_FRAMEWORK_PROVISIONER_MAP` replaces the hardcoded `ProvisionerType.TERRAFORM` filter:
   ```python
   _FRAMEWORK_PROVISIONER_MAP = {
       "terraform": (ProvisionerType.TERRAFORM, ("*.tf",)),
       "bicep": (ProvisionerType.BICEP, ("*.bicep",)),
       "ansible": (ProvisionerType.ANSIBLE, ("*.yml", "*.yaml")),
   }
   ```
   `configuration.framework` (already existed, defaults to `"terraform"`) also selects the provisioner
   type via reverse lookup. `_resolve_terraform_dirs()` was renamed `_resolve_provisioner_dirs()` and
   takes `framework` as a third argument — everything else (`scope: staged|all|<stage-name>`,
   `get_provisioner_path()` resolution, per-provisioner aggregation, `warnings`) needed no changes,
   since none of it was actually Terraform-specific once the provisioner-type filter and file-glob
   were parameterized.
2. `helm` remains deliberately excluded — scanning it properly means enumerating every
   `namespaces[].modules[]` pair reachable from the policy's `scope`, then running Checkov once per
   rendered chart directory (or once over the whole namespace). Left as a separate follow-up design,
   not folded into this generalization.
3. `compose`/`argocd`/`flux`/`script` stay unsupported — configuring `framework: compose` (etc.) is a
   clean, explicit skip+warning ("framework 'compose' has no supported provisioner mapping — use one
   of: ansible, bicep, terraform"), not a crash.
4. **One policy instance scans one framework**, confirmed as implemented — `skip_checks`/
   `severity_gate`/finding IDs live in unrelated namespaces per framework (`CKV_AWS_*` vs
   `CKV_ANSIBLE_*` vs the Bicep/ARM checks), so mixing them in one enforcement rule would make
   `skip_checks` ambiguous about which framework's check it silences. A workspace wanting both
   Terraform and Ansible coverage declares two `checkov` policies,
   one per framework.

### Configuration (supersedes the 2026-07-23 example)

```yaml
policies:
  - name: terraform_security_baseline
    type: checkov
    phase: build
    enforcement: deny
    configuration:
      framework: terraform          # default: terraform | bicep | ansible
      severity_gate: high           # critical|high|medium|low (default: high)
      scope: staged                 # staged (default) | all | <stage-name>
      skip_checks: []
      include_checks: []
      custom_checks_dir: ".strata/checkov/custom/"
      timeout: 120
```

### Remaining work

- Implementation (this revision records the design only — see the tracking session for
  implementation status).
- `.github/HISTORY.md` entry (full root-cause/fix/testing narrative) and a terse
  `.github/CHANGELOG.md` bullet pointing back here, once implemented.
