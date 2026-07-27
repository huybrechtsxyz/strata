# AI agent integration for build and deploy workflows

- Status: completed
- Date: 2026-07-07
- Completed: 2026-07-27

## Context and Problem Statement

Infrastructure-as-code workflows produce outputs that require human interpretation: Terraform plans, policy violations, SBOM vulnerability reports, drift analysis, deployment failures, and audit logs. Today an operator must manually review these artefacts, correlate errors across tools, and decide how to proceed. This is time-consuming, error-prone, and does not scale.

At the same time, the market increasingly expects developer tools to embed AI-assisted capabilities. strata already exposes an **MCP server** (read-only workspace status, validation, schema queries) and a **policy engine** — but neither can _act_ on what they find or produce natural-language guidance for operators.

Adding an AI agent integration layer would let strata call an LLM at well-defined points in the build/deploy lifecycle to **analyse, explain, and recommend** — without giving the agent autonomous control over infrastructure mutations.

### Concrete Use Cases

| Phase        | Trigger                    | What the Agent Does                                                                                    |
| ------------ | -------------------------- | ------------------------------------------------------------------------------------------------------ |
| **Validate** | Policy violations detected | Explain violations in plain language; suggest YAML fixes                                               |
| **Build**    | SBOM generated             | Analyse dependency risks (CVEs, deprecated packages, license issues); summarise findings               |
| **Build**    | Terraform plan produced    | Summarise resource changes; flag destructive operations (deletes, replacements); estimate blast radius |
| **Deploy**   | Provisioner step fails     | Parse Terraform/Ansible error output; diagnose root cause; suggest remediation                         |
| **Deploy**   | Drift detected             | Explain configuration vs. actual state delta; recommend reconciliation path                            |
| **Audit**    | Deployment completes       | Generate human-readable deployment summary from manifest; highlight anomalies vs. previous runs        |
| **Guide**    | Onboarding (guide show)    | Answer questions about workspace readiness in conversational style                                     |
| **Doctor**   | `env doctor` check fails   | Explain why a tool/environment check failed; provide step-by-step remediation                          |
| **Guide**    | Phase blocked              | Explain what is preventing readiness phase completion; suggest concrete next action                    |

### Why Not Just Use the MCP Server?

The MCP server (ADR MCP docs) provides _external_ AI tools read-only access to strata's workspace. That is valuable for IDE copilot experiences. But an **embedded** agent integration is different:

1. **Timing** — It runs _during_ the build/deploy pipeline, not after. The agent sees transient artefacts (plan JSON, partial error output) that are not persisted.
2. **Context** — It receives the full resolved platform model, environment variables, and stage history — context an external tool would have to reconstruct.
3. **Action gating** — It can _block_ a deployment (e.g., "plan contains 12 resource deletions, confirm?") based on LLM analysis, which an external tool cannot do.
4. **CI/CD integration** — It works headless in pipelines, not just in VS Code.

## Decision Drivers

- **Operator productivity** — Reduce time-to-resolution for build/deploy failures and plan reviews.
- **Safety** — AI must never autonomously mutate infrastructure. All actions are analyse/explain/recommend.
- **Opt-in** — Zero impact on users who do not configure an AI provider. No AI calls unless explicitly enabled.
- **Provider-agnostic** — Support multiple LLM backends (OpenAI, Azure OpenAI, Anthropic, Ollama, etc.) through a pluggable provider model.
- **Cost control** — Operators must be able to control when and how often the agent is invoked to manage API costs.
- **Offline capability** — Local models (Ollama) must be supported for air-gapped environments.
- **Layer discipline** — Respect the existing architecture (ADR-0003). The agent is an integration, not a controller.
- **Auditability** — Every AI invocation must be logged in the audit trail with prompt, response, and token usage.

## Considered Options

### Option A — AI Agent as an Integration (recommended)

Add a new `AiAgentIntegration` following the existing `BaseIntegration` pattern. Lifecycle hooks and deployer steps optionally call the agent when configured. The agent is a **passive advisor** — it returns analysis and recommendations but never executes commands.

### Option B — AI Agent as a Deployer

Create a new `AiDeployer` provisioner type that runs AI analysis as a stage in the deployment pipeline. Operators add an `ai_review` stage to their deployment YAML.

- Pro: Fits the existing stage model; no new integration type needed.
- Con: Conflates analysis with provisioning. A deployer that never deploys anything is architecturally misleading. Stages have ordering constraints that do not map well to "review this plan".

### Option C — AI Agent as a Policy

Add AI-powered policy types (e.g., `type: ai_plan_review`) to the policy engine. The policy calls an LLM to evaluate plan output and returns pass/fail.

- Pro: Natural gating mechanism; policies already block deploys.
- Con: Policies are boolean (pass/fail) — they are not designed for rich narrative output. An AI "policy" would need to smuggle its explanation into error messages, breaking the policy contract. Also does not cover post-failure diagnosis or audit summarisation.

### Option D — External Webhook / Event System

Emit events (webhooks, CloudEvents) at lifecycle points and let external systems (including AI agents) subscribe and respond.

- Pro: Maximum flexibility; strata remains AI-unaware.
- Con: Adds network dependency and latency. Requires external infrastructure. Loses the tight context (resolved values, transient artefacts) that makes embedded analysis valuable. Does not work offline.

## Decision Outcome

Chosen: **Option A — AI Agent as an Integration**, because it follows the proven `BaseIntegration` pattern, keeps the agent advisory-only, and provides the tightest context at each lifecycle point without architectural distortion.

Option C's policy approach can be supported _within_ Option A — an `ai_review` policy type delegates to the `AiAgentIntegration` — giving operators both rich analysis and gating in a single mechanism.

### Consequences

- Good: Operators get plain-language explanations of plans, failures, and drift without leaving the pipeline.
- Good: CI/CD pipelines can gate deployments on AI review (e.g., "flag any plan with >5 resource deletions").
- Good: Works with local models (Ollama) for air-gapped or cost-sensitive environments.
- Good: Follows existing integration and plugin patterns — minimal architectural novelty.
- Bad: Adds an optional dependency on LLM provider SDKs (openai, anthropic, httpx for Ollama).
- Bad: LLM responses are non-deterministic; operators must understand that recommendations are advisory.
- Bad: Token cost for large plans/SBOM reports can be significant — requires prompt engineering to stay within context windows.
- Neutral: No infrastructure mutations — the agent cannot fix problems, only explain them.

