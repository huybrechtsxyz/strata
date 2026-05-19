#!/bin/bash
# Test script for deploy_apply lifecycle phase (apply step)
# Corresponds to: terraform apply

echo "=========================================="
echo "Executing DEPLOY_APPLY step"
echo "=========================================="
echo "This step applies the deployment changes"
echo ""
echo "Environment variables:"
echo "  STRATA_WORK_PATH: ${STRATA_WORK_PATH}"
echo "  STRATA_STAGE: ${STRATA_STAGE}"
echo "  STRATA_STEP: ${STRATA_STEP}"
echo ""
echo "Applying changes..."
echo "  [✓] Creating virtualmachine.manager..."
sleep 0.5
echo "  [✓] Creating virtualmachine.worker_1..."
sleep 0.5
echo "  [✓] Creating firewall.STRATA_fw..."
sleep 0.5
echo "  [✓] Modifying namespace.STRATA_base..."
sleep 0.5
echo ""
echo "Apply Summary:"
echo "  Created: 3 resources"
echo "  Modified: 1 resource"
echo "  Destroyed: 0 resources"
echo ""
echo "deploy_apply completed successfully"
exit 0
