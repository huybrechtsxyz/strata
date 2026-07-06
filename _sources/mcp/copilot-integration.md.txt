# GitHub Copilot Integration

Ideas and patterns for enhancing GitHub Copilot with strata MCP integration for inline YAML validation and AI-powered assistance.

## Current State: CLI-Based Extension

The current **VS Code extension** for strata uses the CLI directly (not MCP). This means:

✅ **Advantages:**
- Works offline (no separate process)
- Direct integration with VS Code diagnostics
- Real-time validation with CodeLens
- No additional setup needed

❌ **Limitations:**
- Limited to editor-specific commands
- Harder to share context between Copilot Chat and diagnostics
- No programmatic access for custom workflows

---

## Vision: MCP-Enhanced Copilot

### Idea 1: Inline Copilot Chat with Strata Tools

**Current workflow:**
```
You edit deployment.yaml → Extension validates (CodeLens) → You run CLI commands
```

**Enhanced workflow with MCP:**
```
You edit deployment.yaml 
→ Extension shows diagnostics 
→ You ask Copilot Chat: "@strata Show me what would be built"
→ Copilot calls validate_file(), build_plan() via MCP
→ Copilot shows preview inline
→ You review and run strata build run via CLI
```

**Implementation:**

In the VS Code extension:

```typescript
// src/vscode/extension.ts

import { LanguageClient } from 'vscode-languageclient/node';

export async function activate(context: vscode.ExtensionContext) {
  // 1. Start the strata MCP server in the background
  const mcpServer = new MCPServerManager(workspaceRoot);
  await mcpServer.start();
  
  // 2. Make MCP tools available to Copilot Chat
  const toolProvider = new StrataToolProvider(mcpServer);
  
  // 3. Register Copilot Chat participant
  vscode.chat.createChatParticipant('strata', {
    handler: async (request, context, stream, token) => {
      stream.markdown(`\`@strata\` is connected. Available commands:\n\n`);
      stream.markdown(`- "Validate this file"\n`);
      stream.markdown(`- "Show build preview"\n`);
      stream.markdown(`- "Deploy preview"\n`);
      stream.markdown(`- "Check workspace status"\n`);
      stream.markdown(`- "Compare with staging"\n`);
      
      // Delegate to MCP tools
      const response = await toolProvider.handleRequest(request);
      stream.markdown(response);
    },
  });
}
```

**Usage in Copilot Chat:**

```
You: "@strata What would be deployed if I apply this YAML?"

Copilot:
  Let me check the deployment preview.
  [calls deploy_plan() via MCP]
  
  🔨 Build Preview:
  ✚ azurerm_resource_group.prod (new)
  ✚ azurerm_kubernetes_cluster.prod (new)
  + 8 more resources
  
  Estimated cost: $2,340/month
  Deployment time: ~15 minutes
```

---

### Idea 2: CodeLens with Copilot Quick Actions

**Current:** CodeLens shows action buttons (Validate, Build, Deploy, Schema)

**Enhanced:** Add "Explain with AI" button

```typescript
// src/vscode/providers/codelens.ts

class StrataCodeLensProvider implements vscode.CodeLensProvider {
  provideCodeLenses(document, token) {
    const lenses = [
      new vscode.CodeLens(range, {
        title: "✓ Validate",
        command: "strata.validate",
      }),
      new vscode.CodeLens(range, {
        title: "📋 Build Plan",
        command: "strata.buildPlan",
      }),
      new vscode.CodeLens(range, {
        title: "🤖 Explain with AI",  // ← NEW
        command: "strata.explainWithCopilot",
      }),
    ];
    return lenses;
  }
}

// When user clicks "Explain with AI":
export async function explainWithCopilot(document: vscode.TextDocument) {
  const validationResult = await validateFile(document.uri.fsPath);
  const schema = await getSchema(document.languageId);
  
  // Open Copilot Chat with context
  await vscode.commands.executeCommand('github.copilot.openChat', {
    contextMessage: `I'm working on a strata deployment file. Can you explain what this does and suggest improvements?
    
    Validation result: ${JSON.stringify(validationResult)}
    Schema: ${JSON.stringify(schema)}`,
  });
}
```

---

### Idea 3: Inline Error Explanations

**Current:** Extension shows validation errors with error codes

**Enhanced:** Use MCP to get error explanations and fixes

```typescript
// src/vscode/diagnostics.ts