---

## Detailed Design

### 1. Architecture Position

```
commands/
  └── (existing commands — unchanged, call controllers)
controllers/
  └── (existing controllers — call agent integration at lifecycle points)
builders/
  └── (existing builders — call agent for SBOM/plan analysis after artefact generation)
deployers/
  └── (existing deployers — call agent on step failure or plan output)
validators/
  └── policies/
        └── ai_review_policy.py     # NEW — delegates to AiAgentIntegration
integrations/
  └── ai/
        ├── __init__.py
        ├── base_ai_provider.py     # ABC for LLM providers
        ├── ai_integration.py       # AiAgentIntegration (BaseIntegration subclass)
        ├── openai_provider.py      # OpenAI / Azure OpenAI
        ├── anthropic_provider.py   # Anthropic Claude
        ├── ollama_provider.py      # Local Ollama

# VS Code extension (TypeScript) — chat participant path only
src/vscode/src/providers/
  └── strataChatParticipant.ts     # Uses vscode.lm directly (no Python provider needed)
  └── aiPromptBuilder.ts           # Builds prompts from workspace context; reads .strata/prompts/
data/
  └── prompts/
        └── plan_review.py    # Terraform plan analysis prompt (built-in)
        ├── failure_diagnosis.py
        ├── sbom_analysis.py
        ├── drift_explanation.py
        ├── deployment_summary.py
        ├── policy_review.py
        ├── doctor_analysis.py     # env doctor failure explanation
        └── guide_assistance.py    # readiness phase guidance

# Workspace data (operator-managed, see Section 11)
.strata/
  └── prompts/                      # Optional — overrides built-in prompts
        ├── plan_review.md
        ├── failure_diagnosis.md
        └── <custom_name>.md
```

The `AiAgentIntegration` sits in the integrations layer — it wraps external LLM APIs the same way `TerraformIntegration` wraps the Terraform CLI.

### 2. Configuration Model

AI agents are configured in the **configuration YAML** (same level as other integrations):

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: myplatform
spec:
  integrations:
    - name: ai-advisor
      type: ai_agent
      endpoints:
        provider: azure_openai          # openai | azure_openai | anthropic | ollama | vscode_lm
        endpoint: https://my-aoai.openai.azure.com/
        model: gpt-4o
      authentication:
        type: env_var                   # env_var | secret_store | azure_cli | managed_identity | token | none
        value: AZURE_OPENAI_API_KEY
      settings:
        temperature: 0.1               # Low temperature for deterministic analysis
        max_tokens: 4096
        timeout: 60                     # seconds
```

For an API key stored in a secret store (reuses the existing `SecretStoreType` resolution path — same stores available to `spec.secrets` in environment YAML):

```yaml
    - name: ai-advisor
      type: ai_agent
      endpoints:
        provider: openai
        model: gpt-4o
      authentication:
        type: secret_store
        store: azure_keyvault         # azure_keyvault | bitwarden | vault | infisical
        value: my-openai-api-key      # key name in the secret store
```

For Azure OpenAI with Azure CLI authentication (no API key required — uses the token from `az login`):

```yaml
    - name: ai-advisor
      type: ai_agent
      endpoints:
        provider: azure_openai
        endpoint: https://my-aoai.openai.azure.com/
        model: gpt-4o
      authentication:
        type: azure_cli               # Acquires a bearer token via `az account get-access-token`
```

For local Ollama:

```yaml
    - name: ai-local
      type: ai_agent
      endpoints:
        provider: ollama
        endpoint: http://localhost:11434
        model: llama3
      authentication:
        type: none
```

`provider: vscode_lm` requires no configuration — authentication and model selection are handled by VS Code (e.g. GitHub Copilot). This provider is **only active when running inside the VS Code extension**; the CLI ignores it and falls back to the next configured provider.

```yaml
    - name: ai-ide
      type: ai_agent
      endpoints:
        provider: vscode_lm
        model: copilot-gpt-4o        # passed to vscode.lm.selectChatModels() as a hint
      authentication:
        type: none                   # VS Code manages auth transparently
```

### 3. Provider Interface

```python
class BaseAiProvider(ABC):
    """ABC for LLM provider implementations."""

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        context: dict[str, Any],
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> AiResponse:
        """Send a completion request and return the structured response."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider endpoint is reachable."""


@dataclass
class AiResponse:
    """Structured response from an AI provider."""
    content: str                  # The analysis text
    provider: str                 # Provider name (for audit)
    model: str                    # Model used
    prompt_tokens: int            # Input tokens consumed
    completion_tokens: int        # Output tokens generated
    duration_ms: int              # Wall-clock time
    cached: bool = False          # Whether response came from cache
```

### 4. AiAgentIntegration

```python
class AiAgentIntegration(BaseIntegration):
    """Wraps an LLM provider for advisory analysis during build/deploy."""

    def analyse_plan(self, plan_json: dict, deployment_context: dict) -> AiResponse:
        """Analyse a Terraform/OpenTofu plan and return summary + risk assessment."""

    def diagnose_failure(self, error_output: str, step: str, context: dict) -> AiResponse:
        """Diagnose a deployer step failure and suggest remediation."""

    def analyse_sbom(self, sbom_json: dict, policies: list[dict]) -> AiResponse:
        """Analyse SBOM for supply-chain risks against configured policies."""

    def explain_drift(self, drift_report: dict, context: dict) -> AiResponse:
        """Explain infrastructure drift in plain language."""

    def summarise_deployment(self, manifest: dict, history: list[dict]) -> AiResponse:
        """Generate a human-readable deployment summary."""

    def review_policy_violations(self, violations: list[dict], context: dict) -> AiResponse:
        """Explain policy violations and suggest fixes."""

    def explain_doctor_results(self, failed_checks: list[dict], context: dict) -> AiResponse:
        """Explain env doctor check failures and suggest step-by-step remediation."""

    def assist_guide(self, phase: int, phase_label: str, blocking_items: list[dict], context: dict) -> AiResponse:
        """Explain what is blocking a readiness phase and suggest the next action."""
