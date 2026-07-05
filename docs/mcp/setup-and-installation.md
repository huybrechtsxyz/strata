# Setup & Installation

Start the strata MCP server and configure your AI client to connect.

## Prerequisites

- **strata CLI** — installed and working
  ```bash
  strata --version
  ```
- **Python 3.10+** — required by strata
- **MCP optional dependency** — must be installed separately
- **Workspace** — initialized with `strata sln init`

## Step 1: Install the MCP Optional Dependency

The MCP server requires the `mcp` package, which is optional for strata to keep the base installation lightweight.

### Option A: Install in Your Project Environment

If you're developing a strata integration:

```bash
pip install xyz-strata[mcp]
```

This installs strata with the MCP extra, which includes the `mcp` package and FastMCP.

### Option B: Install in a Global Environment

If you want to run the server independently:

```bash
# Create a virtual environment
python -m venv ~/.strata/mcp-env

# Activate it
source ~/.strata/mcp-env/bin/activate  # macOS/Linux
# or
~\.strata\mcp-env\Scripts\Activate.ps1  # Windows PowerShell

# Install strata with MCP
pip install xyz-strata[mcp]
```

### Verify Installation

```bash
strata mcp serve --help
```

You should see the command help. If you get "command not found" or "unknown command", the MCP package isn't installed.

## Step 2: Start the MCP Server

The server communicates via **stdio** (standard input/output), which is the standard MCP transport.

```bash
strata mcp serve
```

The server will:
1. Detect the workspace root (from CWD by walking up to find `.strata/`)
2. Load workspace configuration
3. Print a startup message to stderr
4. Begin listening for JSON-RPC requests on stdin

**Important**: The server runs indefinitely. It's designed to be spawned by your MCP client (Claude, Copilot, etc.) and runs until the client closes stdin.

### Specify Workspace Explicitly

If you want to run the server from a different directory:

```bash
cd /path/to/my/workspace && strata mcp serve
```

Or set the environment variable:

```bash
export STRATA_WORK_PATH=/path/to/my/workspace
strata mcp serve
```

## Step 3: Configure Your MCP Client

Each AI platform configures MCP servers differently. Choose your platform below.

### Claude (via Claude Desktop)

1. **Create or edit** `~/.claude/claude-config.json` (macOS/Linux) or `%APPDATA%\Roaming\Claude\claude-config.json` (Windows)

2. **Add the strata MCP server** under `mcpServers`:

   ```json
   {
     "mcpServers": {
       "strata": {
         "command": "strata",
         "args": ["mcp", "serve"],
         "cwd": "/path/to/my/strata/workspace"
       }
     }
   }
   ```

3. **Restart Claude** to load the new config.

### Claude API / Python SDK

If using Claude Anthropic API with MCP:

```python
import subprocess
from anthropic import Anthropic

client = Anthropic()

# Configure MCP server
strata_server = {
    "name": "strata",
    "uri": "stdio://path/to/my/workspace",
    "command": "strata",
    "args": ["mcp", "serve"],
}

# Start a conversation with strata tools available
response = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    system="You have access to strata infrastructure tools. Use them to validate, build, and preview deployments.",
    messages=[
        {
            "role": "user",
            "content": "Validate my deployment file and show me what would be built.",
        }
    ],
    tools=[...],  # Strata tools will be auto-discovered via MCP
)
```

### GitHub Copilot

The VS Code extension currently uses the CLI directly (not MCP). MCP integration ideas are documented in [Copilot Integration](copilot-integration.md).

For Copilot extension development:

```json
// .vscode/settings.json
{
  "codeium.enableCodeiumServer": true,
  "strata.mcp.server": "strata mcp serve",
  "strata.mcp.workspacePath": "${workspaceFolder}"
}
```

### Generic MCP Client (Python / Node.js)

If building a custom MCP client:

```python
# Python example
import subprocess
import json

# Start strata MCP server
server_process = subprocess.Popen(
    ["strata", "mcp", "serve"],
    cwd="/path/to/my/workspace",
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

# Send JSON-RPC requests to stdin
request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "workspace_status",
        "arguments": {},
    },
}

server_process.stdin.write(json.dumps(request) + "\n")
server_process.stdin.flush()

# Read response from stdout
response = server_process.stdout.readline()
print(json.loads(response))
```

## Step 4: Test the Connection

Once configured, test that your AI client can reach the strata server.

### In Claude Desktop

Send a message:
> "What tools do you have access to?"

Claude should mention strata tools like `workspace_status`, `validate_file`, `build_plan`, etc.

### Programmatically

```bash
# Send a test request via stdin
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_schemas","arguments":{}}}' | strata mcp serve
```

## Troubleshooting

### Error: "command not found: strata"

**Solution**: Ensure strata is installed and in your `$PATH`:
```bash
which strata
strata --version
```

### Error: "The `mcp` package is required"

**Solution**: Install the MCP optional dependency:
```bash
pip install xyz-strata[mcp]
```

### Error: "Not inside a strata workspace"

**Solution**: The server can't find `.strata/`. Either:
1. Run the server from inside a workspace directory
2. Set `STRATA_WORK_PATH` environment variable
3. Specify `cwd` in your MCP client configuration

```bash
cd /path/to/workspace && strata mcp serve
# or
export STRATA_WORK_PATH=/path/to/workspace && strata mcp serve
```

### Server starts but client can't connect

**Solution**: Verify stdio transport is working:
```bash
echo '{}' | strata mcp serve 2>&1 | head -20
```

You should see a response (even if it's an error). If you see nothing, check that:
- The MCP package is installed
- The workspace path is valid
- Firewall/antivirus isn't blocking subprocess communication

## Environment Variables

Configure server behavior via environment variables:

| Variable           | Purpose                             | Example              |
| ------------------ | ----------------------------------- | -------------------- |
| `STRATA_WORK_PATH` | Workspace root (overrides CWD walk) | `/path/to/workspace` |
| `STRATA_VERBOSE`   | Enable verbose logging              | `1`                  |
| `STRATA_QUIET`     | Suppress non-error output           | `1`                  |
| `STRATA_OUTPUT`    | Output format                       | `json` or `text`     |

Example:
```bash
export STRATA_WORK_PATH=/my/workspace
export STRATA_VERBOSE=1
strata mcp serve
```

## Next Steps

1. **[Configure Claude](claude-deployment-assistant.md)** for a full walkthrough
2. **[Review Security](security-and-workflows.md)** before using in production
3. **[Explore Tools](tools-reference.md)** to see what's available
