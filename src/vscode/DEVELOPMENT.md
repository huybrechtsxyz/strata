# Development Guide — Strata VS Code Extension

How to set up, build, debug, and extend this extension. For what it does and how to use it, see
[README.md](README.md). For history, see [CHANGELOG.md](CHANGELOG.md).

---

## Prerequisites

- **Node.js** 18+ and **npm** 9+ (`node --version`, `npm --version`).
- **VS Code** 1.90 or later.
- The **strata CLI** runnable somehow — this repository's own `uv`-managed checkout works (see
  [Get Started](../../.github/CONTRIBUTING.md#get-started)): `uv sync --group dev`, then point
  `strata.cliPath` at `"uv run strata"`.

## Setup

```powershell
cd src/vscode
npm install
```

## Building

```powershell
npm run compile   # tsc -p ./ — one-shot build into out/
npm run watch      # tsc -watch -p ./ — recompiles on every save
```

Both are plain `tsc`; there is no bundler step (no esbuild/webpack) — `out/` mirrors `src/`
file-for-file.

## Running & Debugging

Press **F5** from the repository root. This uses the root
[`.vscode/launch.json`](../../.vscode/launch.json) ("Run VS Code Extension") configuration, whose
`preLaunchTask` is the `watch` task in [`.vscode/tasks.json`](../../.vscode/tasks.json) — so
editing a `.ts` file, saving, and re-running the Extension Development Host window
(`Ctrl+R`/`Cmd+R` inside it) picks up the change without restarting the debug session.

Logs from `console.log(...)` land in the outer window's **Debug Console**. Breakpoints set in a
`.ts` file work directly — `outFiles` in `launch.json` maps back to source via the `.js.map` files
`tsc`'s `sourceMap` option produces.

## Testing

**Not wired up yet.** `package.json` declares `"test": "vscode-test"` and the
`@vscode/test-cli`/`@vscode/test-electron` dev dependencies are installed, but there is no
`.vscode-test.mjs` config and no test files — running `npm test` will fail. This was a deliberate
scope cut for the initial scaffold (see the CHANGELOG), not an oversight to work around; add the
config and a first suite before relying on it.

## Project Structure

```text
src/vscode/
├── src/
│   ├── extension.ts                  # Entry point (activate/deactivate); wires everything below
│   ├── strataClient.ts               # CLI wrapper — spawns `strata`, parses its JSON envelope
│   ├── fieldLocation.ts              # Dotted field-path -> editor Range heuristic
│   └── providers/
│       ├── diagnosticsProvider.ts    # `strata validate` -> Problems panel
│       ├── graphViewProvider.ts      # `strata graph` -> "Graph" tree view
│       └── statusBarProvider.ts      # `strata version` -> status bar item
├── resources/
│   └── icon.svg                      # Activity Bar container icon
├── package.json                      # Extension manifest + npm scripts/dependencies
├── package-lock.json
├── tsconfig.json
├── .vscodeignore                     # Files excluded from a packaged .vsix
├── README.md
├── CHANGELOG.md
└── DEVELOPMENT.md                    # This file
```

(`out/` and `node_modules/` are build output — gitignored at the repository root.)

## Common Tasks

### Adding a new command

1. Declare it in `package.json` under `contributes.commands`:

   ```json
   {
     "command": "strata.myNewCommand",
     "title": "Strata: My New Command"
   }
   ```

2. Register the handler in `extension.ts`, inside `activate()`:

   ```typescript
   context.subscriptions.push(
     vscode.commands.registerCommand('strata.myNewCommand', async () => {
       // ...
     }),
   );
   ```

If it needs to call the CLI, add a method to `StrataClient` (`strataClient.ts`) rather than
spawning `strata` directly from the command handler — every other provider goes through that one
wrapper so the JSON envelope is parsed and CLI-not-found errors are reported consistently.

### Adding a new tree view

1. Declare the view under `contributes.views` in `package.json` (reuse the existing `strata`
   `viewsContainers` entry, or add a new container if it doesn't belong under "Graph").
2. Implement `vscode.TreeDataProvider<T>` in `src/providers/`, following `graphViewProvider.ts`'s
   shape: a `refresh()` method that calls `StrataClient`, stores the result, and fires
   `onDidChangeTreeData`; `getChildren()`/`getTreeItem()` read only from that stored state — never
   call the CLI from inside them, since VS Code can invoke both at any time.
3. In `extension.ts`, construct the provider, call `.setClient(client)`, and register it:

   ```typescript
   context.subscriptions.push(myView, vscode.window.registerTreeDataProvider('myViewId', myView));
   ```

## Publishing

Not published anywhere yet (no Marketplace listing, no `.vsix` release). To build a local package
for manual installation once there's a reason to:

```powershell
cd src/vscode
npx @vscode/vsce package
```

Then, in VS Code: **Extensions** view → **...** menu → **Install from VSIX...**.
