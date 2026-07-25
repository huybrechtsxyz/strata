# AI Agent Integration

Advisory LLM analysis at build/deploy lifecycle points.
Calls a configured language model to explain Terraform plans, diagnose failures, analyse SBOMs, explain drift, and summarise deployments — all read-only and opt-in.

See ADR-0025 for the full design.

---

## Supported Providers

| Provider           | `provider` value | Auth                       |
| ------------------ | ---------------- | -------------------------- |
| Local Ollama       | `ollama`         | none                       |
| OpenAI             | `openai`         | `api_key` (env var)        |
| Azure OpenAI       | `azure_openai`   | `api_key` or `azure_cli`   |
| Azure OpenAI (CLI) | `azure_cli`      | `az login` — no stored key |
| Anthropic Claude   | `anthropic`      | `api_key` (env var)        |

---

## Configuration

Add an `ai_agent` integration to your `kind: configuration` YAML:

```yaml
spec:
  integrations:
    - name: ai-advisor
      type: ai_agent
      endpoints:
        address: https://my-aoai.openai.azure.com/
      authentication:
        method: api_key
        api_key:
          api_key: AZURE_OPENAI_API_KEY    # name of the env var
      properties:
        provider: azure_openai             # ollama | openai | azure_openai | anthropic | azure_cli
        model: gpt-4o
        temperature: 0.1
        max_tokens: 4096
        timeout: 60
        enabled_hooks: []                  # lifecycle hooks where AI runs automatically
```

### Ollama (zero-config local)

```yaml
    - name: ai-local
      type: ai_agent
      properties:
        provider: ollama
        endpoint: http://localhost:11434
        model: llama3
```

### Azure OpenAI via `az login` (no stored key)

```yaml
    - name: ai-azure-cli
      type: ai_agent
      endpoints:
        address: https://my-aoai.openai.azure.com/
      authentication:
        method: cli
      properties:
        provider: azure_cli
        model: gpt-4o
```

---

## CLI Usage

Run AI plan analysis on demand:

```bash
strata build plan -f deploy/deploy-prd.yaml --ai
```

The AI analysis appears after the terraform plan output. Risk levels: `low` · `medium` · `high` · `critical`.

---

## Policy Gating

Block or warn on high-risk plans using `type: ai_review`:

```yaml
spec:
  policies:
    - name: ai_plan_gate
      type: ai_review
      phase: plan
      enforcement: deny
      configuration:
        integration: ai-advisor
        risk_threshold: high       # deny if risk is high or critical
```

---

## Custom Prompts

Override any built-in system prompt by placing a Markdown file in `.strata/prompts/`:

```
.strata/
  prompts/
    plan_review.md        # overrides the Terraform plan analysis prompt
    failure_diagnosis.md
    sbom_analysis.md
    drift_explanation.md
    deployment_summary.md
    policy_review.md
```

The user prompt (artefact content) is always constructed by the CLI; only the system prompt is replaced.

---

## Enabled Hooks

Set `properties.enabled_hooks` to auto-invoke AI analysis at lifecycle points without `--ai`:

```yaml
properties:
  enabled_hooks:
    - deploy_plan_after     # after terraform plan — advisory
    - build_sbom_after      # after SBOM generation
    - deploy_apply_error    # on provisioner failure
    - validate_after        # after policy evaluation
    - drift_after           # after drift detection
    - deploy_run_after      # after successful deploy
```

---

## Audit Trail

Every AI invocation is written to `.strata/audit.log`:

```json
{"action": "ai_agent.analyse_plan", "outcome": "success", "target": "ai-advisor",
 "detail": {"provider": "azure_openai", "model": "gpt-4o",
             "prompt_tokens": 3420, "completion_tokens": 512,
             "duration_ms": 2340, "cached": false}}
```

---

## Response Cache

Responses are cached under `.strata/cache/ai/` by content hash (default TTL: 24 h).
Set a lower TTL or disable caching by configuring `properties.cache_ttl_seconds: 0`.
Failure-diagnosis responses are never cached.

---

## Docs

- ADR-0025: [AI agent integration for build and deploy workflows](../../docs/decisions/0025-ai-agent-integration-for-build-and-deploy.md)
