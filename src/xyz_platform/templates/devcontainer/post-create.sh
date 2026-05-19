#!/usr/bin/env bash
set -e

echo ">>> Installing xyz-platform..."
pip install --quiet git+https://github.com/huybrechtsxzy/xyz-platform.git

echo ">>> Installing shell completion..."
xyz --install-completion bash 2>/dev/null || true

echo ">>> Verifying installation..."
xyz --version

echo ""
echo ">>> Setup complete. Run 'xyz doctor' to verify your environment."