export async function createDiagnostics(file: string): vscode.Diagnostic[] {
  const result = await validateFile(file);
  
  const diagnostics = result.errors.map(error => {
    const diag = new vscode.Diagnostic(
      error.range,
      error.message,
      vscode.DiagnosticSeverity.Error
    );
    
    // Add CodeAction to explain error with AI
    diag.code = {
      value: error.code,
      target: vscode.Uri.parse(`strata-help:${error.code}`),
    };
    
    return diag;
  });
  
  return diagnostics;
}

// CodeActionProvider to handle "Explain" action
class StrataCodeActionProvider implements vscode.CodeActionProvider {
  provideCodeActions(document, range, context) {
    return context.diagnostics.map(diag => {
      const action = new vscode.CodeAction(
        '🤖 Explain with AI',
        vscode.CodeActionKind.QuickFix
      );
      action.command = {
        title: 'Explain',
        command: 'strata.explainErrorWithCopilot',
        arguments: [diag.message],
      };
      return action;
    });
  }
}
```

**Result:** User hovers over error, sees "Explain with AI" button, clicks it, Copilot opens with error context.

---

### Idea 4: Diff View with Copilot Commentary

**Current:** Diff shows what changed in a file

**Enhanced:** Use `build_plan()` to show what would change in infrastructure

```typescript
// src/vscode/diffView.ts

export async function showBuildDiff(deploymentFile: string) {
  // Get current build artifacts
  const currentBuild = await getBuildArtifacts(deploymentFile);
  
  // Run dry-run build
  const plannedBuild = await buildPlan(deploymentFile);
  
  // Create a text diff
  const diff = createDiff(currentBuild, plannedBuild);
  
  // Show in diff editor
  const diffUri = vscode.Uri.parse(`strata-diff:${deploymentFile}`);
  await vscode.commands.executeCommand('vscode.open', diffUri);
  
  // Show Copilot analysis
  await vscode.commands.executeCommand('github.copilot.openChat', {
    contextMessage: `Here's what the build would change:\n\n${diff}\n\nExplain these changes and tell me if there are any concerns.`,
  });
}
```

---

### Idea 5: Multi-File Deployment Planning

**Current:** Validate individual files

**Enhanced:** Copilot can orchestrate multi-step deployments

```
You: "@strata I have three files to deploy: network.yaml, cluster.yaml, services.yaml. 
Show me the full plan and highlight any risks."

Copilot:
  1. [calls validate_file() on all three] ✅ All valid
  2. [calls build_plan() for each] → Generates artifacts
  3. [calls deploy_plan() sequentially] → Shows deployment order
  4. [analyzes dependencies] → Identifies risks
  
  📊 Multi-Stage Deployment Plan:
  
  Stage 1: network.yaml
    ✚ Virtual network, subnets, NSGs
    Time: ~2 minutes
    ⚠️ Risk: Cannot rollback if cluster stage fails
  
  Stage 2: cluster.yaml (depends on Stage 1)
    ✚ AKS cluster with 5 nodes
    Time: ~15 minutes
    ⚠️ Risk: High cost impact
  
  Stage 3: services.yaml (depends on Stage 2)
    ✚ Helm charts for services
    Time: ~5 minutes
    ✅ Safe to rollback independently
  
  Total time: ~22 minutes
  Total cost: $2,340/month
  
  Risks:
  1. No rollback automation for Stage 1 if Stage 2 fails
     Suggestion: Add auto-rollback policy to network.yaml
  
  2. Insufficient monitoring in services.yaml
     Suggestion: Add health_checks to deployment
```

---

### Idea 6: Collaborative Deployment Comments

**Current:** Only the person with CLI access can deploy

**Enhanced:** Leave comments in Copilot Chat for the team

```
You (Developer): "@strata Is this deployment safe?"

Copilot: ✅ Validation passed. Build preview shows... [details]

You: "Looks good, but I can't deploy. @alex Can you review this before deploying?"

Alex (SRE, in Copilot Chat): "@strata Show me the health check status after deployment"

