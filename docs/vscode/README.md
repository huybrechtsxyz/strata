############
VS Code Extension
############

The Strata VS Code extension brings the full power of the strata CLI into your editor. It provides:

- **Real-time validation** with squiggly underlines in the Problems panel
- **Tree views** showing workspace structure, repositories, and tools
- **CodeLens** for inline validate, build, deploy, and schema actions
- **Chat integration** with `@strata` in Copilot Chat for AI-powered assistance
- **File decorations** with ✅ / ⚠️ / ❌ badges on validated files
- **Command Palette** for all strata operations
- **Dependency graphs** showing how documents relate
- **Auto-refresh** when `.strata/solution.json` changes externally

The extension **consumes the CLI directly** via `child_process.execFile`, not the MCP server. This means:

- **No process lifecycle complexity** — spawn the CLI on each call, parse the JSON response
- **Works offline** — no separate MCP server to start
- **Same JSON envelope** as the CLI — `{ success, command, data, errors, messages }`

.. toctree::
   :maxdepth: 2

   installation
   features
   tree-views
   diagnostics
   codelens
   chat
   settings
   keyboard-shortcuts
