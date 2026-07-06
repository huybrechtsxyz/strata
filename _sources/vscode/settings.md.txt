# Settings

Configure the strata extension behavior via VS Code settings. Open settings with `Ctrl+,` (or `Cmd+,` on Mac) and search for "strata".

## All Settings

| Setting                      | Type    | Default    | Description                                                               |
| ---------------------------- | ------- | ---------- | ------------------------------------------------------------------------- |
| `strata.cliPath`             | string  | `"strata"` | CLI executable or command (e.g., `"uv run strata"`)                       |
| `strata.validateOnSave`      | boolean | `true`     | Run validation on file save                                               |
| `strata.validateOnType`      | boolean | `false`    | Run validation with 1.5s debounce while typing                            |
| `strata.showCodeLens`        | boolean | `true`     | Show inline CodeLens actions (Validate, Build, Deploy, Schema, Guide)     |
| `strata.showStatusBar`       | boolean | `true`     | Show status bar item (health, profile, phase)                             |
| `strata.autoExportSchemas`   | boolean | `false`    | Auto-export and wire JSON schemas on workspace open                       |
| `strata.showFileDecorations` | boolean | `true`     | Show validation badges (✅/⚠️/❌) in file tree                               |
| `strata.defaultProfile`      | string  | `""`       | Profile to activate automatically on workspace open (empty = last active) |

## Details

### `strata.cliPath`

**Type:** `string`  
**Default:** `"strata"`  
**Description:** Path to the strata CLI executable.

**Examples:**

```json
"strata.cliPath": "strata"           // CLI in $PATH
"strata.cliPath": "/usr/local/bin/strata"  // Full path
"strata.cliPath": "uv run strata"    // Via uv
"strata.cliPath": "poetry run strata"// Via poetry
"strata.cliPath": "C:\\tools\\strata.exe"  // Windows
```

**How it's used:**

The extension runs:

```bash
<cliPath> [arguments]
```

So `"strata.cliPath": "uv run strata"` becomes:

```bash
uv run strata validate <file>
```

**Finding your CLI:**

In a terminal, run:

```bash
which strata          # macOS/Linux
where strata          # Windows
```

If not in `$PATH`, install it first:

```bash
pip install strata
# or
pip install --user strata
```

Then check where it was installed:

```bash
pip show strata       # shows Location field
```

### `strata.validateOnSave`

**Type:** `boolean`  
**Default:** `true`  
**Description:** Automatically validate files on save.

- When `true` — every time you save a `.yaml` file, validation runs
- When `false` — validation only runs on-demand (Command Palette, CodeLens, right-click)

**Use case:**

- **Enable** (`true`) for tight feedback loops (errors caught immediately)
- **Disable** (`false`) if validation is slow or you prefer manual control

### `strata.validateOnType`

**Type:** `boolean`  
**Default:** `false`  
**Description:** Validate while typing with a 1.5-second debounce.

- When `true` — 1.5 seconds after you stop typing, validation runs
- When `false` — only on save or on-demand

**Debounce:** 1.5 seconds of inactivity before validation starts. This prevents excessive CLI calls while you're typing.

**Use case:**

- **Enable** (`true`) if you have a fast machine and want live feedback
- **Disable** (`false`) on slow hardware or large workspaces to avoid slowdowns

**Warning:** Enabling both `validateOnSave` and `validateOnType` means validation can run frequently. Be aware of CPU/disk impact.

### `strata.showCodeLens`

**Type:** `boolean`  
**Default:** `true`  
**Description:** Show inline CodeLens actions above YAML documents.

When `true`, you see:

```yaml
# [Validate] [Schema] [Build DryRun] [Deploy DryRun] [Guide]
apiVersion: strata.huybrechts.xyz/v1
```

When `false`, these lenses are hidden.

**Note:** Even when hidden, the actions still work via Command Palette or right-click menu.

### `strata.showStatusBar`

**Type:** `boolean`  
**Default:** `true`  
**Description:** Show status bar item in the bottom-left corner.

When `true`:

```
$(cloud) strata: dev | Phase 5/8
```

When `false`, the status bar item is hidden (but workspace health is still tracked internally).

### `strata.autoExportSchemas`

**Type:** `boolean`  
**Default:** `false`  
**Description:** Automatically export and wire JSON schemas on workspace open.

When `true`:

1. On extension activation, runs `strata schema export`
2. Generates JSON schemas to `.strata/schemas/`
3. Runs `strata schema wire` to update `.vscode/settings.json` with `yaml.schemas` mapping
4. The YAML extension (if installed) immediately provides autocomplete and validation

**Use case:**

- Enable if you want one-time automatic schema setup
- After enabling, restart VS Code or reload window

**Manual alternative:**

If you keep this `false`, you can manually export + wire via:

```
Command Palette → "Strata: Export Schemas"
```

### `strata.showFileDecorations`

**Type:** `boolean`  
**Default:** `true`  
**Description:** Show validation status badges in the file tree.

When `true`, files are decorated:

```
✓  config/main.yaml         ← validated, no issues
⚠️  config/test.yaml        ← warnings
❌ deploy/prd.yaml         ← errors
```

When `false`, badges are hidden (but validation still runs).

**Performance:** Decorations are lightweight and don't impact performance.

### `strata.defaultProfile`

**Type:** `string`  
**Default:** `""` (empty)  
**Description:** Profile to activate automatically on workspace open.

**Examples:**

```json
"strata.defaultProfile": ""          // Don't auto-activate (use last active)
"strata.defaultProfile": "dev"       // Auto-activate 'dev' profile
"strata.defaultProfile": "production"// Auto-activate 'production' profile
```

**How it works:**

When the extension activates:

1. If `defaultProfile` is non-empty, the extension calls `strata profile activate <name>`
2. If successful, `_refreshAll()` runs to update all UI
3. If the profile doesn't exist, you'll see an error notification

**Use case:**

- Set to your development profile so each workspace opens with the right profile already active
- Leave empty to use whatever profile was active last time

## Workspace vs User Settings

Settings can be configured at two levels:

### User Settings (`.config/Code/settings.json`)

Apply to all workspaces. Use for global preferences.

### Workspace Settings (`.vscode/settings.json`)

Apply only to the current workspace. Use for project-specific overrides.

**Example: per-workspace CLI path**

Workspace `.vscode/settings.json`:

```json
{
  "strata.cliPath": "uv run strata"
}
```

This overrides the global setting just for this workspace.

## Command-Line Configuration

Settings can also be set via VS Code CLI:

```bash
code --user-data-dir /tmp/vscode-data \
  --install-extension strata \
  --settings strata.cliPath="uv run strata"
```

## Programmatic Access

In the extension code, settings are read with:

```typescript
const setting = vscode.workspace
  .getConfiguration('strata')
  .get<string>('cliPath', 'strata');
```

Changes to settings are picked up immediately (no reload needed) because the extension listens to `onDidChangeConfiguration`.

## Troubleshooting

**Settings are not taking effect**

1. Check spelling (e.g., `strata.validateOnSave` not `strata.validateonsave`)
2. Reload the window: `Ctrl+Shift+P` → "Developer: Reload Window"
3. Check if workspace settings are overriding user settings

**"Unknown configuration setting"**

→ You're using an old version of the extension. Update from the Marketplace.

**CLI path is wrong but settings show correct value**

→ Might be a shell issue. Try:

```bash
# Test if the CLI works
$(which strata) --version
# or
uv run strata --version
```

If it fails, fix your shell environment first (add to `$PATH`, install dependencies, etc.).
