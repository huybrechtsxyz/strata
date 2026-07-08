#!/usr/bin/env bash
set -e

echo ">>> Installing strata..."
pip install --quiet git+https://github.com/huybrechtsxzy/strata.git

echo ">>> Installing shell completion..."
strata --install-completion bash 2>/dev/null || true

echo ">>> Verifying installation..."
strata --version

echo ""
echo ">>> Setup complete. Run 'strata doctor' to verify your environment."
