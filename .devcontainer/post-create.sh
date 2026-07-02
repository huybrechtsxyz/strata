#!/usr/bin/env bash
set -e

echo ">>> Setting up strata development environment..."
echo ""

# Python setup
echo "[1/4] Installing Python dependencies with uv..."
uv sync --all-extras
if [ $? -ne 0 ]; then
    echo "Error: Failed to install Python dependencies."
    exit 1
fi
echo "✓ Python dependencies installed"
echo ""

# Install strata in editable mode (already done by uv sync, but verify)
echo "[2/4] Verifying strata CLI installation..."
strata --version
if [ $? -ne 0 ]; then
    echo "Error: strata CLI not available."
    exit 1
fi
echo "✓ strata CLI is ready"
echo ""

# Shell completion for bash
echo "[3/4] Installing shell completion..."
strata --install-completion bash 2>/dev/null || echo "Note: Shell completion installation skipped (optional)"
echo "✓ Completion configured"
echo ""

# VS Code Extension setup
echo "[4/4] Installing VS Code extension dependencies..."
cd "$WORKSPACE_DIR/src/vscode" || { echo "Error: VS Code extension directory not found"; exit 1; }
npm install
if [ $? -ne 0 ]; then
    echo "Error: Failed to install Node.js dependencies for extension."
    exit 1
fi
echo "✓ Extension dependencies installed"
echo ""

echo "=========================================="
echo "✓ Development environment ready!"
echo "=========================================="
echo ""
echo "📋 Next steps:"
echo "  1. Run 'strata doctor' to verify your environment"
echo "  2. Run 'npm run watch' in src/vscode/ to compile TypeScript"
echo "  3. Press F5 in VS Code to launch the extension"
echo ""
echo "📚 Useful commands:"
echo "  uv run strata --help          - Run strata CLI"
echo "  cd src/vscode && npm run lint  - Lint extension code"
echo "  cd src/vscode && npm test      - Test extension"
echo ""
