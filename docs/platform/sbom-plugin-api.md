# SBOM Plugin API Reference

Complete API reference for custom SBOM collectors and lockfile parsers.

---

## Lockfile Parser Base Class

### `strata.builders.sbom.lockfile_parsers._base.LockfileParser`

Abstract base class for dependency manifest parsers.

```python
from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency
```

#### Auto-Registration

Concrete subclasses are **automatically registered** into `DEFAULT_REGISTRY` when the class is defined. No explicit registration call is needed:

```python
class MyParser(LockfileParser):
    # This is automatically registered when the module is imported
    ...
```

To suppress auto-registration (for abstract bases or test stubs):

```python
class MyAbstractParser(LockfileParser, register=False):
    # This will NOT be registered
    ...
```

#### Abstract Properties

##### `ecosystem` (property)

```python
@property
@abstractmethod
def ecosystem(self) -> str:
    """Return the purl type identifier for this ecosystem.
    
    Examples: "pypi", "npm", "golang", "maven", "gem", "crate", "nuget", "composer"
    
    Returns:
        str: Purl type identifier
    """
```

Used to construct the Package URL (purl) for each dependency.

#### Abstract Methods

##### `filename_patterns() -> List[str]`

```python
@abstractmethod
def filename_patterns(self) -> List[str]:
    """Return glob patterns to match against manifest filenames.
    
    Patterns are matched case-insensitively against the **filename** 
    (not the full path). The DependencyFileCollector scans all 
    workspaces using fnmatch and rglob.
    
    Examples:
        ["requirements*.txt"]           # requirements.txt, requirements-dev.txt
        ["package-lock.json"]           # npm lockfile
        ["Pipenv.lock"]                 # Pipenv lock
        ["go.sum"]                      # Go dependencies
        ["Gemfile.lock"]                # Ruby Bundler
        ["pom.xml"]                     # Maven
        ["Cargo.lock"]                  # Rust
    
    Returns:
        List[str]: List of glob patterns
    """
```

**Important:** Patterns are case-insensitive and matched against filenames only.

##### `parse(path: Path) -> List[RawDependency]`

```python
@abstractmethod
def parse(self, path: Path) -> List[RawDependency]:
    """Extract dependencies from a manifest file.
    
    The DependencyFileCollector calls this method for every file matching
    filename_patterns(). Parse failures **must raise ValueError** — 
    other exception types are not caught.
    
    Args:
        path (Path): Path to the manifest file
    
    Returns:
        List[RawDependency]: List of (name, version) pairs
    
    Raises:
        ValueError: On any parse error (file read failure, invalid format, etc.)
                    This is the ONLY exception type the collector catches.
    
    Examples:
        Parse success with pinned versions:
            >>> return [
            ...     RawDependency("flask", "2.3.1"),
            ...     RawDependency("requests", "2.31.0"),
            ... ]
        
        Parse with unpinned version:
            >>> return [
            ...     RawDependency("flask", "2.3.1"),
            ...     RawDependency("six", None),  # Version not specified
            ... ]
    """
```

---

### `RawDependency` NamedTuple

```python
from strata.builders.sbom.lockfile_parsers._base import RawDependency
```

Represents a single (name, version) pair extracted from a manifest.

```python
class RawDependency(NamedTuple):
    name: str              # Package name
    version: Optional[str] # Version string, constraint, or None if unpinned
```

#### Construction

```python
# Pinned version
RawDependency(name="flask", version="2.3.1")

# Unpinned (no version specified in manifest)
RawDependency(name="requests", version=None)

# Version constraint
RawDependency(name="six", version=">=1.16.0,<2.0")
```

#### Usage in `parse()` method

```python
def parse(self, path: Path) -> List[RawDependency]:
    deps: List[RawDependency] = []
    for line in path.read_text().splitlines():
        name, version = parse_line(line)
        # Use named arguments for clarity
        deps.append(RawDependency(name=name, version=version))
    return deps
```

---

## Collector Base Class

### `strata.builders.sbom.base_sbom_collector.BaseSbomCollector`

Abstract base class for SBOM component collectors.

```python
from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
```

#### Lifecycle

1. **Instantiation** — Collector is created once per `strata build sbom` invocation
2. **Calling** — `collect(platform, work_path, deployment_build_path)` is called once
3. **Warnings retrieval** — `get_warnings()` is called after `collect()` completes
4. **Cleanup** — Instance is discarded

