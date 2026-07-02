# Installation

## From the Marketplace

The Strata extension is published to the VS Code Marketplace. Search for "Strata" in the Extensions view (`Ctrl+Shift+X` / `Cmd+Shift+X`) and click **Install**.

## From source

If you're developing the extension or want to install a specific version:

```bash
cd strata/src/vscode
npm install
npx tsc
npx @vscode/vsce package --no-dependencies --skip-license
code --install-extension strata-*.vsix --force
```

## Requirements

- **VS Code:** 1.90.0 or higher
- **Strata CLI:** The extension runs `strata` commands. Make sure the CLI is available in your `$PATH` or configure the path in settings.
- **YAML Extension:** (Optional) For schema-driven editing, install [Red Hat YAML](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml).

## Activation

The extension activates automatically when VS Code detects a `.strata/solution.json` file in the open workspace. If the workspace has no `.strata/` directory, the extension remains inactive until you initialize one with `strata sln init`.

## Settings

After installing, configure the CLI path and validation behavior:

1. Open VS Code settings (`Ctrl+,` / `Cmd+,`)
2. Search for "strata"
3. Adjust:
   - `strata.cliPath` — CLI executable (default: `"strata"`)
   - `strata.validateOnSave` — Auto-validate on save (default: `true`)
   - `strata.validateOnType` — Auto-validate with 1.5s delay (default: `false`)
   - `strata.showFileDecorations` — Show badges in file tree (default: `true`)
   - Other display options

See [Settings](settings.md) for the full list.
