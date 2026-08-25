# Development Guide — Strata VS Code Extension

This document covers how to set up a development environment, build the extension, run tests, and contribute changes.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Building](#building)
- [Running & Debugging](#running--debugging)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Common Tasks](#common-tasks)
- [Debugging Tips](#debugging-tips)
- [Publishing](#publishing)

---

## Prerequisites

- **Node.js** 18 or later (`node --version`)
- **npm** 9 or later (`npm --version`)
- **VS Code** 1.90.0 or later
- **Strata CLI** installed for testing (`strata --version`)
  - Install via: `pipx install xyz-strata` or `uv sync` from the workspace root

---

## Setup

### 1. Install Dependencies

```bash
cd src/vscode
npm install
```

### 2. Verify Installation

```bash
npm list typescript vscode @types/vscode
```

You should see recent versions of these packages listed.

### 3. Configure Strata CLI Path (Optional)

If your `strata` CLI is not in PATH, create a `.env` file:

```bash
# src/vscode/.env
STRATA_CLI_PATH="uv run strata"
# or
STRATA_CLI_PATH="/path/to/strata"
```

The extension will read this during debugging.

---

## Building

### Compile TypeScript to JavaScript

```bash
npm run compile
```

Compiles `src/**/*.ts` to `out/**/*.js` using tsconfig.json.

### Watch Mode (Development)

```bash
npm run watch
```

Recompiles automatically when files change. Useful while developing.

### Type Check (No Emit)

```bash
npm run type-check
```

Runs tsc in `--noEmit` mode to verify types without generating output.

---

## Running & Debugging

### Launch Debug Session

Press **F5** in VS Code to start the debugger:

1. A new VS Code window opens (**Extension Development Host**)
2. Your extension is loaded
3. Debug console shows logs from your extension

### Debugging Shortcuts

| Action    | Shortcut      |
| --------- | ------------- |
| Step over | F10           |
| Step into | F11           |
| Step out  | Shift+F11     |
| Continue  | F5            |
| Pause     | Pause/Break   |
| Restart   | Ctrl+Shift+F5 |

### Debug Console

Print values using `console.log()`:

```typescript
console.log('My value:', myVariable);
```

Logs appear in the **Debug Console** tab in the bottom panel.

### Breakpoints

Click on the line number in the editor to set a breakpoint. When execution reaches that line, the debugger pauses.

---

## Testing

### Run All Tests

```bash
npm run test
```

### Run Tests in Watch Mode

```bash
npm run test -- --watch
```

### Run Specific Test

```bash
npm run test -- --grep "my test name"
```

### Test Coverage

```bash
npm run test:coverage
```

Generates coverage report in `coverage/` directory.

---

## Project Structure

```
src/vscode/
├── src/
│   ├── extension.ts                  # Entry point (activate/deactivate)
│   ├── strataClient.ts              # CLI wrapper (subprocess calls)
│   └── providers/
│       ├── statusBarProvider.ts      # Status bar UI
│       ├── workspaceViewProvider.ts  # Workspace tree view
│       ├── filesViewProvider.ts      # Files tree view
│       ├── repositoriesViewProvider.ts # Repos tree view
│       ├── toolsViewProvider.ts      # Tools tree view
│       ├── envViewProvider.ts        # Environments tree view
│       ├── auditViewProvider.ts      # Audit trail tree view
│       ├── diagnosticsProvider.ts    # Inline error diagnostics
│       ├── codeLensProvider.ts       # Inline code actions
│       ├── guideViewProvider.ts      # Readiness guide
│       ├── crossReferenceProvider.ts # Reference resolution
│       ├── snippetProvider.ts        # YAML snippets
│       ├── diagramPreviewProvider.ts # CLI-backed diagram rendering + click-to-open (ADR-0034)
│       ├── fileDecorationProvider.ts # File badges
│       ├── strataChatParticipant.ts  # Chat integration
│       ├── strataTaskProvider.ts     # Task definitions
│       └── colorProvider.ts          # Syntax highlighting (future)
├── tests/
│   ├── unit/
│   │   ├── extension.test.ts
│   │   ├── strataClient.test.ts
│   │   └── providers/
│   │       └── diagnosticsProvider.test.ts
│   └── integration/
│       ├── commands.test.ts
│       └── views.test.ts
├── resources/
│   ├── icon.png                      # Extension marketplace icon
│   ├── strata.svg                    # Activity bar icon
│   └── walkthrough/
│       ├── init.md
│       ├── schemas.md
│       └── guide.md
├── package.json                      # Extension manifest + dependencies
├── package-lock.json                 # Lock file
├── tsconfig.json                     # TypeScript config
├── .vscodeignore                     # Files to exclude from VSIX package
├── README.md                         # User-facing documentation
├── CHANGELOG.md                      # Version history
├── DEVELOPMENT.md                    # This file
└── .vscode/
    ├── launch.json                   # Debug configuration
    ├── settings.json                 # Workspace settings
    └── tasks.json                    # Build tasks
```

---

## Common Tasks

### Adding a New Command

1. **Define the command** in `package.json` under `contributes.commands`:

```json
{
  "command": "strata.myNewCommand",
  "title": "Strata: My New Command",
  "icon": "$(icon-name)"
}
```

2. **Implement the handler** in `src/extension.ts`:

```typescript
const myNewCommandDisposable = vscode.commands.registerCommand(
  'strata.myNewCommand',
  async () => {
    vscode.window.showInformationMessage('My new command executed!');
  }
);

context.subscriptions.push(myNewCommandDisposable);
```

3. **Register in subscriptions**:

```typescript
context.subscriptions.push(myNewCommandDisposable);
```

### Adding a New Tree View Provider

1. **Create a new provider class**:

```typescript
// src/providers/myViewProvider.ts
import * as vscode from 'vscode';
import { StrataClient } from '../strataClient';

export class MyViewProvider implements vscode.TreeDataProvider<MyItem> {
  private _onDidChangeTreeData = new vscode.EventEmitter<MyItem | undefined>();
  onDidChangeTreeData = this._onDidChangeTreeData.event;

  constructor(private client: StrataClient) {}

  getTreeItem(element: MyItem): vscode.TreeItem {
    return element;
  }

  async getChildren(element?: MyItem): Promise<MyItem[]> {
    // Fetch data from CLI
    const data = await this.client.runCommand(['my', 'command']);
    return data.map(item => new MyItem(item.name));
  }

  refresh(): void {
    this._onDidChangeTreeData.fire(undefined);
  }
}

class MyItem extends vscode.TreeItem {
  constructor(label: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
  }
}
```

2. **Register in `package.json`**:

```json
{
  "views": {
    "strata-explorer": [
      {
        "id": "strataMyView",
        "name": "My View",
        "contextualTitle": "Strata My View"
      }
    ]
  }
}
```

3. **Create and register in `extension.ts`**:

```typescript
let _myView: MyViewProvider | undefined;

export async function activate(context: vscode.ExtensionContext) {
  _myView = new MyViewProvider(_client);
  const myViewDisposable = vscode.window.registerTreeDataProvider('strataMyView', _myView);
  context.subscriptions.push(myViewDisposable);
}
```

### Adding a Configuration Setting

1. **Define in `package.json`**:

```json
{
  "configuration": {
    "properties": {
      "strata.mySetting": {
        "type": "boolean",
        "default": false,
        "description": "My new setting"
      }
    }
  }
}
```

2. **Read in code**:

```typescript
const config = vscode.workspace.getConfiguration('strata');
const myValue = config.get<boolean>('mySetting', false);
```

3. **Listen for changes**:

```typescript
vscode.workspace.onDidChangeConfiguration(event => {
  if (event.affectsConfiguration('strata.mySetting')) {
    // Handle change
  }
});
```

### Writing a Test

```typescript
// tests/unit/myFeature.test.ts
import * as assert from 'assert';
import { MyClass } from '../../src/myClass';

suite('MyClass', () => {
  test('should work correctly', () => {
    const result = MyClass.doSomething();
    assert.strictEqual(result, 'expected value');
  });

  test('should handle errors', async () => {
    try {
      await MyClass.asyncOperation();
      assert.fail('Should have thrown');
    } catch (e) {
      assert.ok(e instanceof Error);
    }
  });
});
```

---

## Debugging Tips

### Enable Verbose Logging

Add this to `src/extension.ts`:

```typescript
const IS_DEBUG = process.env.DEBUG === 'true';

function log(message: string, data?: any) {
  if (IS_DEBUG) {
    console.log(`[Strata] ${message}`, data || '');
  }
}
```

Set environment variable before debugging:

```bash
export DEBUG=true  # Linux/macOS
set DEBUG=true     # Windows
```

### Inspect CLI Output

Add logging to `strataClient.ts`:

```typescript
private async runCommand(args: string[]): Promise<any> {
  console.log('[CLI] Running:', args.join(' '));
  const result = await exec(`${this.cliPath} ${args.join(' ')}`);
  console.log('[CLI] Output:', result.stdout);
  console.log('[CLI] Errors:', result.stderr);
  return JSON.parse(result.stdout);
}
```

### Use VS Code's Built-in Debug View

- **Variables** — inspect all variables in current scope
- **Watch** — add expressions to monitor
- **Breakpoints** — manage all breakpoints
- **Call Stack** — navigate through execution stack

### Check Extension Output

1. Open **Output** panel (`Ctrl+Shift+U`)
2. Select **"Strata"** from dropdown (top right)
3. View all extension logs

---

## Publishing

### Package Extension (VSIX)

```bash
npm run package
```

Creates `xyz-strata-0.16.1.vsix` (version from package.json).

### Verify Package

```bash
unzip -l xyz-strata-0.16.1.vsix | head -20
```

Should show `package.json`, extension.js files, resources/, etc. (no src/ or tests/).

### Publish to Marketplace

Requires:
- [Personal Access Token (PAT)](https://marketplace.visualstudio.com/manage/publishers)
- `vsce` CLI: `npm install -g @vscode/vsce`

```bash
vsce publish --pat <your-pat>
```

Or with prerelease:

```bash
vsce publish --pat <your-pat> --pre-release
```

---

## Continuous Integration

### GitHub Actions (`.github/workflows/vscode-extension.yml`)

Automatically:
- Lints code (`npm run lint`)
- Type-checks (`npm run type-check`)
- Runs tests (`npm run test`)
- Packages extension (`npm run package`)
- Uploads VSIX as artifact

Triggered on:
- Push to `main` branch
- Pull requests to `main`
- Manual workflow dispatch

---

## Code Style & Standards

### TypeScript

- **Target**: ES2020
- **Lib**: ES2020
- **Strict Mode**: Enabled (`strict: true`)
- **No Implicit Any**: Enforced
- **Source maps**: Enabled in debug mode

### Naming Conventions

- Classes: `PascalCase` (e.g., `MyProvider`)
- Functions: `camelCase` (e.g., `runCommand()`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `MAX_RETRIES`)
- Private members: `_camelCase` (e.g., `_client`)

### Formatting

```bash
npm run format          # Auto-format code
npm run format:check   # Check without modifying
```

---

## Troubleshooting Development Issues

### "Command not found: npm"

Install Node.js: https://nodejs.org/

### "Extension doesn't activate"

1. Check activation event in `package.json`: `activationEvents`
2. Ensure `.strata/solution.json` exists in test workspace
3. Open **Output** panel and check for errors
4. Reload debug window: `Ctrl+R`

### "TypeScript errors after installing new packages"

```bash
npm install && npm run compile
```

### "Tests fail intermittently"

1. Increase test timeout (if network-dependent):

```typescript
test('my test', async function() {
  this.timeout(10000);  // 10 seconds
  // test code
});
```

2. Check for race conditions (async/await issues)
3. Mock external calls (CLI, filesystem)

### "Debug breakpoint not hit"

1. Ensure source maps are enabled: `"sourceMap": true` in tsconfig.json
2. Verify code was recompiled: run `npm run compile`
3. Restart debug session: `Ctrl+Shift+F5`

---

## Resources

- [VS Code Extension API](https://code.visualstudio.com/api)
- [VS Code Extension Examples](https://github.com/microsoft/vscode-extension-samples)
- [TypeScript Handbook](https://www.typescriptlang.org/docs/)
- [Node.js API Reference](https://nodejs.org/api/)

---

## Questions or Need Help?

- **Issues**: [GitHub Issues](https://github.com/huybrechtsxyz/strata/issues)
- **Discussions**: [GitHub Discussions](https://github.com/huybrechtsxyz/strata/discussions)
- **VS Code Extension Docs**: https://code.visualstudio.com/api