#### Abstract Methods

##### `get_collector_name() -> str`

```python
@abstractmethod
def get_collector_name(self) -> str:
    """Return the short identifier for this collector.
    
    Used in logs and in the source_collector field of components.
    
    Examples: "image", "helm", "terraform", "ansible", "custom_scanner"
    
    Returns:
        str: Unique, short identifier (alphanumeric + underscore)
    """
```

##### `collect(platform, work_path, deployment_build_path) -> List[SbomComponentModel]`

```python
@abstractmethod
def collect(
    self,
    platform: PlatformArtifactModel,
    work_path: Path,
    deployment_build_path: Path,
) -> List[SbomComponentModel]:
    """Extract components from the platform artifact.
    
    Called once per build. Should call _reset_warnings() at the start
    to clear warnings from any previous invocation.
    
    Args:
        platform (PlatformArtifactModel): The assembled platform artifact,
            containing spec.modules[], spec.provisioners[], etc.
        work_path (Path): Workspace root directory (contains .strata/)
        deployment_build_path (Path): Deployment-specific build directory,
            e.g., /workspace/build/prod-1.0.0/
    
    Returns:
        List[SbomComponentModel]: Components found. Empty list if none.
    
    Example:
        def collect(self, platform, work_path, deployment_build_path):
            self._reset_warnings()
            components: List[SbomComponentModel] = []
            
            if not platform.spec or not platform.spec.modules:
                return components
            
            for module in platform.spec.modules:
                for service in module.services:
                    components.append(
                        SbomComponentModel(
                            component_type="container",
                            name=service.name,
                            version=service.version,
                            purl=build_purl(service),
                            source_collector=self.get_collector_name(),
                        )
                    )
            
            return components
    """
```

#### Protected Methods

##### `_reset_warnings() -> None`

```python
def _reset_warnings(self) -> None:
    """Clear the warnings list before collecting.
    
    Should be called at the start of collect() to remove warnings
    from any previous invocation.
    
    Example:
        def collect(self, platform, work_path, deployment_build_path):
            self._reset_warnings()  # Always first
            components: List[SbomComponentModel] = []
            # ... collection logic ...
            return components
    """
```

##### `_warnings: List[str]`

```python
self._warnings: List[str]  # Accumulated warnings from this collect() call
```

Access directly to append warnings:

```python
self._warnings.append("Optional file not found: /path/to/file")
```

#### Public Methods

##### `get_warnings() -> List[str]`

```python
def get_warnings(self) -> List[str]:
    """Return accumulated warnings from the most recent collect() call.
    
    Returns a **copy** of the warnings list (modifications don't affect internal state).
    
    Returns:
        List[str]: List of warning messages
    
    Example:
        components = collector.collect(platform, work_path, build_path)
        for warning in collector.get_warnings():
            logger.warning("Collector warning", msg=warning)
    """
```

---

## Component Model

### `strata.models.sbom_model.SbomComponentModel`

Internal representation of a single SBOM component.

```python
from strata.models.sbom_model import SbomComponentModel
```

#### Fields

```python
class SbomComponentModel:
    component_type: str
    """CycloneDX component type.
    
    Allowed values: "container", "library", "framework"
    
    - container: Docker/container images (e.g., nginx, postgres)
    - library: Application dependencies (e.g., npm packages, Python modules)
    - framework: Infrastructure code (e.g., Terraform modules, Helm charts)
    """

    name: str
    """Component name.
    
    Examples: "nginx", "flask", "hashicorp/aws", "bitnami/nginx"
    Should be human-readable and descriptive.
    """

    version: Optional[str]
    """Version string, version constraint, or None if unpinned.
    
    Examples:
    - "1.25.0" (pinned)
    - "~> 5.0" (constraint)
    - "main" (branch ref)
    - None (unpinned, no version in source)
    """

    purl: str
    """Package URL (purl) per RFC.
    
    Format: pkg:<type>/<namespace>/<name>@<version>
    
    Examples:
    - pkg:docker/library/nginx@1.25.0
    - pkg:npm/express@4.18.2
    - pkg:pypi/flask@2.3.1
    - pkg:terraform/hashicorp/aws@5.0.0
    - pkg:helm/bitnami/nginx@14.0.0
    
    See: https://github.com/package-url/purl-spec
    """

    properties: Dict[str, str]
    """Additional metadata properties.
    
    Used to attach collector-specific information or warnings.
    
    Examples:
    - {"strata:tag-stability": "floating"}
    - {"deprecated": "true"}
    - {"provider_type": "terraform"}
    
    Keys are arbitrary strings; values are always strings.
    """

    source_collector: str
    """Name of the collector that produced this component.
    
    Should match get_collector_name().
    
    Examples: "image", "helm", "terraform", "custom_scanner"
    Used in logs and for debugging.
    """
```

