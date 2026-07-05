# Extending strata SBOM — Custom Collectors and Parsers

Create custom collectors and lockfile parsers to extend SBOM generation for your specific dependency formats and component sources.

---

## Overview

strata SBOM has two plugin points:

### 1. Lockfile Parsers — zero-config drop-in

Add support for new dependency manifest formats by dropping a Python file into `.strata/lockfile_parsers/`:

```bash
.strata/
└── lockfile_parsers/
    ├── cargo_lock.py           # Custom Rust parser
    ├── pdm_lock.py             # Custom Python parser
    └── private_pip_index.py    # Private package index parser
```

Auto-discovered on every `strata build sbom` run. **No configuration needed.**

### 2. Collectors — config-driven

Add a collector for non-standard dependency file types or custom component sources by declaring it in `.strata/collectors.yaml`:

```yaml
collectors:
  - name: my-custom-collector
    path: .strata/collectors/my_collector.py
    class: MyCollector
    type: collector
```

Used by `strata build sbom` after built-in collectors (image, helm, terraform, ansible, deps).

---

## Quick Start

### Option A: Add a Lockfile Parser (easiest)

For a new dependency manifest format (e.g., `Pipenv.lock`, private registry lock files):

```python
# .strata/lockfile_parsers/pipenv_lock.py

from pathlib import Path
from typing import List
import json

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class PipenvLockParser(LockfileParser):
    """Parse Pipenv lock files."""

    @property
    def ecosystem(self) -> str:
        return "pypi"

    def filename_patterns(self) -> List[str]:
        return ["Pipenv.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for section in ["default", "develop"]:
            packages = data.get(section) or {}
            for name, info in packages.items():
                version = info.get("version", "").lstrip("=")
                deps.append(RawDependency(name=name, version=version or None))
        return deps
```

Run:

```bash
strata build sbom -f deploy.yaml
# Custom parser is auto-discovered and used
```

### Option B: Add a Collector (advanced)

For custom components that aren't dependency files (e.g., Terraform variables, custom binaries):

```python
# .strata/collectors/custom_binary_collector.py

from pathlib import Path
from typing import List

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel


class CustomBinaryCollector(BaseSbomCollector):
    """Collect custom binary components."""

    def get_collector_name(self) -> str:
        return "custom_binary"

    def collect(
        self,
        platform: PlatformArtifactModel,
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        self._reset_warnings()
        components: List[SbomComponentModel] = []

        # Your custom collection logic here
        # Extract components from platform artifact or scan filesystem

        return components
```

Declare in `.strata/collectors.yaml`:

```yaml
collectors:
  - name: custom-binaries
    path: .strata/collectors/custom_binary_collector.py
    class: CustomBinaryCollector
    type: collector
```

---

## Lockfile Parser API

### Base Class: `LockfileParser`

```python
from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency
```

Implement these three methods:

#### 1. `ecosystem` (property)

Return a purl type identifier:

```python
@property
def ecosystem(self) -> str:
    """Return purl type: 'pypi', 'npm', 'golang', 'maven', 'gem', 'crate', etc."""
    return "pypi"
```

**Common purl types:**

| Ecosystem | purl type | Examples |
|-----------|-----------|----------|
| Python | `pypi` | requests, flask, pandas |
| JavaScript | `npm` | react, express, lodash |
| Go | `golang` | github.com/sirupsen/logrus |
| Java/Maven | `maven` | org.apache:commons-lang3 |
| Ruby | `gem` | rails, sinatra, bundler |
| Rust | `crate` | serde, tokio |
| .NET | `nuget` | Newtonsoft.Json, AutoMapper |
| PHP | `composer` | symfony/console, laravel/framework |

