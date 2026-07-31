# Real-Time Validation & Diagnostics

The extension provides real-time validation feedback as you edit, powered by `strata validate`. Errors appear as squiggly underlines in the editor and in VS Code's Problems panel.

## When Validation Runs

Validation is triggered by:

1. **On save** (default: enabled)
   - Every time you save a YAML file, the extension runs `strata validate`
   - Results appear in the Problems panel immediately

2. **While typing** (default: disabled)
   - If `strata.validateOnType: true`, the extension validates after 1.5 seconds of inactivity
   - Useful for tight feedback loops, but can be chatty if you have slow hardware

3. **On demand**
   - Command Palette → "Strata: Validate Current File" (validates active editor)
   - Command Palette → "Strata: Validate All Files" (scans workspace with progress bar)
   - Right-click file in Files tree → "Validate"
   - CodeLens → click "Validate" above a YAML document

## Problems Panel

Open the Problems panel with `Ctrl+Shift+M` (or `Cmd+Shift+M` on Mac). All strata validation errors appear here.

```
PROBLEMS
─────────────────────────────────────────────────
Strata    ❌ 3 errors, ⚠️ 1 warning

config/main.yaml
  3:5   Error      Unknown field 'specc' (did you mean 'spec'?)
 12:0   Error      Missing required field 'provisioner'

deploy/production.yaml
  8:2   Warning    Deprecated field 'replicas' — use 'instances' instead
 42:1   Error      @infra/modules/api.yaml not found in repository 'infra'
```

**Error anatomy:**

```
config/main.yaml
  3:5   Error      Unknown field 'specc'
  ↑     ↑          ↑
  file  line:col   severity + message
```

Click an error to jump to that line in the editor.

## Error Details

Hover over a red squiggle to see full error details:

```
Unknown field 'specc' (did you mean 'spec'?)

[Phase 1] Structural validation (Pydantic)
Field: spec
Got value: {...}
```

The tooltip shows:

- **Error message** — what's wrong
- **Phase** — validation phase (Phase 1 = structural, Phase 2 = cross-reference/dynamic)
- **Field** — exact field path that failed (e.g., `spec.provisioner`)
- **Got value** — the actual value that didn't validate (if known)

## Quick Fixes

When available, VS Code shows a **Quick Fix** bulb (💡) next to errors.

**Common quick fixes:**

1. **Typo suggestion**
   - Error: "Unknown field 'replicas' (did you mean 'instances'?)"
   - Fix: "Rename to 'instances'" → one-click fix

2. **Remove unknown field**
   - Error: "Unknown field 'foo' — extra fields are not allowed"
   - Fix: "Remove unknown field 'foo'" → deletes the line

3. **Add missing field**
   - Error: "Missing required field 'provisioner'"
   - Fix: "Add 'provisioner:' line" → inserts the field

Access quick fixes via:

- Click the 💡 icon that appears when hovering over a squiggle
- `Ctrl+.` (or `Cmd+.` on Mac) when your cursor is on the error line

## Validation Phases

Strata validation happens in two phases:

### Phase 1: Structural (Pydantic)

Checks the YAML against the schema:

- Unknown fields (`extra="forbid"`)
- Type mismatches (e.g., string instead of list)
- Missing required fields
- Enum value violations

These run first and are always fast.

### Phase 2: Dynamic (Cross-Reference & Profile)

Checks references and profile-specific rules:

- `@repo_name/path/file.yaml` references resolve correctly
- Referenced files are valid
- Secret references exist
- Environment variables are available in the active profile

These can be slower because they involve file I/O and profile state.

## Validation Coverage

Only **strata YAML documents** are validated. The extension identifies them by checking for `apiVersion: strata.*` in the first 20 lines.

**Valid strata prefixe:** `strata.huybrechts.xyz/v1`

If a YAML file doesn't have one of these prefixes, it's not validated by strata (though the YAML extension may still check it).

## File Decorations

In the Explorer file tree, validated files show badges:

```
✓  config/main.yaml         ← green checkmark (validated, no issues)
⚠️  config/test.yaml        ← yellow warning badge
❌ deploy/prd.yaml         ← red error badge
```

Badges propagate to parent folders, so a single error bubbles up the tree.

**Toggle with `strata.showFileDecorations` setting.**

## Error Change Notifications

When you save a file and validation runs:

- **New errors** → notification appears: "*2 new validation errors in config/main.yaml*"
- **Errors fixed** → notification appears: "*Validation passed for deploy/prd.yaml*"
- **No change** → silent (no notification)

Notifications are **only** triggered by on-save validation, not by manual validation commands (to avoid spam).

## Configuration

- `strata.validateOnSave` — Run validation on file save (default: `true`)
- `strata.validateOnType` — Run validation with 1.5s debounce while typing (default: `false`)

Both can be toggled in Settings without reloading the extension.

## Troubleshooting

**"Strata validation failed: strata: command not found"**

→ The CLI is not in your `$PATH`. Fix:

1. Install strata: `pip install strata`
2. Or set `strata.cliPath` to the full path (e.g., `/home/user/.local/bin/strata`)

**"Validation passed but I see errors"**

→ You may have other linters enabled (ESLint, YAML extension). Check the "Source" column in the Problems panel to see which tool reported each error.

**Validation is slow**

→ Phase 2 (cross-reference) validation can be slow with many files. Try:

1. Disable `validateOnType` if it's on
2. Use on-demand validation (Command Palette → Validate Current File) instead of on-save
3. Check `strata log list --last` to see how long the CLI call took

**No validation errors but I expect some**

→ The file might not be recognized as a strata document. Check:

1. Does it have `apiVersion: strata.*` in the first 20 lines?
2. Is it in the workspace root or a known repository?
3. Click "Validate Current File" to force validation (not just on-save)