```

### 5. Lifecycle Hook Points

The agent is invoked at these lifecycle phases (all opt-in via configuration):

| Lifecycle Phase      | Hook                    | Agent Method                 | Gating?               |
| -------------------- | ----------------------- | ---------------------------- | --------------------- |
| `build_sbom_after`   | After SBOM generation   | `analyse_sbom()`             | No                    |
| `deploy_plan_after`  | After plan step         | `analyse_plan()`             | Yes — can block apply |
| `deploy_apply_error` | On provisioner failure  | `diagnose_failure()`         | No                    |
| `deploy_run_after`   | After successful deploy | `summarise_deployment()`     | No                    |
| `validate_after`     | After policy evaluation | `review_policy_violations()` | No                    |
| `drift_after`        | After drift detection   | `explain_drift()`            | No                    |
| `doctor_after`       | After env doctor run    | `explain_doctor_results()`   | No                    |
| `guide_after`        | After guide show        | `assist_guide()`             | No                    |

**Gating behaviour**: When `analyse_plan()` returns a risk score above a configurable threshold, the deploy controller pauses and requires explicit confirmation (interactive) or fails (CI mode with `--strict-ai-review`).

### 6. Prompt Engineering

Prompts are structured Python classes (not raw strings) with:

1. **System prompt** — Role definition, output format (structured JSON with narrative + risk_score + recommendations list).
2. **Context injection** — Resolved values, deployment name, environment, provider info, previous stage results.
3. **Content** — The artefact to analyse (plan JSON, error output, SBOM, etc.).
4. **Token budgeting** — Large artefacts (plans with hundreds of resources) are summarised or chunked before sending.

Example plan review prompt structure:

```python
class PlanReviewPrompt:
    SYSTEM = """You are an infrastructure change reviewer for a deployment platform.
Analyse the Terraform plan and provide:
1. A summary of changes (creates, updates, deletes) in 2-3 sentences.
2. A risk score (low / medium / high / critical).
3. Specific concerns (destructive changes, security group modifications, etc.).
4. Recommendations.
Respond in JSON format: {"summary": "...", "risk": "...", "concerns": [...], "recommendations": [...]}"""

    @staticmethod
    def build_user_prompt(plan_json: dict, context: dict) -> str:
        # Filter plan to relevant fields, inject deployment context
        ...
```

### 7. Audit Trail

Every AI invocation is logged to the audit system (ADR-0018):

```json
{
  "action": "ai_agent.analyse_plan",
  "target": "deploy-prd.yaml/stage:infrastructure",
  "outcome": "completed",
  "context": {
    "provider": "azure_openai",
    "model": "gpt-4o",
    "prompt_tokens": 3420,
    "completion_tokens": 512,
    "duration_ms": 2340,
    "risk_score": "medium",
    "cached": false
  }
}
```

### 8. Caching Strategy

To control costs, responses are cached using a content-hash key:

- **Key**: SHA-256 of `(prompt_template_version + artefact_content_hash + model_name)`.
- **Storage**: `.strata/cache/ai` directory with JSON files.
- **TTL**: Configurable, default 24 hours.
- **Invalidation**: Cache is invalidated when the artefact content changes (new plan, updated SBOM).

No caching for failure diagnosis (always unique context).

### 9. Cost Control

| Mechanism                        | Purpose                                                                                         |
| -------------------------------- | ----------------------------------------------------------------------------------------------- |
| `settings.max_tokens`            | Cap response size per invocation                                                                |
| `settings.enabled_hooks`         | List of lifecycle hooks where AI is active (default: none)                                      |
| `settings.budget_tokens_per_run` | Total token budget across all invocations in a single build/deploy                              |
| Response caching                 | Avoid re-analysing identical artefacts                                                          |
| Artefact summarisation           | Reduce large plans/SBOMs before sending                                                         |
| `--ai` CLI flag                  | Enable AI analysis for a single run (opt-in; no AI without this flag or `enabled_hooks` config) |

### 10. Security Considerations

- **No secrets in prompts** — Resolved secrets are never included in AI prompts. The prompt builder strips secret values and replaces them with `[REDACTED]`.
- **No mutation** — The agent never receives credentials or permissions to modify infrastructure. It is purely analytical.
- **API key handling** — Provider API keys follow the existing integration authentication patterns. Keys are never logged or included in audit trails. When `type: secret_store` is used, the key is resolved through the same `SecretStoreType` path used by `spec.secrets` in environment YAML (Azure Key Vault, Bitwarden, HashiCorp Vault, Infisical) — no plaintext secret touches the filesystem. When `type: azure_cli` is used, the provider acquires a short-lived bearer token via `az account get-access-token --resource https://cognitiveservices.azure.com/` — no long-lived secret is stored at all. The `secret_store` pattern is equally applicable to other integration types (Terraform Cloud, registry credentials, etc.) and should be adopted as the standard for all integration authentication in future ADRs.
- **Data residency** — For sensitive environments, operators can use local Ollama to ensure no data leaves the network.
- **Prompt injection** — Artefact content (plan JSON, error output) is treated as untrusted data. System prompts include guardrails against instruction override.

### 11. Custom Prompt Files

Operators can place Markdown prompt files in `.strata/prompts/` to override or extend the built-in system prompts. This is a **data-layer extension point** — no code changes are required.

```
.strata/
  prompts/
    plan_review.md        # Override the Terraform plan analysis system prompt
    failure_diagnosis.md  # Override the failure diagnosis system prompt
    sbom_analysis.md      # Override the SBOM analysis system prompt
    my_policy_checks.md   # Custom prompt loaded by name: --ai-prompt my_policy_checks
```

The `PromptLoader` utility (in `data/prompts/`) applies the following resolution order:

1. `.strata/prompts/<name>.md` — operator override (highest priority)
2. Built-in `data/prompts/<name>.py` template — fallback default

This lets teams inject project-specific context — naming conventions, approved providers, restricted regions, compliance requirements — without modifying the CLI or forking the codebase.

**Scope of override**: Operator files replace the **system prompt** only. The user prompt (artefact content + context injection) is always constructed by the CLI to ensure required fields and token budgeting are respected.

**Selection**: The active prompt file can be selected per-invocation with `--ai-prompt <name>`, allowing teams to maintain multiple prompt strategies (e.g., `strict_review.md` vs. `advisory.md`) and switch between them without configuration changes.