#### Construction

```python
from strata.models.sbom_model import SbomComponentModel

component = SbomComponentModel(
    component_type="container",
    name="nginx",
    version="1.25.0",
    purl="pkg:docker/library/nginx@1.25.0",
    properties={"strata:tag-stability": "floating"},
    source_collector="image",
)
```

#### Types — When to Use Each

| Type | Examples | Used by collector |
|------|----------|-------------------|
| `container` | Docker images, OCI refs | ContainerImageCollector |
| `library` | npm packages, pip modules, Maven jars | DependencyFileCollector |
| `framework` | Terraform modules, Helm charts, Ansible roles | HelmCollector, TerraformCollector |

---

## Plugin Loader & Registry

### `CollectorPluginLoader`

Loads plugins from `.strata/collectors.yaml` and auto-discovers lockfile parsers from `.strata/lockfile_parsers/`.

```python
from strata.builders.sbom.collector_plugin_loader import CollectorPluginLoader
```

#### Methods

##### `load(work_path: Path) -> List[BaseSbomCollector]` (static)

```python
@staticmethod
def load(work_path: Path) -> List[BaseSbomCollector]:
    """Load collector plugins and auto-discover lockfile parsers.
    
    This is called automatically by SbomBuilder. You rarely need to call
    it directly unless testing plugin loading in isolation.
    
    Process:
    1. Reads .strata/collectors.yaml if it exists
    2. Loads collector plugins (type=collector entries)
    3. Imports lockfile_parser plugins (type=lockfile_parser entries)
    4. Auto-discovers .strata/lockfile_parsers/*.py files
    
    Returns:
        List[BaseSbomCollector]: Instantiated extra collector plugins
    
    Raises:
        PlatformError: If collector config is invalid or class cannot load
    
    Example:
        from pathlib import Path
        from strata.builders.sbom.collector_plugin_loader import CollectorPluginLoader
        
        work_path = Path("/workspace")
        extra_collectors = CollectorPluginLoader.load(work_path)
        for collector in extra_collectors:
            print(f"Loaded: {collector.get_collector_name()}")
    """
```

---

### `LockfileParserRegistry`

Registry of lockfile parsers, keyed by filename pattern.

```python
from strata.builders.sbom.lockfile_parsers import DEFAULT_REGISTRY, LockfileParserRegistry
```

#### DEFAULT_REGISTRY

Global registry containing all auto-registered parsers (built-in + custom):

```python
from strata.builders.sbom.lockfile_parsers import DEFAULT_REGISTRY

# Iterate all parsers
for parser in DEFAULT_REGISTRY.all_parsers():
    print(f"{parser.ecosystem}: {parser.filename_patterns()}")

# Find parser for a filename
parser = DEFAULT_REGISTRY.find("package-lock.json")
if parser:
    print(f"Found parser: {parser.ecosystem}")
```

#### Methods

##### `find(filename: str) -> Optional[LockfileParser]`

```python
def find(self, filename: str) -> Optional[LockfileParser]:
    """Find a parser matching the filename.
    
    Matching is case-insensitive and uses fnmatch glob patterns.
    Returns the first parser whose patterns match the filename.
    
    Args:
        filename (str): Filename to match (not full path)
    
    Returns:
        Optional[LockfileParser]: Parser if found, None otherwise
    
    Example:
        parser = DEFAULT_REGISTRY.find("requirements.txt")
        if parser:
            deps = parser.parse(Path("requirements.txt"))
    """
```

##### `all_patterns() -> List[str]`

```python
def all_patterns(self) -> List[str]:
    """Return all registered filename patterns.
    
    Used by DependencyFileCollector to scan the workspace.
    
    Returns:
        List[str]: All glob patterns from all parsers
    """
```

##### `all_parsers() -> List[LockfileParser]`

```python
def all_parsers(self) -> List[LockfileParser]:
    """Return all registered parsers.
    
    Returns:
        List[LockfileParser]: All parsers in registration order
    """
```

---

## Configuration Models