Copilot: [queries deploy_status() and deploy_health()] ✅ All checks passing
```

**Implementation:** Use Copilot Chat thread context to maintain deployment context across multiple users.

---

## Implementation Roadmap

### Phase 1: MVP (Current State)
- ✅ CLI-based extension with CodeLens
- ✅ Real-time YAML validation
- ✅ File decoration badges

### Phase 2: MCP Integration (Proposed)
- Embed MCP server startup in extension activation
- Make MCP tools available to Copilot Chat via `@strata` participant
- Add "Explain with AI" CodeLens action

### Phase 3: Enhanced AI Features
- CodeAction providers for quick fixes
- Diff viewer with deployment impact analysis
- Multi-file orchestration planning

### Phase 4: Collaborative Workflows
- Team approval gates in Copilot Chat
- Shared deployment planning threads
- Comment-based task assignment

---

## Security Considerations

### MCP Scope in VS Code Extension

The MCP server runs **locally in the extension process**, so:

✅ **Secure:**
- No network exposure
- Secrets never transmitted
- File access limited to workspace
- User can see all calls to MCP tools

❌ **Limitations:**
- Only works for the user running VS Code
- Requires MCP dependency installation
- Cannot be used for remote/team deployments (use Claude for that)

### Best Practice

```
1. Editor (local, single user):
   - Use VS Code extension with MCP
   - Good for individual development

2. Shared/Team (multi-user):
   - Use Claude or Copilot Chat via MCP server
   - Better for approval workflows and compliance
```

---

## Example Implementation: Validate Command

Here's what the "Explain with AI" command might look like:

```typescript
// src/vscode/commands/explainWithCopilot.ts

import * as vscode from 'vscode';
import { MCPClient } from '../mcp/client';

export async function explainWithCopilot(
  documentUri: vscode.Uri
): Promise<void> {
  const document = await vscode.workspace.openTextDocument(documentUri);
  const filePath = document.uri.fsPath;
  
  // 1. Validate the file via MCP
  const mcpClient = MCPClient.getInstance();
  const validationResult = await mcpClient.validateFile(filePath);
  
  // 2. Get schema information
  const kind = validationResult.kind;
  const schema = await mcpClient.getSchema(kind);
  
  // 3. Format for Copilot
  const context = `
I'm editing a strata ${kind} file: ${filePath}

Validation Result:
${validationResult.valid ? '✅ Valid' : '❌ Invalid'}

${validationResult.errors?.length ? 
  'Errors:\n' + validationResult.errors.map(e => `- ${e}`).join('\n')
  : 'No validation errors'}

File Content:
\`\`\`yaml
${document.getText()}
\`\`\`

Please review this file and:
1. Explain what it does
2. Point out any issues or best-practice violations
3. Suggest improvements
4. Ask if you need clarification on any field
`;
  
  // 4. Open Copilot with context
  await vscode.commands.executeCommand('github.copilot.openChat', {
    message: context,
  });
}
```

---

## Testing the Integration

### Local Testing

```bash
# 1. Start strata MCP server
cd /path/to/workspace
strata mcp serve

# 2. Run VS Code in the workspace
code .

# 3. Open Copilot Chat and type:
# "@strata Validate the deployment file"
```

### End-to-End Test

```
1. Edit a strata YAML file in VS Code
2. Open Copilot Chat (@strata)
3. Ask: "Is this deployment valid?"
   → Extension calls validate_file() via MCP
   → Copilot shows result
4. Ask: "Show me what would be built"
   → Extension calls build_plan() via MCP
   → Copilot shows preview
5. Ask: "Generate a deployment plan"
   → Extension calls deploy_plan() via MCP
   → Copilot shows orchestration
```

---

## Next Steps

1. **Review [Security & Workflows](security-and-workflows.md)** — Understand approval gates
2. **Explore [AI-Assisted Troubleshooting](ai-troubleshooting.md)** — More AI use cases
3. **Prototype the integration** — Start with CodeLens "Explain with AI" action
4. **Gather feedback** — Test with team, iterate

---

## See Also

- [VS Code Extension](../vscode/README.md) — Current extension docs
- [Claude Deployment Assistant](claude-deployment-assistant.md) — Team-focused MCP workflow
- [Security & Workflows](security-and-workflows.md) — Approval patterns
