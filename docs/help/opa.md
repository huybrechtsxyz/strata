# OPA (Open Policy Agent) Integration

OPA is a general-purpose policy engine for cloud-native environments. strata evaluates
Rego policies via two modes: an OPA HTTP server (fast, preferred) or `opa eval` CLI
(no server required).

Installation
```
# macOS
brew install opa

# Linux
curl -L -o opa https://openpolicyagent.org/downloads/latest/opa_linux_amd64_static
chmod 755 opa && mv opa /usr/local/bin/

# Windows (Scoop)
scoop install open-policy-agent
```

Verify install
```
opa version
```

Minimum recommended version: 0.50.0

Activation — CLI mode (no server required)

Place `.rego` policy files in `.strata/policies/` and declare the policy:

```yaml
policies:
  - name: zone_enforcement
    type: opa
    phase: build
    enforcement: deny
    configuration:
      rule: "data.strata.zones.deny"
      policy_dir: ".strata/policies/"   # relative to workspace root
      timeout: 30
```

Activation — HTTP server mode

Start OPA server before running strata:
```
opa run --server --addr 0.0.0.0:8181 --bundle .strata/policies/
```

Then set the endpoint in policy config or environment:
```yaml
configuration:
  rule: "data.strata.zones.deny"
  endpoint: "http://localhost:8181"
```

Or via environment variable:
```
export OPA_ENDPOINT=http://localhost:8181
```

Writing OPA policies

OPA rules must return a **set of violation message strings** under a `deny` rule:

```rego
package strata.zones

# Block resources outside allowed regions
deny contains msg if {
    resource := input.platform.spec.resources[_]
    not resource.properties.region in input.configuration.spec.allowed_regions
    msg := sprintf("Resource '%s' in disallowed region '%s'",
                   [resource.meta.name, resource.properties.region])
}
```

OPA input document (what strata sends)

```json
{
  "phase": "build",
  "platform": { ... },        // platform artifact model
  "configuration": { ... },   // configuration model
  "deployment": { ... },      // deployment model
  "plan_data": { ... },       // terraform plan (if available)
  "work_path": "/workspace",
  "build_path": "/workspace/.strata/build"
}
```

Mode selection

| Configured                                | Behavior                                           |
| ----------------------------------------- | -------------------------------------------------- |
| `endpoint` set or `OPA_ENDPOINT` env var  | Try HTTP server first; CLI fallback if unreachable |
| Neither set                               | CLI mode only (requires `opa` binary)              |
| Neither `opa` binary nor reachable server | Policy skips (passes), warning logged              |

Testing policies
```
opa test -v .strata/policies/
opa eval -d .strata/policies/ --stdin-input 'data.strata.zones.deny' < input.json
```

Graceful degradation
- OPA not installed and no server → policy skips (never blocks)
- `policy_dir` not found → policy skips
- Network error to server → falls back to CLI mode
- Rule returns empty set or false → pass (no violations)

Docs
- https://www.openpolicyagent.org/docs/latest/
- Rego language: https://www.openpolicyagent.org/docs/latest/policy-language/
