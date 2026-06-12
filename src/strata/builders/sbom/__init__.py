"""SBOM collector subpackage.

Each module provides one concrete ``BaseSbomCollector`` implementation:

- ``image_collector.ContainerImageCollector``   — container images from modules
- ``helm_collector.HelmChartCollector``         — Helm charts from provisioners
- ``terraform_collector.TerraformProviderCollector`` — Terraform providers from .tf files
- ``ansible_collector.AnsibleCollectionCollector``  — Ansible collections/roles from requirements.yml
"""
