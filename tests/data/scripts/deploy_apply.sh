#!/bin/bash
# Test script for deploy_apply lifecycle phase (apply step)
# Corresponds to: terraform apply

echo "=========================================="
echo "Executing DEPLOY_APPLY step"
echo "=========================================="
echo "This step applies the deployment changes"
echo ""
echo "Environment variables:"
echo "  XYZ_WORK_PATH: ${XYZ_WORK_PATH}"
echo "  XYZ_STAGE: ${XYZ_STAGE}"
echo "  XYZ_STEP: ${XYZ_STEP}"
echo ""
echo "Applying changes..."
echo "  [✓] Creating virtualmachine.manager..."
sleep 0.5
echo "  [✓] Creating virtualmachine.worker_1..."
sleep 0.5
echo "  [✓] Creating firewall.xyz_fw..."
sleep 0.5
echo "  [✓] Modifying namespace.xyz_base..."
sleep 0.5
echo ""
echo "Apply Summary:"
echo "  Created: 3 resources"
echo "  Modified: 1 resource"
echo "  Destroyed: 0 resources"
echo ""
echo "deploy_apply completed successfully"
exit 0
