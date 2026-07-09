#!/usr/bin/env bash
set -e

echo ">>> Installing strata {{ STRATA_VERSION }}..."
pip install --quiet strata=={{ STRATA_VERSION }}

echo ">>> Installing shell completion..."
strata --install-completion bash 2>/dev/null || true

echo ">>> Verifying installation..."
strata --version

echo ""
echo ">>> Setup complete. Run 'strata guide show' to check workspace readiness."
