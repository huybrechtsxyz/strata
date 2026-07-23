# Checkov as a first-class integration

- Status: implemented (Phase 1)
- Date: 2026-07-22
- Revised: 2026-07-23
- Supersedes: Partial aspects of ADR-0006 (policy-engine-for-deployment-guardrails)

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
