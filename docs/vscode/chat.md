# Chat Integration

The `@strata` chat participant integrates strata into Copilot Chat. Ask natural language questions about your workspace and get AI-powered answers with full context.

## How to Use

In Copilot Chat (or Claude Chat), type `@strata` followed by your question:

```
@strata what's the current workspace status?
@strata how do I validate config/main.yaml?
@strata which files have errors?
```

The participant injects workspace context into the LLM's response, so answers are tailored to your actual state — not generic.

## Slash Commands

In addition to freeform questions, use slash commands for specific workflows:

### `/status`

Shows workspace health, active profile, readiness, and next steps.

**Response includes:**

```
| Property     | Value     |
| ------------ | --------- |
| Health       | 🟢 HEALTHY |
| Profile      | dev       |
| Readiness    | Phase 5/8 |
| Repositories | 2         |
```

Plus any issues and the next suggested action.

**Use case:** "Give me a quick overview of my workspace"

### `/validate`

Validates the current file or a file you name.

```
@strata /validate config/main.yaml
```

**Response shows:**

- ✅ Validation passed (0 errors)
- ❌ List of errors with field paths and messages

**Use case:** "Check if my deployment file is valid before pushing"

### `/guide`

Shows the 8-phase readiness checklist.

```
✅ Phase 1: Workspace initialized
✅ Phase 2: Repositories registered
⏳ Phase 5: Build artifacts ready — strata build run -f deploy/main.yaml
```

**Use case:** "What do I need to do next to be ready to deploy?"

### `/build`

Lists all deployment files and shows how to build each one.

**Response shows:**

```
Available deployment files:

- deploy/dev.yaml
  strata build run -f deploy/dev.yaml --dry-run
  strata build run -f deploy/dev.yaml

- deploy/prd.yaml
  strata build run -f deploy/prd.yaml --dry-run
  strata build run -f deploy/prd.yaml
```

**Use case:** "How do I build my deployments?"

### `/deploy`

Lists all deployment files and shows how to deploy each one.

**Response shows:**

```
Available deployment files:

- deploy/dev.yaml
  strata deploy run -f deploy/dev.yaml --dry-run
  strata deploy run -f deploy/dev.yaml --force

- deploy/prd.yaml
  strata deploy run -f deploy/prd.yaml --dry-run
  strata deploy run -f deploy/prd.yaml --force
```

**Use case:** "Walk me through deploying to production"

## Freeform Questions

Ask any question and the participant provides workspace context:

```
@strata what configuration files do I have?
@strata why might terraform not be available?
@strata how do I connect the api module to my deployment?
```

The LLM sees:

```
The user is working in a strata infrastructure workspace.
Health: HEALTHY
Profile: dev
Readiness: Phase 5/8 complete
Next step: Build artifacts ready
Repositories: infra, modules
User question: [your question]
```

The LLM can then answer your question with full awareness of your workspace state.

## Follow-Up Suggestions

After each response, the participant suggests follow-up questions:

```
After /status:
  → Show readiness guide
  → Validate current file

After /validate:
  → Show workspace status
  → How do I build?

After /guide:
  → Build my deployment
  → Deploy my deployment
```

Click a suggestion to send it. This creates a natural conversation flow without you needing to retype commands.

## Workspace Context Injection

When you ask a freeform question, the participant automatically includes:

| Context      | Example                           |
| ------------ | --------------------------------- |
| Health       | HEALTHY, DEGRADED, or BROKEN      |
| Profile      | currently active profile name     |
| Readiness    | phases complete (e.g., 5/8)       |
| Next step    | what to do next with command hint |
| Repositories | list of registered repositories   |
| Issues       | any health issues reported        |

This means the LLM can give answers like:

> "Your workspace is healthy and in Phase 5. To be ready for production, you need to: 1) Generate build artifacts with `strata build run -f deploy/prd.yaml`, 2) Run a dry-run deployment to preview changes..."

Instead of generic answers that don't match your actual state.

## Error Handling

**"Strata CLI is not available"**

→ The extension couldn't find the CLI. Check:

1. `strata.cliPath` setting points to a valid executable
2. Run `which strata` in a terminal to verify it's in `$PATH`
3. Reload the extension: `Ctrl+Shift+P` → "Developer: Reload Window"

**Model call failed**

→ If the LLM can't generate a response, the participant falls back to showing you the raw workspace context and command hints.

**Timeout**

→ The LLM took too long. You can:

1. Ask a simpler question (shorter context = faster response)
2. Use a slash command instead (faster, no LLM call)
3. Check if the LLM service is operational

## Privacy

The participant **only sends**:

- Workspace health status
- Profile names (not contents)
- Repository names and branches
- Document kinds and validation status
- Your question

It does **not** send:

- File contents
- Secret values
- Credentials
- Sensitive configuration data

All communication with the LLM goes through your Copilot Chat or Claude Chat client — the strata extension just injects context.

## Limitations

- The participant can only validate files in your open workspace
- It can't modify files or apply fixes directly (use CodeLens quick fixes instead)
- Freeform questions depend on the LLM's knowledge (for truly custom questions, slash commands are more reliable)
- Follow-up suggestions only work within the same chat session

## Examples

### "How do I set up a new deployment?"

```
@strata how do I set up a new deployment?
```

Response includes:

```
Based on your workspace (Phase 5/8), here's how to set up a new deployment:

1. Scaffold a deployment file:
   strata new deployment --name my-deployment

2. Reference your configuration:
   kind: deployment
   spec:
     stages:
       - provisioner: platform_iac
         provisioner_config:
           source: @infra/my-infrastructure
   
3. Validate it:
   strata validate deploy/my-deployment.yaml
   
4. Build to preview:
   strata build run -f deploy/my-deployment.yaml --dry-run
```

### "What's blocking me from deploying?"

```
@strata /status
```

Response:

```
| Property  | Value                                  |
| --------- | -------------------------------------- |
| Health    | 🟡 DEGRADED                             |
| Profile   | prd                                    |
| Readiness | Phase 3/8                              |
| Issues    | 1 validation error in deploy/main.yaml |

Next step: Fix deployment validation errors
Hint: strata validate deploy/main.yaml
```

### "Check this file for me"

```
@strata /validate deploy/production.yaml
```

Response:

```
Validating deploy/production.yaml…

❌ 1 error found:
- Unknown field 'replicas' — did you mean 'instances'? (spec.replicas)
```

Follow-up: "Fix this error" → opens CodeLens quick fix.
