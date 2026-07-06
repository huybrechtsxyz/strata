# Keyboard Shortcuts

Common keyboard shortcuts for the strata extension.

## Built-in Shortcuts

These are VS Code defaults:

| Action                  | Windows/Linux                     | macOS         |
| ----------------------- | --------------------------------- | ------------- |
| Open Command Palette    | `Ctrl+Shift+P`                    | `Cmd+Shift+P` |
| Open Problems panel     | `Ctrl+Shift+M`                    | `Cmd+Shift+M` |
| Open Settings           | `Ctrl+,`                          | `Cmd+,`       |
| Focus Strata Explorer   | `Ctrl+Shift+S` (custom, if bound) | `Cmd+Shift+S` |
| Go to Definition        | `F12`                             | `F12`         |
| Hover (Peek Definition) | `Ctrl+K Ctrl+I`                   | `Cmd+K Cmd+I` |
| Quick Fix               | `Ctrl+.`                          | `Cmd+.`       |
| Rename (F2)             | `F2`                              | `F2`          |

## Extension Commands (via Command Palette)

Press `Ctrl+Shift+P` and type to search:

```
strata: validate current    → Validate active file
strata: validate all        → Workspace validation
strata: build               → Build (no dry-run)
strata: build dry-run       → Preview build
strata: deploy              → Deploy (no dry-run)
strata: deploy dry-run      → Preview deploy
strata: show guide          → Readiness checklist
strata: switch profile      → Quick-pick profile
strata: export schemas      → Wire YAML schemas
strata: open console        → Interactive REPL
strata: dependency graph    → Visualize relationships
strata: init workspace      → New strata workspace
```

Type a few letters and VS Code filters the list — no need to type the full command name.

## Custom Keyboard Bindings

To add custom shortcuts, edit `.vscode/keybindings.json`:

1. `Ctrl+K Ctrl+S` (or `Cmd+K Cmd+S` on macOS) to open Keyboard Shortcuts
2. Click the "Open Keyboard Shortcuts (JSON)" icon (top-right)
3. Add bindings like:

```json
[
  {
    "key": "ctrl+shift+v",
    "command": "strata.validateCurrentFile"
  },
  {
    "key": "ctrl+shift+b",
    "command": "strata.buildDryRun"
  },
  {
    "key": "ctrl+shift+d",
    "command": "strata.deployDryRun"
  },
  {
    "key": "ctrl+shift+g",
    "command": "strata.showGuide"
  }
]
```

## Common Workflows

### Quick Validate & View Errors

```
Ctrl+S                    Save the file (triggers auto-validation)
Ctrl+Shift+M              Open Problems panel
```

### Explore Schema While Editing

```
(cursor on YAML line)
Ctrl+.                    Show CodeLens quick actions
Click "Schema"            Opens schema in side panel
```

### Build & Deploy Workflow

```
(cursor in deployment file)
Ctrl+.                    Show CodeLens
Click "Build DryRun"      Preview what would be generated
(review terminal output)
Click "Deploy DryRun"     Preview what would be deployed
(review terminal output)
Ctrl+Shift+P              Command Palette
Type "deploy"             Find "Strata: Deploy"
Enter                     Run deployment
```

### Switch Profile & Refresh

```
Ctrl+Shift+P              Command Palette
Type "switch profile"     Find command
Enter                     Quick-pick profile to activate
```

## Tips

- **Fast validation:** Bind `strata.validateCurrentFile` to a single key (e.g., `Ctrl+Alt+V`)
- **Quick build:** Bind `strata.buildDryRun` (e.g., `Ctrl+Alt+B`)
- **Quick deploy:** Bind `strata.deployDryRun` (e.g., `Ctrl+Alt+D`)
- **Fast fixes:** Learn `Ctrl+.` (quick fixes) — works with or without extension
- **Search in Command Palette:** Type a few letters of any command name

## See Also

- [VS Code Keyboard Shortcuts Guide](https://code.visualstudio.com/docs/getstarted/keybindings)
- [Command Palette Reference](https://code.visualstudio.com/docs/getstarted/userinterface#_command-palette)