### `.strata/collectors.yaml` Schema

```yaml
collectors:
  - name: <string>              # Unique plugin name (required)
    type: collector|lockfile_parser  # Plugin type (required)
    path: <string>              # File path relative to work_path (mutually exclusive with module)
    module: <string>            # Python module path (mutually exclusive with path)
    class: <string>             # Class name (required for type=collector, optional for lockfile_parser)
```

#### Example

```yaml
collectors:
  # Collector plugin (custom class)
  - name: my-custom-collector
    type: collector
    path: .strata/collectors/my_collector.py
    class: MyCustomCollector

  # Lockfile parser plugin
  - name: cargo-parser
    type: lockfile_parser
    path: .strata/collectors/cargo_parser.py
    # class is optional — __init_subclass__ auto-registers the parser

  # Using installed module (not file path)
  - name: installed-parser
    type: lockfile_parser
    module: my.installed.parsers
```

---

## Common Patterns

### De-duplicate Components by purl

```python
def collect(self, platform, work_path, deployment_build_path):
    self._reset_warnings()
    components: List[SbomComponentModel] = []
    seen_purls: set[str] = set()
    
    for component in extracted_components:
        if component.purl in seen_purls:
            continue
        seen_purls.add(component.purl)
        components.append(component)
    
    return components
```

### Handle Optional Configuration

```python
def collect(self, platform, work_path, deployment_build_path):
    self._reset_warnings()
    components: List[SbomComponentModel] = []
    
    config_path = work_path / "config" / "optional.yaml"
    if not config_path.exists():
        self._warnings.append(f"Optional config not found: {config_path}")
        return components
    
    # ... use config ...
    return components
```

### Add Custom Properties (Warnings/Metadata)

```python
components.append(
    SbomComponentModel(
        component_type="container",
        name="nginx",
        version="latest",  # Floating tag!
        purl="pkg:docker/library/nginx@latest",
        properties={"strata:tag-stability": "floating"},
        source_collector=self.get_collector_name(),
    )
)
self._warnings.append("Floating tag detected: nginx:latest")
```

### Parse with Graceful Fallback

```python
def parse(self, path: Path) -> List[RawDependency]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to parse {path.name}: {exc}") from exc
    
    deps: List[RawDependency] = []
    packages = data.get("packages") or {}
    
    for name, info in packages.items():
        if not isinstance(info, dict):
            continue  # Skip malformed entries, don't raise
        version = info.get("version")
        deps.append(RawDependency(name=str(name), version=version or None))
    
    return deps
```

---

## Error Handling

### ValueError in Parsers (Gracefully Caught)

```python
def parse(self, path: Path) -> List[RawDependency]:
    try:
        data = process_file(path)
    except Exception as exc:
        # ✓ Collector catches and logs this
        raise ValueError(f"Parse failed: {exc}") from exc
    
    # ✗ Collector does NOT catch other exceptions
    # raise RuntimeError("...")  # NOT caught — will crash the build
```

### ValueError in Collectors (NOT Caught)

`collect()` should not raise `ValueError`. If `collect()` raises any exception, the entire build fails. Use `self._warnings.append()` for non-fatal issues instead.

### PlatformError in Plugin Loader

Configuration errors raise `PlatformError` with specific error codes:

```
PlatformError: Collector plugin config error
  error_code: COLLECTOR_PLUGIN_CONFIG_ERROR

PlatformError: Failed to load collector class
  error_code: COLLECTOR_PLUGIN_LOAD_ERROR
```

---

## Platform Artifact Structure

The `platform: PlatformArtifactModel` passed to `collect()` contains:

```python
platform.spec.modules                    # List of module specs
platform.spec.provisioners               # List of provisioner specs
platform.spec.resources                  # Resource references
platform.metadata.sbom                   # Reference to existing SBOM (if any)
```

### Module Structure

```python
module.name                      # Module name
module.services                  # List of service specs
  service.name
  service.image                  # Docker image ref (e.g., "nginx:1.25.0")
  service.version
module.terraform_providers       # List of provider specs (if applicable)
module.helm_releases             # List of helm release specs (if applicable)
```

---

## See Also

- [Extending strata SBOM](./extending-sbom-plugins.md) — User guide with examples
- [SBOM Builder Architecture](../platform/sbom-builder.md) — Internal design
- [Testing SBOM Plugins](../guides/testing-strata-extensions.md) — Test patterns
