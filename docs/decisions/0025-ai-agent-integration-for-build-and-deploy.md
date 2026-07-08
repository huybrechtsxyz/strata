# AI agent integration for build and deploy workflows

- Status: proposed
- Date: 2026-07-07

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
        └── prompts/
              ├── plan_review.py    # Terraform plan analysis prompt
              ├── failure_diagnosis.py
              ├── sbom_analysis.py
              ├── drift_explanation.py
              └── deployment_summary.py
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
        provider: azure_openai          # openai | azure_openai | anthropic | ollama
        endpoint: https://my-aoai.openai.azure.com/
        model: gpt-4o
      authentication:
        type: env_var                   # env_var | managed_identity | token
        value: AZURE_OPENAI_API_KEY
      settings:
        temperature: 0.1               # Low temperature for deterministic analysis
        max_tokens: 4096
        timeout: 60                     # seconds
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
- **Storage**: `.strata/ai-cache/` directory with JSON files.
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
- **API key handling** — Provider API keys follow the existing integration authentication patterns (env vars, managed identity). Keys are never logged or included in audit trails.
- **Data residency** — For sensitive environments, operators can use local Ollama to ensure no data leaves the network.
- **Prompt injection** — Artefact content (plan JSON, error output) is treated as untrusted data. System prompts include guardrails against instruction override.

---

## Implementation Phases

### Phase 1 — Foundation (MVP)

- `BaseAiProvider` ABC + `AiResponse` dataclass
- `OllamaProvider` (simplest — no auth, local, free)
- `AiAgentIntegration` with `analyse_plan()` only
- Integration into `deploy plan` command (non-gating, advisory output)
- Audit logging for AI invocations
- `--ai` flag to enable AI analysis per-run without persistent configuration
- Configuration model for `type: ai_agent`

### Phase 2 — Provider Expansion

- `OpenAiProvider` (OpenAI + Azure OpenAI)
- `AnthropicProvider`
- Response caching
- Token budget tracking

### Phase 3 — Full Lifecycle Coverage

- `diagnose_failure()` on deployer step errors
- `analyse_sbom()` after SBOM generation
- `review_policy_violations()` after validation
- `explain_drift()` after drift detection
- `summarise_deployment()` after successful deploy

### Phase 4 — Gating and Policy Integration

- `ai_review` policy type in the policy engine
- Risk-score-based deploy gating
- `--strict-ai-review` flag for CI/CD
- Interactive confirmation flow for high-risk plans

### Phase 5 — Interactive Prompt Mode

Extend the **existing `strata console` REPL** (`commands/console/run_console_command.py`) with AI chat capability rather than introducing a parallel interactive mechanism. The console already provides `prompt_toolkit` input, `rich` rendering, command dispatch, and session history — the AI integration slots in as an additional command handler.

#### REPL extension

Add an `ai` (alias `ask`) command to `ConsoleCommand._repl_loop()`:

```
strata> ai why did the last deploy fail?
strata> ask what resources would be deleted if I apply this plan?
```

The handler calls `AiAgentIntegration.chat()`, which maintains a conversation history list for the lifetime of the console session. Each call appends both the user turn and the assistant response to the history, so follow-up questions have full context. The last analysis result produced by the session (plan review, failure diagnosis, SBOM analysis, etc.) is automatically injected as the opening context message — operators can ask follow-up questions about it without repeating the artefact.

#### Custom prompt files

Operators can place Markdown prompt files in `.strata/prompts/` to override or extend the built-in system prompts:

```
.strata/
  prompts/
    plan_review.md        # Override the Terraform plan analysis system prompt
    failure_diagnosis.md  # Override the failure diagnosis system prompt
    my_policy_checks.md   # Custom prompt loaded by name: ai --prompt my_policy_checks
```

The `PromptLoader` utility (in `integrations/ai/prompts/`) checks `.strata/prompts/<name>.md` first; if absent it falls back to the built-in `prompts/<name>.py` template. This lets teams inject project-specific context (naming conventions, approved providers, restricted regions) without modifying the CLI.

#### Behaviour rules

- `AiAgentIntegration.chat()` — stateful multi-turn method; conversation history is an in-memory list, cleared when the console session ends
- Session transcript appended to the audit trail under the originating analysis invocation
- `settings.interactive_timeout` — seconds to wait for TTY input before continuing automatically (useful in semi-attended CI)
- No interactive mode when `--force` is present or when stdin is not a TTY — CI pipelines fall back to non-interactive
- `--interactive` flag on `build plan` and `deploy run` drops the operator into the console REPL after analysis completes, pre-loaded with that run's context

---

## References

- [ADR-0003: Layered architecture](0003-layered-architecture.md)
- [ADR-0006: Policy engine for deployment guardrails](0006-policy-engine-for-deployment-guardrails.md)
- [ADR-0018: Deployment audit and traceability](0018-deployment-audit-traceability.md)
- [ADR-0020: Lifecycle phases and environment variables](0020-lifecycle-phases-and-environment-variables.md)
- [ADR-0023: Pluggable provisioner framework](0023-pluggable-provisioner-framework.md)
- [MCP Server documentation](../mcp/README.md)
