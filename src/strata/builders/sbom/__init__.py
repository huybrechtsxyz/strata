"""SBOM collector subpackage.

Each module provides one concrete ``BaseSbomCollector`` implementation:

- ``image_collector.ContainerImageCollector``        — container images from modules
- ``compose_collector.ComposeImageCollector``        — images from docker-compose.yml
- ``helm_collector.HelmChartCollector``              — Helm charts from provisioners
- ``terraform_collector.TerraformProviderCollector`` — Terraform providers from .tf files
- ``terraform_module_collector.TerraformModuleCollector`` — Terraform modules from .tf files
- ``ansible_collector.AnsibleCollectionCollector``   — Ansible collections/roles from requirements.yml
- ``deps_collector.DependencyFileCollector``         — application deps from lockfiles/manifests

The lockfile parsing layer (``lockfile_parsers``) provides the registry and
built-in parsers used by ``DependencyFileCollector``:

- ``lockfile_parsers.LockfileParserRegistry`` — maps filename patterns to parsers
- ``lockfile_parsers.DEFAULT_REGISTRY``       — module-level singleton registry
- ``lockfile_parsers.LockfileParser``         — ABC with ``__init_subclass__`` auto-registration
- Built-in parsers: ``RequirementsTxtParser``, ``PyprojectTomlParser``,
  ``UvLockParser``, ``PackageLockJsonParser``, ``GoSumParser``

Workspace-local plugins are loaded by ``collector_plugin_loader.CollectorPluginLoader``.
"""