---

## Implementation Phases

### Phase 1 — Foundation (MVP) _(implemented)_

- `BaseAiProvider` ABC + `AiResponse` dataclass
- `OllamaProvider` (simplest — no auth, local, free)
- `AiAgentIntegration` with `analyse_plan()` only
- Integration into `deploy plan` command (non-gating, advisory output)
- Audit logging for AI invocations
- `--ai` flag to enable AI analysis per-run without persistent configuration
- Configuration model for `type: ai_agent`
- `PromptLoader` with `.strata/prompts/` override support (see [Section 11](#11-custom-prompt-files))

### Phase 2 — Provider Expansion _(implemented)_

- `OpenAiProvider` (OpenAI + Azure OpenAI)
- `AnthropicProvider`
- `AzureCliProvider` — acquires bearer token via `az account get-access-token`; reuses existing Azure CLI integration
- `secret_store` auth resolution — delegates to `SecretStoreType` path (Key Vault, Bitwarden, Vault, Infisical)
- Response caching (`.strata/cache/ai/`)
- Token budget tracking

### Phase 3 — Full Lifecycle Coverage _(implemented)_

- `diagnose_failure()` on deployer step errors
- `analyse_sbom()` after SBOM generation
- `review_policy_violations()` after validation
- `explain_drift()` after drift detection
- `summarise_deployment()` after successful deploy

### Phase 4 — Gating and Policy Integration _(implemented)_

- `ai_review` policy type in the policy engine
- Risk-score-based deploy gating
- `--strict-ai-review [THRESHOLD]` on `build plan` and `deploy run` — fails non-interactively when AI risk ≥ threshold; no policy declaration required
- Interactive confirmation flow — prompts operator before apply when risk ≥ high and `--force` is not set; auto-blocks in CI (non-TTY) mode

### Phase 5 — VS Code Chat Participant AI Commands _(implemented)_

Extend the existing `@strata` chat participant (`src/vscode/src/providers/strataChatParticipant.ts`) with AI-powered slash commands that surface `AiAgentIntegration` analysis directly in the VS Code chat UX.

**Implemented.** Three new slash commands registered in `package.json` and handled in `strataChatParticipant.ts`. The VS Code LM API (`vscode.lm`) is used natively — no Python provider configuration required in the IDE.
#### New commands

| Command          | Agent Method         | Description                                                                                               |
| ---------------- | -------------------- | --------------------------------------------------------------------------------------------------------- |
| `/review`        | `analyse_plan()`     | Analyse the active deployment's last Terraform plan; stream risk assessment and recommendations into chat |
| `/diagnose`      | `diagnose_failure()` | Parse the last failed deploy's error output; present root cause and remediation in chat                   |
| `/sbom` (extend) | `analyse_sbom()`     | Run AI analysis on the generated SBOM; render supply-chain risk summary                                   |

#### Design notes

- **No REPL needed** — VS Code chat provides built-in conversation history, follow-up questions, and markdown rendering. The cancelled console REPL is fully superseded.
- **Context injection** — The chat participant already receives workspace status via `_chatParticipant?.update(status)`. AI commands pass the resolved deployment context, stage history, and transient artefacts to `AiAgentIntegration`.
- **Custom prompts** — Operators can select a prompt strategy per-invocation (e.g., `@strata /review --prompt strict_review`). Resolution follows [Section 11](#11-custom-prompt-files): `.strata/prompts/<name>.md` → built-in template.
- **Response buttons** — AI responses include actionable buttons (e.g., "▶ Dry Run", "🛑 Abort Deploy") consistent with existing chat participant patterns.
- **VS Code LM API** — The chat participant calls `vscode.lm.selectChatModels()` natively in TypeScript when `provider: vscode_lm` is configured (or as a zero-config default). This uses whatever model the user already has access to (GitHub Copilot, etc.) with no API key or endpoint to manage. Prompt construction is handled by `aiPromptBuilder.ts`, which reads `.strata/prompts/` overrides from the workspace and gathers context via the existing `StrataClient` CLI calls.
- **Fallback** — If no AI provider is configured, commands return a message directing the operator to configure `type: ai_agent` in the configuration YAML. In VS Code the `vscode_lm` provider is tried first before any configured provider.
- **Freeform queries** — The existing `_handleFreeform()` handler is extended to route AI-related questions (plan analysis, failure diagnosis) to the active provider (VS Code LM or configured) when available.

### Phase 6 — Extended Command Coverage _(implemented)_

Wire the two existing analysis methods that are implemented but not yet exposed on the CLI, and add two new analysis methods for `env doctor` and `guide`.

#### 6a — `strata validate --ai`

After validation completes with policy violations, call `review_policy_violations()` and display the explanation + suggested YAML fixes inline.

```
$ strata validate -f deploy/main.yaml --ai
✅ Schema valid
❌ 2 policy violation(s)
  • tenant_zone: resource 'vm-prod' is not in an allowed zone
  • required_tags: missing 'env' tag on namespace 'default'

🤖 AI policy review (ai-advisor) ...

  📊 Summary: 2 violations found across zone and tagging policies.
  Fixes:
    1. Set zone: eu-fr on vm-prod (currently eu-de, not in allowed list)
    2. Add env: production under namespace.default.spec.tags
```

#### 6b — `strata deploy drift --ai`

After drift detection finds changes, call `explain_drift()` and display the explanation + reconciliation path.

```
$ strata deploy drift run -f deploy/main.yaml --ai
⚠️  Drift detected in 1 stage(s)

🤖 AI drift explanation (ai-advisor) ...

  💡 2 resources drifted in stage 'infrastructure'
  Likely cause: manual change to security group rule outside Terraform.
  Recommendations:
    → Run 'strata deploy run' to reconcile
    → Or acknowledge with 'strata deploy drift acknowledge'
```

#### 6c — `strata env doctor --ai`

After doctor finds failed checks, call `explain_doctor_results()` with the failed check details.

```
$ strata env doctor --ai
  ❌ terraform: not found in PATH
  ❌ azure_cli: not authenticated

🤖 AI doctor analysis (ai-advisor) ...

  🔧 Root cause: Terraform binary missing; Azure CLI present but not logged in.
  Remediation:
    1. Install terraform: https://developer.hashicorp.com/terraform/install
    2. Run: az login
```

#### 6d — `strata guide --ai`

After guide show identifies the blocking phase, call `assist_guide()` with the phase details.

```
$ strata guide --ai
  Phase 3/8: ⚠️  Not all repositories synced

🤖 AI guide assistance (ai-advisor) ...

  Phase 3 is blocked because repository 'haven' is not cloned.
  Run: strata repo sync --name haven
  If the repo doesn't exist yet, add it first:
    strata repo add --name haven --path ../haven
```

#### New prompt files

| File                               | Prompt class            | Purpose                                                |
| ---------------------------------- | ----------------------- | ------------------------------------------------------ |
| `data/prompts/doctor_analysis.py`  | `DoctorAnalysisPrompt`  | Root cause + remediation for env doctor failures       |
| `data/prompts/guide_assistance.py` | `GuideAssistancePrompt` | Explain readiness phase blockage + suggest next action |

#### New analysis methods on `AiAgentIntegration`

```python
def explain_doctor_results(self, failed_checks: list[dict], context: dict) -> AiResponse:
    """Explain env doctor check failures and suggest step-by-step remediation."""

def assist_guide(self, phase: int, phase_label: str, blocking_items: list[dict], context: dict) -> AiResponse:
    """Explain what is blocking a readiness phase and suggest the next action."""
```

---

### Phase 7 — `strata policy check --ai` _(implemented)_

Wire `review_policy_violations()` into the standalone `policy check` command. This is a quick win: the AI method is already implemented and battle-tested via `validate --ai`; the violations data structure is identical.

#### Design

`policy check` already evaluates all four phases (validate / build / plan / deploy) and collects results in `self._results: list[{policy, type, phase, enforcement, passed, violations[]}]`. After evaluation, if any entry has `passed=False`, pass the failed entries to `review_policy_violations()`.

**Key difference from `validate --ai`**: policy check can produce violations from multiple phases in one run. The prompt receives all failed entries grouped, giving the AI richer cross-phase context (e.g., a validate-phase naming violation alongside a plan-phase cost threshold breach).

**No new prompt file, no new AI method, no new `AiAgentIntegration` method.**

```
$ strata policy check -f deploy/deploy-prd.yaml --ai

  Policy Results
  ──────────────────────────────────────────────────
  ✗ DENY  [validate] tenant_zone           — resource 'vm-prod' not in allowed zone
  ✗ WARN  [plan]     cost_threshold        — estimated cost $420/month exceeds $300 limit
  ✓       [validate] required_tags         — passed

🤖  AI policy review (ai-advisor) …

  🟠  2 policy issues across 2 phases.
  
  Violations:
    [tenant_zone] Resource 'vm-prod' is deployed to 'eu-de' which is not in the
    allowed zone list [eu-fr, eu-nl]. Set zone: eu-fr in the resource declaration.
    
    [cost_threshold] Estimated monthly cost of $420 exceeds the configured threshold
    of $300. Consider downsizing the VM SKU or splitting into a lower-cost tier.
  
  Recommendations:
    → Update vm-prod zone in stack/vm-infra.yaml
    → Review cost allocation in configuration; the threshold may need updating if
      the higher cost is expected
```

#### Implementation plan

| Step | File                      | Change                                                                                                                          |
| ---- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| 1    | `check_policy_command.py` | Add `ai: bool = False` to `__init__`; after `_run_execution()`, call `_run_ai_policy_review()` if any result failed             |
| 2    | `check_policy_command.py` | Add `_run_ai_policy_review()` — builds violations list from `self._results`, calls `review_policy_violations()`, renders output |
| 3    | `check_policy_command.py` | Add `_print_ai_policy_review()` console renderer (risk icon + per-violation description + recommendations)                      |
| 4    | `cli_policy.py`           | Add `--ai` flag to `check_policy_command` Click command                                                                         |

**Reuses**: `review_policy_violations()`, `policy_review.py` prompt, `find_ai_integration()` helper — all unchanged.

**Output data**: adds `ai_analysis.policy_review` key to `_output_data` for JSON consumers.

---

## Command Coverage Reference

Complete survey of every strata CLI command and its AI applicability. Legend: ✅ implemented · 🔶 candidate (not yet done) · ➖ not applicable.

### Build & Deploy

| Command   | Subcommand  | Status | Flag                         | AI method                                      | Notes                                                                                                         |
| --------- | ----------- | ------ | ---------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `build`   | `plan`      | ✅      | `--ai`, `--strict-ai-review` | `analyse_plan()`                               | Risk assessment + gating                                                                                      |
| `build`   | `sbom`      | ✅      | `--ai`                       | `analyse_sbom()`                               | Supply-chain risk summary                                                                                     |
| `build`   | `run`       | ✅      | `--audit --ai`               | new: `analyse_cve_results()`                   | AI CVE triage after the built-in `--audit` scan; prioritises findings, flags no-fix CVEs, suggests upgrades   |
| `deploy`  | `run`       | ✅      | `--ai`, `--strict-ai-review` | `diagnose_failure()`, `summarise_deployment()` | Failure diagnosis + post-deploy summary; interactive plan gate                                                |
| `deploy`  | `drift run` | ✅      | `--ai`                       | `explain_drift()`                              | Drift explanation + reconciliation path                                                                       |
| `deploy`  | `health`    | ✅      | `--ai`                       | new: `explain_health_failures()`               | Explain why HTTP/TCP probes fail; suggest service fixes. Also fixes missing `@deploy.command` registration.   |
| `deploy`  | `history`   | ✅      | `--ai`                       | new: `summarise_deploy_history()`              | Trend analysis: success rate, recurring failures, degrading patterns; requires ≥2 entries                     |
| `deploy`  | `status`    | ➖      | —                            | —                                              | Low-value; covered by `deploy run --ai` summary                                                               |
| `service` | `deploy`    | ✅      | `--ai`                       | reuse `diagnose_failure()`                     | When a helm/compose deploy step fails, AI explains root cause + remediation; same method as `deploy run --ai` |
| `env`     | `doctor`    | ✅      | `--ai`                       | `explain_doctor_results()`                     | Per-check root cause + numbered remediation                                                                   |
| `cost`    | `history`   | ✅      | `--ai`                       | new: `analyse_cost_trend()`                    | Trend direction, spike detection with likely cause, cost-reduction recommendations                            |

### Inspection & Validation

| Command    | Subcommand | Status | Flag   | AI method                        | Notes                                                                                                                   |
| ---------- | ---------- | ------ | ------ | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `validate` | `run`      | ✅      | `--ai` | `review_policy_violations()`     | Schema + policy violation explanations with YAML fix suggestions                                                        |
| `validate` | `graph`    | ➖      | —      | —                                | VS Code freeform covers "what depends on what" questions                                                                |
| `guide`    | —          | ✅      | `--ai` | `assist_guide()`                 | Explain blocking phase; suggest next concrete action                                                                    |
| `policy`   | `check`    | ✅      | `--ai` | `review_policy_violations()`     | Same method as `validate --ai`; multi-phase violations give richer AI context                                           |
| `audit`    | `changes`  | ✅      | `--ai` | new: `summarise_audit_history()` | Trends, anomalies, recurring stage failures, recommendations; stats pre-computed client-side                            |
| `tools`    | `status`   | ➖      | —      | —                                | Already covered by `env doctor --ai`                                                                                    |
| `tools`    | `install`  | ✅      | `--ai` | new: `guide_tool_setup()`        | Combines runtime check state + static setup info into a tailored, OS-aware install guide; skips already-satisfied steps |
| `schema`   | all        | ➖      | —      | —                                | Schema Q&A is handled by the MCP server and VS Code freeform                                                            |
| `log`      | `list`     | ✅      | `--ai` | new: `summarise_execution_log()` | Groups related errors, identifies root causes, suggests next steps; only warning/error/critical entries sent to AI      |

### VS Code Chat Participant

| Command             | Status | AI method                            | Notes                                         |
| ------------------- | ------ | ------------------------------------ | --------------------------------------------- |
| `@strata /review`   | ✅      | `analyse_plan()` via `vscode.lm`     | Terraform plan risk assessment in chat        |
| `@strata /diagnose` | ✅      | `diagnose_failure()` via `vscode.lm` | Last failure root cause + remediation         |
| `@strata /sbom`     | ✅      | `analyse_sbom()` via `vscode.lm`     | Supply-chain risk in chat                     |
| `@strata` freeform  | ✅      | `vscode.lm` with workspace context   | Auto-routes AI keywords to dedicated handlers |

### Configuration & Setup

| Command group                                     | Status      | Notes                                                                                                    |
| ------------------------------------------------- | ----------- | -------------------------------------------------------------------------------------------------------- |
| `sln`, `profile`, `repo`, `ref`, `config`, `vars` | ➖           | Mechanical ops — no useful AI target                                                                     |
| `versions`, `promote`                             | ✅ (partial) | `promote status` now has `--ai`; `promote matrix` and `versions` remain mechanical — no useful AI target |
| `values list`                                     | ✅           | `strata values list --ai` explains each unresolved value and provides exact store-specific fix commands  |
| `new`                                             | ➖           | Template scaffolding — VS Code freeform and MCP server answer "which template to use" questions          |
| `secret`                                          | ➖           | Security-sensitive — AI must never handle credential context                                             |
| `mcp`                                             | ➖           | IS the external AI integration point                                                                     |
| `console`                                         | ➖           | REPL cancelled — VS Code chat supersedes                                                                 |
| `completion`, `version`, `help`                   | ➖           | No AI target                                                                                             |

### Extended Coverage — All Phases Implemented

All seven candidate items were implemented across Phases 7–11:

1. **`policy check --ai`** — implemented in Phase 7 (`review_policy_violations()`)
2. **`build run --audit --ai`** — implemented in Phase 7 (`analyse_cve_results()`)
3. **`deploy health --ai`** — implemented in Phase 7 (`explain_health_failures()`)
4. **`audit changes --ai`** — implemented in Phase 10 (`summarise_audit_history()`)
5. **`cost history --ai`** — implemented in Phase 11 (`analyse_cost_trend()`)
6. **`service deploy --ai`** — implemented in Phase 7 (reuses `diagnose_failure()`)
7. **`log list --ai`** — implemented in Phase 9 (`summarise_execution_log()`)

Additional Phase 7 items implemented beyond the original list:
- `deploy drift --ai`, `validate --ai`, `env doctor --ai`, `guide --ai`, `promote status --ai`, `values list --ai`, `deploy history --ai`, `tools install --ai`

---

### Phase 11 — `strata cost history --ai` _(implemented)_

Analyses the cost snapshot history using AI. Detects the trend direction (stable / rising / falling / volatile), identifies significant cost spikes with likely causes, and provides actionable cost-reduction or investigation recommendations.

```
$ strata cost history -f deploy/deploy-prd.yaml --last 10 --ai

─────────────────────────────────────────────────────────────────────
💰 Cost History — haven-prd (last 10 snapshots)
─────────────────────────────────────────────────────────────────────

Date (UTC)             Version       Monthly           Delta
────────────────────── ──────────── ────────────────── ────────────
2026-07-26 14:00       1.3.0          4 900.00 USD      +1200.00
2026-07-25 09:00       1.2.1          3 700.00 USD        +50.00
2026-07-24 16:00       1.2.0          3 650.00 USD       +847.50
2026-07-23 10:00       1.1.0          2 802.50 USD          —
...

  🤖  AI cost analysis (ai-advisor) …

  ──────────────────────────────────────────────────────────────────────
  🔴  Monthly cost rose 74.8% ($2 802.50 → $4 900.00 USD) over the 10-snapshot window.
      The largest single spike (+$1 200.00) occurred on 2026-07-26 following version 1.3.0.

  Cost window: 2802.50 → 4900.00 USD  (+2097.50, +74.8%)

  Cost spikes:
    [2026-07-26T14:00] v1.3.0 [terraform]  +1200.00 (+32.4%)
      → Three new VM instances (Standard_D4s_v3) added in the 'compute' provisioner.
    [2026-07-24T16:00] v1.2.0 [terraform]  +847.50 (+30.2%)
      → Azure Kubernetes node pool scaled from 2→5 nodes in version 1.2.0.

  Recommendations:
    1. Review VM SKU in version 1.3.0 — Standard_D2s_v3 may be sufficient for non-prod
    2. Enable autoscaling on the AKS node pool to avoid fixed 5-node cost during off-peak
    3. Run: strata cost diff -f deploy/deploy-prd.yaml to compare current vs. planned cost
  ──────────────────────────────────────────────────────────────────────
```

**Useful flag combinations**:

```bash
strata cost history -f deploy.yaml --last 10 --ai    # analyse last 10 snapshots
strata cost history -f deploy.yaml --last 30 --ai    # longer trend window
```

#### Stats pre-computation

Earliest/latest/min/max/avg costs and the largest single-step spike are computed in Python before the AI call. The AI focuses on interpreting *why* costs changed (version bumps, new provisioners, resource changes) rather than recalculating numbers. Snapshots are re-ordered chronologically (oldest → newest) in the prompt to make trend direction obvious.

#### Spike detection threshold

A spike is defined as a single-step delta ≥ 10% of the previous snapshot total. This threshold is baked into the system prompt — operators can override it via `.strata/prompts/cost_trend_analysis.md`.

#### Implementation

| Step | File                                  | Change                                                                                                                                                    |
| ---- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | `data/prompts/cost_trend_analysis.py` | New `CostTrendAnalysisPrompt` — `summary`, `trend`, `total_change`, `spikes[]`, `recommendations[]`                                                       |
| 2    | `ai_integration.py`                   | Add `analyse_cost_trend(snapshots, stats, context, work_path)` — cached                                                                                   |
| 3    | `history_cost_command.py`             | Add `ai: bool`; at end of `_execute()` call `_run_ai_cost_analysis()` with pre-computed `_compute_cost_stats()`; add `_print_ai_cost_analysis()` renderer |
| 4    | `cli_cost.py`                         | Add `--ai` flag to `cost_history` Click command                                                                                                           |

**Reuses**: `find_ai_integration()`, `self._configuration_service` (already loaded by `BaseDeployCommand`).

**Output data**: adds `ai_analysis.cost_trend` key to `_output_data` (includes `snapshots_analysed` count) for JSON consumers.

---

### Phase 10 — `strata audit changes --ai` _(implemented)_

Summarises a window of deployment executions using AI. Reports the health of the deployment pipeline: success rate, average duration, anomalies (duration spikes, sudden failures), recurring stage failures, and trends over the window.

```
$ strata audit changes --last 10 --ai

Timestamp                    Deployment           Success   Duration   Stages
──────────────────────────────────────────────────────────────────────────────────────────
2026-07-26T14:30:00+00:00    haven-prd            ✓         242.0s     3
2026-07-26T12:10:00+00:00    haven-prd            ✗         381.0s     3
2026-07-25T16:00:00+00:00    haven-prd            ✓         255.0s     3
...

10 entries shown.

  🤖  AI audit summary (ai-advisor) …

  ────────────────────────────────────────────────────────────
  🟡  8/10 deployments succeeded (80%) over the last 10 runs.
      Average duration 248s; two runs were notably slower (380s, 412s).

  Trend: Success rate declined in the last 3 runs: 3 of the most recent 4 runs failed
         compared to 1/6 failures in earlier runs.

  Anomalies:
    ⚠  Duration spike in run 2026-07-26T12:10 (+57% above average)
    ⚠  Run 2026-07-25T09:00 failed immediately with 0 stages completed

  Recurring failing stages: networking

  Recommendations:
    1. Investigate 'networking' stage — failed in 3/4 recent runs; likely a provider timeout
    2. Review the commit deployed at 2026-07-25T09:00 for breaking changes
  ────────────────────────────────────────────────────────────
```

**Useful flag combinations**:

```bash
strata audit changes --last 10 --ai                         # summarise last 10 runs
strata audit changes --since 2026-07-01T00:00:00Z --ai      # summarise since a date
strata audit changes --last 20 --stage networking --ai      # focus on one stage
```

#### Stats pre-computation

Success rate, average/min/max duration, and counts are computed in Python before the AI call. This keeps the prompt compact and avoids asking the LLM to do arithmetic. The AI focuses on pattern recognition and narrative — what the numbers mean, not what they are.

#### Caching

Responses are cached (SHA-256 of prompt content + model). A new run or a changed `--last`/`--since` window produces a different cache key. Combine `--stage` filtering with `--ai` for focused analysis without paying for a full-window token cost.

#### Implementation

| Step | File                                    | Change                                                                                                                                               |
| ---- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | `data/prompts/audit_history_summary.py` | New `AuditHistorySummaryPrompt` — `summary`, `health`, `anomalies[]`, `failing_stages[]`, `trends`, `recommendations[]`                              |
| 2    | `ai_integration.py`                     | Add `summarise_audit_history(entries, stats, context, work_path)` — cached                                                                           |
| 3    | `changes_audit_command.py`              | Add `ai: bool`; at end of `_execute()` call `_run_ai_audit_summary()` with pre-computed `_compute_stats()`; add `_print_ai_audit_summary()` renderer |
| 4    | `cli_audit.py`                          | Add `--ai` flag to `audit_changes` Click command                                                                                                     |

**Reuses**: `find_ai_integration()`, `SolutionController` workspace context pattern.

**Output data**: adds `ai_analysis.audit_history` key to `_output_data` (includes `entries_analysed` count) for JSON consumers.

---

### Phase 9 — `strata log list --ai` _(implemented)_

Summarises errors and warnings from execution log entries using AI. Groups related failures, identifies root causes, and produces prioritised next steps — replacing the need to manually scan log files after a failed command.

**Key difference from `diagnose_failure()`**: `diagnose_failure()` operates on a single provisioner step's error output in real time. `log list --ai` operates on the full log history — it can find patterns across multiple entries, correlate related errors, and distinguish transient noise (retries, timeouts) from persistent failures.

```
$ strata log list --last --ai

  📋  Execution Logs
  Filters: lines=50 | execution=a1b2c3d4…

  [2026-07-26T14:32:01]  ERROR    terraform apply failed: timeout waiting for resource
  [2026-07-26T14:32:03]  ERROR    stage 'networking' exceeded timeout (300s)
  [2026-07-26T14:32:03]  WARNING  retry 3/3 failed for resource azurerm_subnet.main
  ...

  🤖  AI log summary (ai-advisor) …

  ────────────────────────────────────────────────────────────
  🤖  AI Log Summary  (3 warning/error entries)
  ────────────────────────────────────────────────────────────

  🟠  Stage 'networking' timed out waiting for azurerm_subnet.main.
      All 3 errors are related to a single stuck resource.

  [3x] Azure subnet provisioning timeout
        Terraform applied but the subnet resource did not reach Ready state within 300s.
        Likely cause: Azure API throttling or a dependency (VNet) not yet available.
        → Run: strata deploy run --stage networking --force to retry the stage.

  Next steps:
    1. Check Azure portal for subnet 'main' provisioning state
    2. Verify VNet dependency is fully provisioned before retrying
    3. Consider increasing the Terraform timeout for azurerm_subnet in the module
  ────────────────────────────────────────────────────────────
```

**Useful flag combinations**:

```bash
strata log list --last --ai                     # summarise most recent execution
strata log list --minutes 60 --ai              # summarise last hour
strata log list --level error --ai             # focus AI on errors only (fewer tokens)
strata log list --execution-id <id> --ai       # summarise a specific run
```

#### Token efficiency

Only `WARNING`, `ERROR`, and `CRITICAL` entries are sent to the AI — `INFO` and `DEBUG` entries are rendered in the console log view but excluded from the AI prompt. This keeps token usage proportional to the signal density, not the log volume. For busy workspaces with verbose debug logging enabled, this can reduce the AI prompt by 90–99%.

#### Implementation

| Step | File                                    | Change                                                                                                                                                                                    |
| ---- | --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | `data/prompts/execution_log_summary.py` | New `ExecutionLogSummaryPrompt` — groups errors, `severity`, `error_groups[]`, `noise`, `next_steps[]`                                                                                    |
| 2    | `ai_integration.py`                     | Add `summarise_execution_log(log_entries, context, work_path)` — not cached (log content changes between runs)                                                                            |
| 3    | `show_log_command.py`                   | Add `ai: bool` to `__init__`; in `_execute()` after populating `_output_data`, call `_run_ai_log_summary()` which filters to warning/error entries then calls `summarise_execution_log()` |
| 4    | `cli_log.py`                            | Add `--ai` flag to `log list` Click command                                                                                                                                               |

**Reuses**: `find_ai_integration()`, `SolutionController` workspace context pattern.

**Output data**: adds `ai_analysis.log_summary` key to `_output_data` (includes `entries_analysed` count) for JSON consumers.

**Not cached**: each log window is unique; caching would return stale analysis for the same time window after new errors occur.

---

### Phase 8 — `strata tools install <name> --ai` _(implemented)_

Combines the static setup guide from `tools install` with live runtime state from `tools check` to produce a **tailored, state-aware installation guide** — skipping steps already satisfied and providing OS-appropriate commands.

**Key difference from `env doctor --ai`**: `env doctor` is _reactive_ (something broke, explain why). `tools install --ai` is _proactive_ — the operator is setting up a new integration and wants a personalised walkthrough.

```
$ strata tools install terraform --ai

  Integration : terraform
  CLI command : terraform
  Download    : https://developer.hashicorp.com/terraform/install

  ...

  🤖  AI setup guide (ai-advisor) …

  ────────────────────────────────────────────────────────────
  terraform binary is not installed; TF_TOKEN is not configured.

  Steps:

  1. Install (Windows)
       winget install HashiCorp.Terraform
     → Open a new terminal after install for PATH to take effect.

  2. Configure Terraform Cloud authentication
       $env:TF_TOKEN_app_terraform_io = "<your-api-token>"
     → Create a token at https://app.terraform.io/app/settings/tokens

  3. Verify
       strata tools check terraform

  References:
    https://developer.hashicorp.com/terraform/install
  ────────────────────────────────────────────────────────────
```

When the binary is already installed, the AI skips step 1 and focuses only on missing auth configuration:

```
  Already configured:
    ✓ binary installed (v1.9.2)

  Steps:

  1. Configure Terraform Cloud authentication
       ...
```

#### What the AI receives

The prompt combines output from two controller calls:
- `controller.check(name)` — runtime state: `available`, `version`, plus all fields from `install_info`
- `controller.install_info(name)` — static: `install_url`, `env_vars[]`, `auth_methods[]`, `yaml_example`

Each env var in the prompt includes a live `is_set` field (checked via `os.environ` at call time) so the AI knows exactly which configuration gaps remain.

Workspace context (provisioner types, backend types) is injected when an active profile is found — allowing the AI to recommend the correct auth method for the workspace's IaC backend (e.g. Terraform Cloud vs. local state).

#### Implementation

| Step | File                         | Change                                                                                                                                        |
| ---- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | `data/prompts/tool_setup.py` | New `ToolSetupPrompt` — `status_summary`, `already_done[]`, `steps[]`, `verify_command`, `references[]`                                       |
| 2    | `ai_integration.py`          | Add `guide_tool_setup(tool_detail, os_name, workspace_context, work_path)`                                                                    |
| 3    | `install_tools_command.py`   | Add `ai: bool` to `__init__`; after `_print_guide()`, call `_run_ai_setup_guide()` which calls `controller.check()` then `guide_tool_setup()` |
| 4    | `cli_tools.py`               | Add `--ai` flag to `tools_install` Click command                                                                                              |

**Reuses**: `find_ai_integration()`, `SolutionController` workspace context pattern from `env doctor --ai`.

**Output data**: adds `ai_analysis.tool_setup` key to `_output_data` for JSON consumers.

**Caching**: response is cached (SHA-256 of prompt + tool state) — same tool with same env-var state hits the cache. Cache is invalidated when a new env var is set or the binary is installed.

---

## References

- [ADR-0003: Layered architecture](0003-layered-architecture.md)
- [ADR-0006: Policy engine for deployment guardrails](0006-policy-engine-for-deployment-guardrails.md)
- [ADR-0018: Deployment audit and traceability](0018-deployment-audit-traceability.md)
- [ADR-0020: Lifecycle phases and environment variables](0020-lifecycle-phases-and-environment-variables.md)
- [ADR-0023: Pluggable provisioner framework](0023-pluggable-provisioner-framework.md)
- [MCP Server documentation](../mcp/README.md)
