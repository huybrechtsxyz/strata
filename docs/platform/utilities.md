# strata - Utilities Documentation

Self-contained utility modules. No cross-imports between utils modules; no imports from `services/`, `controllers/`, or `commands/`.

## `config.py`

Package-level constants. Import instead of hard-coding strings.

```python
from strata.utils.config import SOLUTION_DIR, SOLUTION_FILE, SCRIPT_EXTENSIONS
# SOLUTION_DIR = ".strata"
# SOLUTION_FILE = "solution.json"
# SCRIPT_EXTENSIONS = {".sh", ".bash", ".py", ".ps1"}
```

## `system.py`

Path resolution, UUID generation, subprocess execution.

**`resolve_path(base_path, target_path=None, *sub_paths, repo_map=None)`** — resolves paths with `@repo-name/...` cross-repo reference support. Raises `ValueError` for unknown `@` references or absolute `sub_paths`.

**`resolve_work_path(explicit=None)`** — workspace root discovery: explicit path → `.strata/` upward walk → CWD.

**`normalize_path(path)`** — strips invalid filesystem characters.

**`generate_uuid()`** — UUID v4 string.

**`run_command(args, cwd, timeout, env)`** — subprocess wrapper returning a `CommandResult(stdout, stderr, returncode)`.

```python
from strata.utils.system import resolve_path, resolve_work_path, run_command

path = resolve_path("/base", "@myrepo/config/file.yaml", repo_map={"myrepo": "/repos/myrepo"})
root = resolve_work_path()  # auto-discovers .strata/ ancestor
result = run_command(["git", "status"], cwd="/repo")
```

## `configuration_loader.py`

Low-level YAML loader and deep merger. No schema knowledge.

**`ConfigurationLoader`** — methods:
- `load_yaml_file(file_path: Path)` — load single YAML file
- `load_yaml_files(file_paths: List[Path])` — load and deep-merge a list
- `load_and_merge_yaml_files(base, overrides_list)` — explicit base + overrides
- `deep_merge(base, override)` — recursive dict merge (override wins on conflict)
- `apply_overrides(base, overrides)` — alias for `deep_merge`

```python
from strata.utils.configuration_loader import ConfigurationLoader
from pathlib import Path

loader = ConfigurationLoader()
config = loader.load_yaml_file(Path("config/logging.yaml"))
merged = loader.apply_overrides(config, {"level": "DEBUG"})
```

## `service_cache.py`

In-process service instance cache to avoid re-parsing YAML on repeated loads.

```python
from strata.utils.service_cache import get_cached_service, cache_service, clear_cache

instance = get_cached_service(MyService, file_path="/path/to/file.yaml")
if instance is None:
    instance = MyService.load("/path/to/file.yaml")
    cache_service(MyService, instance, file_path="/path/to/file.yaml")
```

Used internally by `BaseService.load()`. Call `clear_cache()` between tests.

## `templater.py`

File template processor: expands `*.template.*` files using environment variable substitution (`$VAR` / `${VAR}`), then optionally deletes the template source.

```python
from strata.utils.templater import TemplateProcessor
from pathlib import Path

processor = TemplateProcessor(template_dir=Path("terraform/"), cleanup_templates=True)
processor.process_all_templates()  # main.template.tf → main.tf
```

## `ansible_utils.py`

Shared helper for locating Ansible Galaxy requirements files.

**`find_ansible_requirements_file(directory: Path) -> Optional[Path]`** — checks `requirements.yml` then `collections/requirements.yml` under the given directory. Returns the first match or `None`.

Used by both `AnsibleCollectionCollector` and `AnsibleDeployer` to avoid duplicated discovery logic.

```python
from strata.utils.ansible_utils import find_ansible_requirements_file

req = find_ansible_requirements_file(Path("deploy/configure"))
# returns Path("deploy/configure/requirements.yml") or None
```

## `sbom_utils.py`

Pure-function PURL helpers and floating-tag detection. No imports from builders, services, or integrations.

**`is_floating_tag(tag)`** — returns `True` for `None`, empty string, well-known moving tags (`latest`, `main`, `dev`, `edge`, …), or any non-semver string. Returns `False` for pinned semver strings and digests.

**`parse_image_ref(image)`** — splits a container image reference into `(name, tag, digest)`.

**`image_to_purl(image)`** — converts a container image reference to a `pkg:docker/…` PURL string.

**`helm_chart_to_purl(name, version, repository=None)`** — `pkg:helm/…` PURL with optional `repository_url` qualifier.

**`terraform_provider_to_purl(source, version=None)`** — `pkg:terraform/…` PURL.

**`ansible_collection_to_purl(name, version=None)`** / **`ansible_role_to_purl(name, version=None)`** — `pkg:ansible/…` PURLs.

```python
from strata.utils.sbom_utils import is_floating_tag, image_to_purl, helm_chart_to_purl

is_floating_tag("latest")          # True
is_floating_tag("v3.0.1")          # False
image_to_purl("traefik:v3.0.1")   # "pkg:docker/traefik@v3.0.1"
helm_chart_to_purl("authentik", "2024.12.0", "https://charts.goauthentik.io")
# "pkg:helm/authentik@2024.12.0?repository_url=https%3A%2F%2Fcharts.goauthentik.io"
```

## `version.py`

Reads the installed package version from package metadata.

```python
from strata.utils.version import get_version
print(get_version())  # e.g. "1.2.3"
```