# CodeLens

CodeLens shows inline actions above strata YAML documents. Click them to run workflows without leaving the editor.

## Available Lenses

Five lenses appear above every strata YAML document:

```yaml
# [Validate] [Schema] [Build DryRun] [Deploy DryRun] [Guide]
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: main
spec:
  ...
```

### Validate

Runs `strata validate <file>` and shows results in the Problems panel.

- **For all kinds:** always available
- **Result:** red/yellow/green indicator in Problems panel
- **Use:** quick validation without opening Command Palette

### Schema

Opens the JSON schema for this document's `kind` in a side panel.

- **Example:** For a `kind: deployment`, opens `deployment.schema.json`
- **Shows:** all available fields, descriptions, examples, constraints
- **Use:** discover what fields are valid without reading docs

### Build DryRun

Runs `strata build run -f <file> --dry-run` in a terminal.

- **Available for:** `kind: deployment`
- **Shows:** what artifacts would be generated (Terraform, Helm, etc.)
- **Use:** preview changes before committing

### Deploy DryRun

Runs `strata deploy run -f <file> --dry-run` in a terminal.

- **Available for:** `kind: deployment`
- **Shows:** what resources would be created/updated/deleted
- **Terminal:** stays open for inspection, close manually
- **Safety:** dry-run means no actual changes are applied
- **Use:** safe preview of deployment impact before deploying

### Guide

Opens the 8-phase readiness checklist in a side panel.

- **For all kinds:** always available
- **Shows:** workspace readiness, phases complete, next step
- **Use:** context-aware guidance while editing

## Enabling/Disabling

Toggle CodeLens on/off with:

```json
{
  "strata.showCodeLens": true    // default
}
```

Change this in Settings or `settings.json`. The change takes effect immediately (no reload needed).

## Viewing CodeLens

CodeLens appears by default. If you don't see it:

1. Check `strata.showCodeLens` setting (must be `true`)
2. Check VS Code's global CodeLens setting: `editor.codeLens` (must be `true`)
3. Make sure the file is recognized as a strata YAML (has `apiVersion: strata.*`)

To temporarily hide CodeLens:

- Press `Ctrl+Shift+P` → "CodeLens: Toggle CodeLens"
- Or set `"editor.codeLens": false` in settings

## Terminal Integration

Build and Deploy lenses launch commands in a terminal:

- New terminal named "strata build" or "strata deploy"
- Terminal stays open for you to inspect output
- Close manually when done
- Closing the terminal triggers `_refreshAll()` to update UI

This lets you see full command output and logs while keeping your editor context.

## Keyboard Shortcut

While your cursor is on a line with CodeLens, press:

- `Ctrl+Shift+P` → search for the action
- Or directly run commands via Command Palette (without needing the lens)

## Styling

CodeLens text is gray and small, appearing above your code. It's styled per your color theme. Common theme colors:

- `editorCodeLens.foreground` — lens text color
- `editorCodeLens.fadingForeground` — dimmed lens (less important ones)

Customize in your theme or `settings.json`:

```json
{
  "workbench.colorCustomizations": {
    "editorCodeLens.foreground": "#888"
  }
}
```

## Troubleshooting

**CodeLens not appearing**

1. Check `strata.showCodeLens: true`
2. Check `editor.codeLens: true`
3. Make sure `apiVersion: strata.*` is in the first 20 lines
4. Reload window: `Ctrl+Shift+P` → "Developer: Reload Window"

**Schema lens shows nothing**

→ The schema file might not exist. Run:

```bash
strata schema export
```

to generate all schemas, then wire them:

```bash
strata schema wire
```

**Build/Deploy lens shows nothing**

→ The lens only appears for `kind: deployment`. Make sure:

```yaml
kind: deployment
```

is in the file.

**Terminal hangs after running lens**

→ The CLI might be waiting for input. Check:

1. The terminal is visible (you can see its output)
2. Try pressing Enter or Ctrl+C to recover
3. Check `strata audit list --last` for the command that ran
