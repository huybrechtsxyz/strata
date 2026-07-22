# OPA (Open Policy Agent) as a first-class integration

- Status: pending
- Date: 2026-07-22
- Supersedes: Partial aspects of ADR-0006 (policy-engine-for-deployment-guardrails)

## Context and Problem Statement

strata currently supports OPA via the `script` policy type (ADR-0006), which runs OPA as a subprocess and passes JSON context on stdin. However, OPA is an industry-standard policy framework used across CNCF ecosystems, Kubernetes, Terraform workflows, and enterprise security compliance processes.

**Current limitations of the script-type approach:**

- Subprocess overhead — each policy evaluation spawns a new process
- No state sharing — OPA runs in isolation; policies cannot build cross-evaluation context
- Data serialization friction — strata must convert internal models to JSON/YAML, OPA converts back; information loss during round-trips
- Limited policy reusability — policies written for OPA's `rego` language cannot easily be vendored or composed
- No native bundle support — OPA supports policy bundles for version-controlled, deployed-as-artifacts workflows; strata's script approach doesn't leverage this

**Opportunity:**

Organizations using strata likely already have OPA policies in their infrastructure (Kubernetes admission controllers, Terraform policy-as-code, drift detection). Offering OPA as a **first-class integration** with:
- Native data pipeline (strata → OPA)
- Unified policy authoring (single `rego` source of truth for OPA across all tools)
- Bundle/artifact lifecycle (policies versioned, tested, deployed alongside strata workflows)
- State sharing (OPA can build context across multiple policy phases)

...would reduce friction and open strata to teams already invested in OPA.

## Considered Options

### Option A: Keep script-type only

Rationale: No extra dependencies, users bring OPA themselves.

Consequences:
- ✅ Zero additional code in strata core
- ❌ Subprocess overhead on every evaluation
- ❌ No data schema guarantee — strata format not stable for OPA consumption
- ❌ Misses opportunity to align with CNCF ecosystem

### Option B: Dedicated OPA integration with direct API

Rationale: Embed OPA as a library (Go + CGO bindings) for direct policy evaluation.

Consequences:
- ✅ Zero subprocess overhead, direct library calls
- ✅ State sharing across evaluations
- ❌ Hard dependency on Go/CGO (strata is Python-native; breaks Windows support)
- ❌ Build complexity (Go toolchain required)
- ❌ Deployment complexity (binary distribution)

### Option C: OPA integration via gRPC

Rationale: Run OPA as a long-lived sidecar gRPC server; strata connects to it.

Consequences:
- ✅ State sharing across evaluations
- ✅ Language-agnostic (OPA runs standalone)
- ✅ Deployment optionality (local sidecar or remote server)
- ✅ Policy bundle versioning + artifact lifecycle
- ⚠️ Extra complexity: bootstrapping OPA server, connection handling, lifecycle
- ⚠️ Requires user to have OPA CLI available (soft dependency)

### Option D: Hybrid — first-class integration + script fallback

Rationale: Offer gRPC integration for teams using OPA, keep script-type for one-off use.

Consequences:
- ✅ Best of both: native integration + escape hatch
- ✅ Gradual adoption (users try gRPC, can fall back to script)
- ⚠️ Code maintenance burden (two code paths)
- ⚠️ User must understand when to use each

## Decision Outcome

**Chosen: Option C — OPA integration via gRPC**, with fallback to script-type when OPA unavailable.

Rationale:
1. **Zero subprocess overhead per evaluation** (gRPC calls vs process spawn)
2. **Language-agnostic** (OPA runs standalone, no CGO)
3. **State sharing** (OPA maintains evaluation context across phases)
4. **Bundle lifecycle** (policies versioned, promoted, deployed as artifacts)
5. **Graceful degradation** (if OPA unavailable, fall back to script-type or built-in policies)
6. **Aligns with CNCF ecosystem** (many teams already run OPA for K8s/Terraform)

## Data Pipeline: strata → OPA

### Startup Phase

1. **Bootstrap check:** `strata policy activate opa` (explicit opt-in)
2. **Discover OPA:** Look for `opa` CLI in PATH, or require `OPA_ENDPOINT` env var
3. **Start OPA server:** 
   ```
   opa run --server --addr localhost:8181 --bundle <bundle-path>
   ```
   Or: connect to remote OPA at `OPA_ENDPOINT`
4. **Load bundle:** Optionally bootstrap with `.rego` policies from `.strata/policies/` or a policy artifact repository

### Policy Evaluation Flow

Each policy phase (validate, build, plan, deploy) evaluates OPA policies via gRPC:

```
strata build run [...]
  ↓
  PolicyEngine.evaluate()
    ↓
    OPA integration detects OPA-type policy
      ↓
      Serialize evaluation context to JSON
        ├─ phase: "build"
        ├─ platform: <platform.json serialized>
        ├─ configuration: <configuration.yaml serialized>
        ├─ build_path: "/path/to/build"
        └─ metadata: <tenant, workspace, environment>
      ↓
      POST /v1/data/{rule} JSON {"input": {...}}
      ↓
      OPA evaluates rego policies
        ├─ access tenant zone restrictions
        ├─ validate naming conventions
        ├─ enforce required tags
        └─ custom rules
      ↓
      Return result: {"result": true/false, "violations": [...]}
      ↓
      PolicyResult.passed = true/false
```

### Data Formats Provided to OPA

strata exposes evaluation context as JSON to OPA:

```json
{
  "phase": "build",
  "enforcement": "deny",
  "platform": {
    "api_version": "strata.huybrechts.xyz/v1",
    "kind": "workspace",
    "meta": { ... },
    "spec": { ... }
  },
  "configuration": {
    "api_version": "strata.huybrechts.xyz/v1",
    "kind": "configuration",
    "meta": { ... },
    "spec": { ... }
  },
  "plan_data": { ... },                    # terraform show -json (if available)
  "deployment_manifest": { ... },          # manifest model (deploy phase only)
  "build_path": "/path/to/build",
  "work_path": "/path/to/workspace",
  "metadata": {
    "tenant": "acme",
    "workspace": "prod",
    "environment": "us-east-1",
    "timestamp": "2026-07-22T14:30:00Z"
  }
}
```

### OPA Policy Examples

```rego
# file: policies/zone_enforcement.rego
package strata.zones

import future.keywords.contains
import future.keywords.if

deny contains msg if {
    resource := input.platform.spec.resources[_]
    zone := resource.properties.zone
    allowed_zones := input.configuration.spec.tenant.allowed_zones
    not zone in allowed_zones
    msg := sprintf("Resource %s in zone %s not allowed (allowed: %v)", [resource.meta.name, zone, allowed_zones])
}

# file: policies/naming.rego
package strata.naming

import future.keywords.contains

deny contains msg if {
    resource := input.platform.spec.resources[_]
    name := resource.meta.name
    not regex.match("^[a-z][a-z0-9_-]*$", name)
    msg := sprintf("Resource name %s violates naming convention", [name])
}
```

### OPA Bundle Artifact Lifecycle

Policies are versioned as OPA bundles (Tar + manifests):

```
.strata/
  policies/
    bundle.tar.gz          # OPA bundle (policies + metadata)
    manifest.json          # Bundle metadata, signature, version
  bundles/
    strata-policies-v1.0.0/
      _manifest.json
      policies/
        zone_enforcement.rego
        naming.rego
        security_baseline.rego
```

Workflow:
1. **Author:** Write `.rego` policies locally
2. **Test:** `opa test -v policies/`
3. **Build:** `opa build -b policies/ -o bundle.tar.gz`
4. **Deploy:** Commit bundle to repo, tag with version
5. **Activate:** `strata policy activate opa --bundle-artifact v1.0.0`

## Implementation Approach

### New Components

1. **`integrations/opa.py`** — OPA gRPC integration

```python
class OPAIntegration(BaseIntegration):
    """Wrapper around OPA gRPC API."""
    
    def __init__(self, endpoint: str = "localhost:8181"):
        self.endpoint = endpoint
        self.enabled = self._check_availability()
    
    def evaluate(self, rule: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """POST /v1/data/{rule} with input JSON."""
        # Returns: {"result": {...}, "violations": [...]}
    
    def health_check(self) -> bool:
        """GET /health."""
    
    def load_bundle(self, bundle_path: str) -> None:
        """Activate OPA policy bundle."""
```

2. **`validators/policies/opa_policy.py`** — OPA policy type

```python
class OPAPolicy(BasePolicy):
    """Evaluate a policy rule via OPA gRPC."""
    
    def __init__(self, policy_model: PolicyModel):
        super().__init__(policy_model)
        self.opa_integration = integrations.get("opa")
    
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        """Serialize context, call OPA, return result."""
        input_data = self._serialize_context(context)
        rule = self.policy.configuration.get("rule")
        result = self.opa_integration.evaluate(rule, input_data)
        return PolicyResult(...)
```

3. **CLI commands**

```bash
strata policy activate opa --endpoint localhost:8181 --bundle path/to/bundle.tar.gz
strata policy opa health                    # Check OPA connection
strata policy opa test                      # Run OPA unit tests
strata policy opa bundle build <dir> <out> # Build bundle
```

### Integration Points

- `PolicyEngine.register_type("opa", OPAPolicy)` — register OPA as a built-in type
- `run_validate_command`, `run_build_command`, `run_plan_command`, `run_deploy_command` — call policy engine as before
- Graceful degradation: if OPA unavailable, fall back to script-type or warn + continue

### Configuration

```yaml
# configuration.spec.policies
- name: zone_check
  type: opa
  phase: build
  enforcement: deny
  configuration:
    endpoint: "localhost:8181"
    rule: "strata.zones.deny"     # OPA rego rule to evaluate
    bundle_version: "v1.0.0"       # optional: bundle artifact to load
```

## Trade-Offs and Consequences

### Positive

- ✅ Zero subprocess overhead per evaluation (gRPC calls)
- ✅ State sharing across evaluations (OPA maintains context)
- ✅ Policy bundles versioned as artifacts (GitOps workflow)
- ✅ Native OPA tooling integration (opa test, conftest, etc.)
- ✅ Teams using OPA elsewhere can reuse policies
- ✅ Aligns with CNCF + Kubernetes ecosystem
- ✅ Graceful fallback if OPA unavailable

### Negative

- ❌ Soft dependency: users must have `opa` CLI or run OPA server separately
- ❌ Extra server lifecycle to manage (start, stop, health checks)
- ❌ Bootstrap complexity (discovering OPA, loading bundles)
- ❌ gRPC error handling (connection failures, timeouts, malformed responses)
- ❌ Maintenance burden (gRPC client, API version compatibility)

### Neutral

- ~  Rego language learning curve for custom policies
- ~  Policy bundle versioning and artifact lifecycle (good for governance, extra process)

## Related Decisions

- **ADR-0006** — Policy engine (native policies + script escape hatch)
- **ADR-0035** — Enterprise store (policy artifacts stored alongside other enterprise resources)
- **ADR-0003** — Layered architecture (integrations layer, where OPA lives)

## Glossary

- **gRPC** — Google's RPC framework; OPA exposes `/v1/data/` and `/health` via HTTP/REST but also supports gRPC
- **Rego** — OPA's policy language
- **Bundle** — Versioned set of Rego policies + metadata; OPA's deployment unit
- **Conftest** — OPA CLI wrapper for testing; runs policies against data