See [purl spec](https://github.com/package-url/purl-spec) for the full list.

#### 2. `filename_patterns()` method

Return a list of glob patterns matched against filenames (not full paths):

```python
def filename_patterns(self) -> List[str]:
    """Return glob patterns to match manifest files."""
    return ["requirements*.txt", "requirements-*.txt"]
```

Patterns are matched case-insensitively against the **filename only**, not the directory path. Examples:

```python
["Pipenv.lock"]              # Match only Pipenv.lock
["package-lock.json"]        # npm lockfile
["requirements*.txt"]        # requirements.txt, requirements-dev.txt, etc.
["go.sum"]                   # Go module dependencies
["Gemfile.lock"]             # Ruby Bundler
["*.toml"]                   # Any TOML file (risky; use specific names)
```

#### 3. `parse(path: Path) -> List[RawDependency]` method

Extract (name, version) pairs from the file. **Must raise `ValueError` on parse failure** — the collector catches it, logs a warning, and continues.

```python
def parse(self, path: Path) -> List[RawDependency]:
    """Extract dependency pairs from the manifest file.
    
    Raise ValueError on parse failure (file not found, invalid format, etc.).
    Never raise other exceptions.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Could not read {path}: {exc}") from exc
    
    deps: List[RawDependency] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, version = parse_line(line)  # Your parsing logic
        deps.append(RawDependency(name=name, version=version))
    return deps
```

### `RawDependency` — Return Type

```python
class RawDependency(NamedTuple):
    name: str              # Package name
    version: Optional[str] # Version or None if unpinned
```

**Version handling:**

- **Pinned:** `RawDependency("flask", "2.3.1")` → produces a versioned component
- **Unpinned:** `RawDependency("flask", None)` → component has no version; purl has no `@version`
- **Range/constraint:** `RawDependency("flask", ">=2.0,<3.0")` → version field contains the constraint string

### Auto-Registration

Parsers **auto-register** when the module is imported — you don't call any registration function:

```python
class MyParser(LockfileParser):
    """Subclass of LockfileParser with all abstractmethods implemented."""
    # This class is automatically registered into DEFAULT_REGISTRY
    # when the module is imported.
    ...

# No need for:
# DEFAULT_REGISTRY.register(MyParser())  ← Happens automatically
```

**To skip auto-registration** (for abstract base classes or test stubs):

```python
class MyAbstractParser(LockfileParser, register=False):
    """This will NOT be registered."""
    ...
```

---

## Collector API

### Base Class: `BaseSbomCollector`

```python
from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel
```

Implement these three methods:

#### 1. `get_collector_name()` method

Return a short identifier:

```python
def get_collector_name(self) -> str:
    """Return identifier: 'image', 'helm', 'custom_scanner', etc."""
    return "my_collector"
```

Used in logs and in the `source_collector` field of components.

#### 2. `collect()` method

Extract components from the platform artifact:

```python
def collect(
    self,
    platform: PlatformArtifactModel,
    work_path: Path,
    deployment_build_path: Path,
) -> List[SbomComponentModel]:
    """Extract components from the platform artifact.
    
    Args:
        platform: Assembled platform artifact model (contains spec.modules, etc.)
        work_path: Workspace root directory
        deployment_build_path: Deployment build directory (e.g., build/{deployment}/{version}/)
    
    Returns:
        List of SbomComponentModel instances. Empty list if no components found.
    """
    self._reset_warnings()  # Clear warnings from previous call
    components: List[SbomComponentModel] = []
    
    # Your extraction logic
    # Use platform.spec, work_path, or deployment_build_path as needed
    
    return components
```

#### 3. Warnings handling

Collect non-fatal issues (missing files, parse errors) in the warnings list:

```python
def collect(self, platform, work_path, deployment_build_path):
    self._reset_warnings()
    components: List[SbomComponentModel] = []
    
    # ... collection logic ...
    
    if missing_file:
        self._warnings.append("Optional config file not found: /path/to/file")
    
    return components

# After calling collect(), caller reads warnings:
components = my_collector.collect(...)
for warning in my_collector.get_warnings():
    logger.warning("Collector warning", msg=warning)
```

### `SbomComponentModel` — Return Type

```python
class SbomComponentModel:
    component_type: str           # "container" | "library" | "framework"
    name: str                     # Component name
    version: Optional[str]        # Version string or None
    purl: str                     # Package URL (pkg:docker/..., pkg:helm/..., etc.)
    properties: Dict[str, str]    # Metadata (e.g., {"strata:tag-stability": "floating"})
    source_collector: str         # Collector name that produced this component
```

**Example:**

```python
components.append(
    SbomComponentModel(
        component_type="container",
        name="nginx",
        version="1.25.0",
        purl="pkg:docker/library/nginx@1.25.0",
        properties={"strata:tag-stability": "floating"},
        source_collector=self.get_collector_name(),
    )
)
```

---

## Complete Example: Python Private Index Parser

Support a private package index with a custom lockfile format:

```python
# .strata/lockfile_parsers/private_pypi.py
"""Parse private PyPI JSON index files."""

import json
from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class PrivatePyPiParser(LockfileParser):
    """Parse private PyPI JSON index (internal tool format).
    
    File format:
    {
      "packages": {
        "mylib": {"version": "1.0.2", "url": "https://private-index/mylib-1.0.2.tar.gz"},
        "otherlib": {"version": "2.1.0", ...}
      }
    }
    """

    @property
    def ecosystem(self) -> str:
        return "pypi"

    def filename_patterns(self) -> List[str]:
        return ["private-index.json"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Failed to parse {path.name}: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")

        packages = data.get("packages") or {}
        deps: List[RawDependency] = []

        for name, info in packages.items():
            if not isinstance(info, dict):
                continue
            version = info.get("version")
            deps.append(RawDependency(name=str(name), version=version or None))

        return deps
```

---

## Complete Example: Custom Collector — Terraform Variables

Extract version constraints from Terraform variable defaults:

```python
# .strata/collectors/terraform_vars_collector.py
"""Collect Terraform provider version constraints."""

import re
from pathlib import Path
from typing import List

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel


class TerraformVarsCollector(BaseSbomCollector):
    """Extract version constraints from Terraform provider requirements.
    
    Scans platform artifact for declared Terraform providers and their
    required versions, producing a component per provider.
    """

    def get_collector_name(self) -> str:
        return "terraform_vars"

    def collect(
        self,
        platform: PlatformArtifactModel,
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        self._reset_warnings()
        components: List[SbomComponentModel] = []

        if not platform.spec or not platform.spec.modules:
            return components

        seen_providers: set[str] = set()

        # Iterate through all modules
        for module in platform.spec.modules:
            # Assume module has a 'terraform_providers' field
            # (This is an example; adjust to your actual structure)
            providers = getattr(module, "terraform_providers", None) or []

            for provider in providers:
                # provider: {"name": "aws", "version": "~> 5.0"}
                provider_name = getattr(provider, "name", "")
                provider_version = getattr(provider, "version", None)

                if not provider_name:
                    continue

                purl_key = f"terraform:{provider_name}"
                if purl_key in seen_providers:
                    continue
                seen_providers.add(purl_key)

                components.append(
                    SbomComponentModel(
                        component_type="framework",
                        name=f"terraform-provider-{provider_name}",
                        version=provider_version,
                        purl=f"pkg:terraform/hashicorp/{provider_name}@{provider_version or 'unknown'}",
                        properties={"provider_type": "terraform"},
                        source_collector=self.get_collector_name(),
                    )
                )

        return components
```

Declare in `.strata/collectors.yaml`:

```yaml
collectors:
  - name: terraform-vars
    path: .strata/collectors/terraform_vars_collector.py
    class: TerraformVarsCollector
    type: collector
```

---

## Testing Custom Plugins

### Testing Lockfile Parsers

```python
# tests/strata/sbom/test_private_pypi_parser.py

from pathlib import Path
import pytest
from strata.builders.sbom.lockfile_parsers.private_pypi import PrivatePyPiParser


class TestPrivatePyPiParser:
    def test_parse_valid(self, tmp_path):
        """Parse a valid private PyPI index."""
        parser = PrivatePyPiParser()
        
        index_file = tmp_path / "private-index.json"
        index_file.write_text('''{
            "packages": {
                "mylib": {"version": "1.0.2"},
                "otherlib": {"version": "2.1.0"}
            }
        }''')
        
        deps = parser.parse(index_file)
        assert len(deps) == 2
        assert deps[0].name == "mylib"
        assert deps[0].version == "1.0.2"

    def test_parse_missing_version(self, tmp_path):
        """Handle packages without explicit version."""
        parser = PrivatePyPiParser()
        
        index_file = tmp_path / "private-index.json"
        index_file.write_text('''{
            "packages": {"mylib": {}}
        }''')
        
        deps = parser.parse(index_file)
        assert len(deps) == 1
        assert deps[0].version is None

    def test_parse_invalid_json(self, tmp_path):
        """Invalid JSON raises ValueError."""
        parser = PrivatePyPiParser()
        
        index_file = tmp_path / "private-index.json"
        index_file.write_text("not valid json")
        
        with pytest.raises(ValueError):
            parser.parse(index_file)

    def test_filename_patterns(self):
        """Parser matches correct filename patterns."""
        parser = PrivatePyPiParser()
        assert "private-index.json" in parser.filename_patterns()
```

### Testing Collectors

```python
# tests/strata/sbom/test_terraform_vars_collector.py

from pathlib import Path
from unittest.mock import MagicMock
import pytest
from strata.builders.sbom.terraform_vars_collector import TerraformVarsCollector
from strata.models.platform_artifact_model import PlatformArtifactModel


class TestTerraformVarsCollector:
    def test_collect_empty_platform(self, tmp_path):
        """Collector returns empty list when no modules."""
        collector = TerraformVarsCollector()
        
        # Mock a minimal platform with no modules
        platform = MagicMock(spec=PlatformArtifactModel)
        platform.spec = None
        
        components = collector.collect(platform, tmp_path, tmp_path / "build")
        assert len(components) == 0

    def test_collect_providers(self, tmp_path):
        """Extract provider components."""
        collector = TerraformVarsCollector()
        
        # Build a mock platform with providers
        provider1 = MagicMock()
        provider1.name = "aws"
        provider1.version = "~> 5.0"
        
        provider2 = MagicMock()
        provider2.name = "kubernetes"
        provider2.version = "2.20.0"
        
        module = MagicMock()
        module.terraform_providers = [provider1, provider2]
        
        platform = MagicMock(spec=PlatformArtifactModel)
        platform.spec = MagicMock()
        platform.spec.modules = [module]
        
        components = collector.collect(platform, tmp_path, tmp_path / "build")
        
        assert len(components) == 2
        assert components[0].name == "terraform-provider-aws"
        assert components[1].name == "terraform-provider-kubernetes"
```

### Running Tests

```bash
# Run tests in the workspace
uv run pytest tests/strata/sbom/ -v

# Run a specific test
uv run pytest tests/strata/sbom/test_private_pypi_parser.py::TestPrivatePyPiParser::test_parse_valid -v
```

---

## Auto-Discovery Behavior

### Lockfile Parsers (`.strata/lockfile_parsers/`)

Every `*.py` file (except those starting with `_`) in `.strata/lockfile_parsers/` is:

1. Imported automatically on `strata build sbom`
2. Any `LockfileParser` subclass in the module is auto-registered
3. Changes take effect **immediately** (no CLI flag or config needed)

**Behavior:**

```
.strata/
└── lockfile_parsers/
    ├── cargo_lock.py         ✓ Auto-discovered and loaded
    ├── pdm_lock.py           ✓ Auto-discovered and loaded
    ├── _testing_helpers.py   ✗ Skipped (starts with _)
    └── __init__.py           ✗ Skipped (no subclasses expected)
```

### Collectors (`.strata/collectors/`)

Collectors are loaded only if declared in `.strata/collectors.yaml`:

```yaml
collectors:
  - name: my-collector
    path: .strata/collectors/my_collector.py
    class: MyCollector
    type: collector
```

**Both of these work:**

1. **By file path** (relative to workspace):
   ```yaml
   path: .strata/collectors/my_collector.py
   ```

2. **By Python module** (if installed in the environment):
   ```yaml
   module: my.custom.collectors
   ```

---

## Lifecycle & Error Handling

### Lockfile Parser Lifecycle

1. **Discovery** — `strata build sbom` scans `.strata/lockfile_parsers/`
2. **Import** — Each `*.py` module is imported
3. **Auto-register** — `LockfileParser.__init_subclass__` fires; subclass is added to `DEFAULT_REGISTRY`
4. **Matching** — `DependencyFileCollector` finds files matching `filename_patterns()`
5. **Parse** — `parser.parse(file)` is called
6. **Error handling** — If `parse()` raises `ValueError`, collector logs a warning and continues; no other exception types are caught

**Parse errors are gracefully handled:**

```python
def parse(self, path: Path) -> List[RawDependency]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Raise ValueError — collector will catch it
        raise ValueError(f"Invalid JSON: {exc}") from exc
    # ... continue parsing ...
```

### Collector Lifecycle

1. **Discovery** — `.strata/collectors.yaml` is read
2. **Loading** — Class is imported, instance created
3. **Validation** — Instance must be a `BaseSbomCollector` subclass
4. **Execution** — `collector.collect(platform, work_path, deployment_build_path)` is called
5. **Warnings** — After `collect()`, caller reads `collector.get_warnings()`

---

## Debugging & Troubleshooting

### Enable debug logging

```bash
export STRATA_VERBOSE=1
strata build sbom -f deploy.yaml
```

You'll see logs like:

```
DEBUG Auto-discovered lockfile parser | file=private_pypi.py
DEBUG Loaded lockfile_parser plugin module | plugin=cargo-parser
DEBUG Scanning for dependency files | pattern=private-index.json
DEBUG Failed to parse dependency file | file=private-index.json error="Invalid JSON"
```

### Check which plugins are loaded

```python
# In Python REPL or script
from strata.builders.sbom.lockfile_parsers import DEFAULT_REGISTRY

for parser in DEFAULT_REGISTRY.all_parsers():
    print(f"Ecosystem: {parser.ecosystem}, Patterns: {parser.filename_patterns()}")
```

### Test parse in isolation

```python
from pathlib import Path
from strata.builders.sbom.lockfile_parsers.private_pypi import PrivatePyPiParser

parser = PrivatePyPiParser()
path = Path("path/to/private-index.json")

try:
    deps = parser.parse(path)
    for dep in deps:
        print(f"{dep.name}=={dep.version}")
except ValueError as exc:
    print(f"Parse error: {exc}")
```

### Verify collector is loaded

```bash
# Check .strata/collectors.yaml syntax
strata validate .strata/collectors.yaml --deep

# Run SBOM build with debug output
export STRATA_VERBOSE=1
strata build sbom -f deploy.yaml 2>&1 | grep -i "custom\|collector"
```

---

## Best Practices

### Lockfile Parsers

1. **Be strict with filenames** — Use exact names or narrow patterns:
   - `["requirements.txt"]` — exact match
   - `["requirements*.txt"]` — good
   - `["*.txt"]` — too broad, matches unrelated files

2. **Raise `ValueError` on parse failure** — Never raise other exception types:
   ```python
   try:
       data = parse_file(path)
   except IOError as exc:
       raise ValueError(f"Could not read file: {exc}") from exc  # ✓ Correct
   except Exception as exc:
       raise exc  # ✗ Wrong — not caught by collector
   ```

3. **Handle missing/unpinned versions** — Use `version=None`:
   ```python
   deps.append(RawDependency(name="flask", version=None))  # ✓ Unpinned
   ```

4. **Use ecosystem names from purl spec** — `pypi`, `npm`, `maven`, `crate`, `gem`, `nuget`, `composer`, `golang`.

### Collectors

1. **Call `_reset_warnings()` at the start of `collect()`**:
   ```python
   def collect(self, ...):
       self._reset_warnings()  # Clear warnings from previous call
   ```

2. **Use `self._warnings.append(msg)` for non-fatal issues**:
   ```python
   if file_not_found:
       self._warnings.append("Optional config not found: /path/to/file")
   ```

3. **Deduplicate by purl** — Check if you've already seen a component:
   ```python
   seen_purls: set[str] = set()
   for component in components:
       if component.purl in seen_purls:
           continue
       seen_purls.add(component.purl)
   ```

4. **Set accurate component types** — Use `container`, `library`, or `framework`:
   - `container` — Docker images
   - `library` — Dependencies (npm, pip, maven, etc.)
   - `framework` — Infrastructure code (terraform modules, helm charts, ansible)

5. **Include a meaningful `source_collector` name** — Used in logs and component metadata.

---

## Integration with strata

### Build Phase

When you run:

```bash
strata build sbom -f deploy/production.yaml
```

The build process:

1. Loads `platform.json` artifact from the previous `strata build run`
2. Instantiates built-in collectors (image, helm, terraform, ansible, deps)
3. Loads custom collectors from `.strata/collectors.yaml`
4. Discovers custom lockfile parsers from `.strata/lockfile_parsers/`
5. Calls `collect()` on each collector
6. Generates `sbom.json` (CycloneDX format)
7. Produces `platform.json` with SBOM reference in `metadata.sbom`

### Audit Export

Include SBOM in deployment manifests:

```bash
strata audit export --include-manifests --output audit.json
```

Each manifest includes a reference to the SBOM:

```json
{
  "metadata": {
    "sbom": {
      "path": "build/prod-1.0.0/sbom.json",
      "format": "cyclonedx-1.6",
      "component_count": 42
    }
  }
}
```

---

## See Also

- [SBOM Builder Architecture](../platform/sbom-builder.md) — Internal design reference
- [Testing Patterns](../guides/testing-strata-extensions.md) — More testing examples
- [Configuration Schema](../config/configuration.md) — spec.modules, spec.repositories
