# Extended SBOM sources and platform inventory

- Status: completed
- Date: 2026-06-17
- Issue: #122 (related)

## Context and Problem Statement

strata currently generates a CycloneDX 1.6 SBOM from four sources: container images,
Helm charts, Terraform providers, and Ansible collections.  Two gaps exist.

**Coverage gaps:**

1. **Terraform modules** — `required_providers` are collected but `module {}` blocks
   are not.  Registry modules (e.g. `registry.terraform.io/Azure/compute/azurerm`) are
   third-party code running against live infrastructure and belong in the SBOM.
2. **Docker Compose services** — `ComposeDeployer` is a first-class strata deployer:
   deployments using it are fully declared in the strata deployment YAML.  Yet the
   images in those deployments' `docker-compose.yml` files are invisible to the SBOM
   pipeline because no collector reads them.  This is an internal consistency gap —
   strata manages the deployment but doesn't capture its images.
3. **Application dependencies** — if workspace repos contain application code
   (`requirements.txt`, `pyproject.toml`, `package-lock.json`, `go.sum`), their
   packages are not captured.  The SBOM spans IaC but not the apps being deployed.

**Usability gap:**

The SBOM output is CycloneDX JSON — authoritative for scanners but not useful as a
first-look tool.  New team members exploring an existing strata workspace have no
single-command way to understand what the platform is built from.  `strata guide` tells
you what to *do* next but not what the platform currently *contains*.

## Considered Options

### Option A — Close IaC gaps only (Terraform modules + Compose)

Add two new collectors; leave application dependencies out of scope.

- **Pro:** Stays strictly in strata's IaC/infrastructure domain
- **Pro:** Minimal scope — two well-defined collectors
- **Con:** SBOM still cannot answer "what Python packages does this deployment depend on?"

### Option B — Close IaC gaps + application dependency scanning

Add Terraform module and Compose collectors plus a generic
`DependencyFileCollector` that scans configured source paths for standard
dependency manifests (`requirements.txt`, `pyproject.toml`, `package-lock.json`,
`go.sum`, etc.).

- **Pro:** Full-stack SBOM spanning IaC and application layers
- **Pro:** No new mandatory dependencies — parsing is file-based
- **Con:** Deliberately broadens strata's SBOM scope from "infrastructure" to
  "full stack" — a meaningful design commitment
- **Con:** Dependency file formats vary widely; a generic scanner will miss edge cases

### Option C — IaC gaps + inventory output mode + guide integration (recommended)

Same as Option B, but also adds a human-readable `--report inventory` output mode
to `strata build sbom` and wires the SBOM into `strata guide` so the inventory
becomes a natural onboarding step.

- **Pro:** Addresses coverage gaps and the usability gap simultaneously
- **Pro:** The inventory output doubles as onboarding material for new engineers
- **Pro:** `strata guide` already has a phase-based checklist — adding an SBOM phase
  is low-friction
- **Con:** `--report` flag is a new surface on `build sbom` that needs careful naming
  to avoid conflicting with the existing `--output` flag

## Decision Outcome

Chosen: **Option C**, because the inventory output is what makes the extended SBOM
genuinely useful for onboarding — without it, adding more collectors just produces
a larger JSON file.  The guide integration ensures new team members discover it
naturally rather than needing to know the command exists.

### Consequences

- Good: SBOM covers the full IaC surface (providers + modules + images + charts +
  collections + Compose + app deps)
- Good: Closes an internal consistency gap — deployments using `ComposeDeployer` or
  Terraform module blocks are fully declared in strata's standard YAML files and
  managed by strata, but their components were previously absent from the SBOM;
  the SBOM now reflects what strata actually manages, not just a subset of it
- Good: `strata build sbom --report inventory` gives every engineer a readable
  platform overview in one command
- Good: `strata guide` gains a Phase 8 that surfaces the inventory after setup is
  complete
- Bad: Application dependency scanning is best-effort — strata cannot verify that
  a `requirements.txt` in a repo corresponds to a deployed service
- Bad: The `LockfileParser` implementations need updating when lockfile schemas
  change, but each parser is isolated — a format change only affects one class

---

## Detailed Design

### New Collectors

#### `TerraformModuleCollector`

Scans `*.tf` files in the deployment build path for `module {}` blocks and extracts
the `source` attribute.  Supports:

- **Registry modules:** `source = "registry.terraform.io/hashicorp/consul/aws"` →
  purl `pkg:terraform/hashicorp/consul@{version}?repository_url=registry.terraform.io`
  Version is extracted from the `version` constraint attribute of the module block.
  If `version` is absent (no pinning), the purl is emitted without `@version`.
  If `version` is a range (e.g. `~> 6.0`), the constraint string is stored as-is.
- **GitHub modules:** `source = "github.com/org/module"` →
  purl `pkg:github/org/module@{ref}` (ref from `?ref=` query or absent)
- **Local modules:** `source = "./modules/vpc"` → skipped (local path, no purl)

```python
class TerraformModuleCollector(BaseSbomCollector):
    """Collect Terraform module components from module{} blocks in *.tf files.

    Runs after TerraformProviderCollector in the default collector list.
    Skips local modules (source starts with ./ or ../).
    Deduplicates by source string; first occurrence wins.
    """
    def get_collector_name(self) -> str:
        return "terraform-module"
```

Implementation uses `python-hcl2` (already a dependency via
`TerraformProviderCollector`) — no new dependencies.

#### `ComposeImageCollector`

Reads `docker-compose.yml` (and `docker-compose.yaml`) files from the deployment
build path.  The compose files arrive there because `ComposeDeployer` stages them
during `strata build run` — they are already present as build output for any
deployment that uses the Compose deployer.  Extracts `services.<name>.image` entries.
Deduplicates by purl (same logic as `ContainerImageCollector`).

```python
class ComposeImageCollector(BaseSbomCollector):
    """Collect container image components from docker-compose.yml service definitions.

    Scans the deployment build path recursively for docker-compose.yml and
    docker-compose.yaml files.  Images without an explicit tag get a
    strata:tag-stability=floating property (same as ContainerImageCollector).
    """
    def get_collector_name(self) -> str:
        return "compose"
```

No new dependencies — `PyYAML` is already available.

#### `DependencyFileCollector` + `LockfileParser` Registry

Rather than hardcoding all format logic inside `DependencyFileCollector`, each
supported format is a small, independent `LockfileParser` class registered in a
shared `LockfileParserRegistry`.  Adding a new format, fixing a format version
change, or replacing a parser touches only that one class — not the collector.

##### `LockfileParser` — abstract base with auto-registration

Module load order in `lockfile_parsers.py`:

1. `LockfileParserRegistry` class is defined.
2. `DEFAULT_REGISTRY = LockfileParserRegistry()` is created.
3. `LockfileParser(ABC)` is defined — its `__init_subclass__` hook references `DEFAULT_REGISTRY` at module scope.
4. Each built-in parser class body is executed → `__init_subclass__` fires → instance auto-registered.

No explicit `DEFAULT_REGISTRY.register(...)` calls are needed anywhere.

```python
class LockfileParserRegistry:
    """Maps filename patterns to LockfileParser instances.

    Parsers are matched by filename (not full path).  Last registered parser
    wins — custom parsers registered after the defaults shadow any earlier
    registration for the same pattern.
    """

    def register(self, parser: "LockfileParser") -> None:
        """Append *parser* to the registry.  Later registrations take precedence."""

    def find(self, filename: str) -> Optional["LockfileParser"]:
        """Return the last-registered parser whose pattern matches *filename*."""

    def all_patterns(self) -> List[str]:
        """Return all registered filename patterns (for recursive glob scanning)."""

    def copy(self) -> "LockfileParserRegistry":
        """Return a shallow copy — base for custom registries."""


# Must be defined BEFORE LockfileParser so __init_subclass__ can reference it.
DEFAULT_REGISTRY = LockfileParserRegistry()


class LockfileParser(ABC):
    """Parser for one dependency manifest / lockfile format.

    Concrete subclasses auto-register into DEFAULT_REGISTRY on class definition.
    Use `register=False` to suppress auto-registration (abstract subclasses,
    test stubs, or parsers intended for a custom registry only):

        class MyAbstractParser(LockfileParser, register=False): ...
    """

    def __init_subclass__(cls, register: bool = True, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Skip abstract subclasses (still have unimplemented abstractmethods)
        if register and not getattr(cls, "__abstractmethods__", None):
            DEFAULT_REGISTRY.register(cls())

    @property
    @abstractmethod
    def ecosystem(self) -> str:
        """Purl ecosystem identifier.  Examples: "pypi", "npm", "golang", "cargo""""

    @abstractmethod
    def filename_patterns(self) -> List[str]:
        """Glob patterns this parser handles.
        Examples: ["requirements*.txt"], ["package-lock.json"], ["go.sum"]
        """

    @abstractmethod
    def parse(self, path: Path) -> List[RawDependency]:
        """Extract (name, version_or_None) pairs from *path*.

        Parse failures must raise ValueError — the registry catches these,
        emits a warning, and moves on.  Must never raise any other exception.
        """


# Each class body executes → __init_subclass__ fires → instance added to DEFAULT_REGISTRY
class RequirementsTxtParser(LockfileParser): ...
class PyprojectTomlParser(LockfileParser): ...
class UvLockParser(LockfileParser): ...
class PackageLockJsonParser(LockfileParser): ...
class GoSumParser(LockfileParser): ...
```

`RawDependency` is a `NamedTuple(name: str, version: Optional[str])`.  Purl
construction is handled centrally in `DependencyFileCollector`, not in individual
parsers.  When `version` is None (unpinned dependency), the purl is emitted without
the `@version` suffix (e.g. `pkg:pypi/flask`) — this is valid per the purl spec and
means "version unspecified".

##### Built-in parsers (Phase 3)

| Class                   | File pattern        | Ecosystem | Parse strategy                                                     |
| ----------------------- | ------------------- | --------- | ------------------------------------------------------------------ |
| `RequirementsTxtParser` | `requirements*.txt` | `pypi`    | Line-by-line `name==version`; skips comments and editable installs |
| `PyprojectTomlParser`   | `pyproject.toml`    | `pypi`    | `tomllib` — reads `[project.dependencies]` PEP 508 strings         |
| `UvLockParser`          | `uv.lock`           | `pypi`    | TOML — reads `[[package]]` entries (name + version fields)         |
| `PackageLockJsonParser` | `package-lock.json` | `npm`     | `json.load` — reads `packages` dict (npm v2/v3 lockfile)           |
| `GoSumParser`           | `go.sum`            | `golang`  | Line-by-line `module version/go.mod` — deduplicates by module path |

##### `DependencyFileCollector`

`DependencyFileCollector` is now thin — it walks paths, dispatches to parsers via the
registry, and converts `RawDependency` entries to `SbomComponentModel`:

```python
class DependencyFileCollector(BaseSbomCollector):
    """Collect application dependency components using the LockfileParserRegistry.

    Accepts an injectable registry for testing and custom parser extension.
    Defaults to DEFAULT_REGISTRY (all built-in parsers).
    """

    def __init__(self, registry: Optional[LockfileParserRegistry] = None) -> None:
        super().__init__()
        self._registry = registry or DEFAULT_REGISTRY

    def get_collector_name(self) -> str:
        return "deps"
```

##### Why parsers auto-register but collectors don't

Parsers are **unordered** — dispatch is by filename match, not position.  Auto-
registration via `__init_subclass__` works naturally because insertion order doesn't
matter to the caller.

Collectors are **ordered** — they run in a specific sequence (images before modules,
modules before deps) because later collectors may deduplicate against earlier results.
Auto-registration would remove ordering control, so collectors are explicitly listed
in `_default_collectors()`.

##### Test isolation

Because `__init_subclass__` fires at class-definition time into the module-level
`DEFAULT_REGISTRY`, **test parsers MUST use `register=False`** to avoid polluting
the global registry across test runs:

```python
class FakeParser(LockfileParser, register=False):
    ...
```

Alternatively, tests can construct a fresh `LockfileParserRegistry()` and inject it
via `DependencyFileCollector(registry=fresh_registry)`.

##### Extending with a custom parser

Teams can register additional parsers without forking strata.  The injectable
`registry` parameter on `DependencyFileCollector` is the extension point.

All parsers use only stdlib (`tomllib`, `json`, line iteration) — no new
mandatory dependencies.  `tomllib` requires Python 3.11+, which is already
strata's minimum version.

### Workspace-local Collector Plugins

Users can register their own collectors and lockfile parsers from within their
workspace — without forking strata — using the same loading mechanism as
`IntegrationFactory`.

#### `.strata/collectors.yaml`

```yaml
# .strata/collectors.yaml  (optional)
collectors:
  # A full BaseSbomCollector subclass — appended after the built-in collectors
  - name: cargo
    path: .strata/plugins/cargo_collector.py   # path relative to work_path
    class: CargoCollector
    type: collector

  # A LockfileParser subclass — registered into DependencyFileCollector's registry
  - name: gemfile
    path: .strata/plugins/gemfile_parser.py
    class: GemfileLockParser
    type: lockfile_parser
```

Two plugin types:

| `type`            | Base class          | Effect                                                                         |
| ----------------- | ------------------- | ------------------------------------------------------------------------------ |
| `collector`       | `BaseSbomCollector` | Appended to the builder's collector list after all built-ins                   |
| `lockfile_parser` | `LockfileParser`    | Registered into the `LockfileParserRegistry` used by `DependencyFileCollector` |

#### `CollectorPluginLoader`

Mirrors `IntegrationFactory`'s load pattern — `importlib.util.spec_from_file_location`
for workspace-local files, `importlib.import_module` for installed packages:

```python
class CollectorPluginLoader:
    """Load collector plugins declared in .strata/collectors.yaml.

    Follows the same pattern as IntegrationFactory: importlib-based class
    loading, structured errors, debug logging.
    """

    @staticmethod
    def load(
        work_path: Path,
        lockfile_registry: LockfileParserRegistry,
    ) -> List[BaseSbomCollector]:
        """Load .strata/collectors.yaml and return extra collectors.

        Side effect: registers any lockfile_parser entries into
        *lockfile_registry* before returning.

        Returns:
            List of instantiated BaseSbomCollector plugins (type=collector
            entries only).  Empty list when no config file exists or no
            collector-type entries are declared.
        """

    @staticmethod
    def _load_class(path: Optional[str], module_path: Optional[str], class_name: str) -> type:
        """Load a class from a file path or dotted module path.

        Raises PlatformError with a clear message if the file is missing,
        the module cannot be imported, or the class is not found.
        """
```

#### Plugin loading lifecycle

`SbomBuilder.before_build()` calls `CollectorPluginLoader.load()` after standard
validation and before the collector list is finalised:

```
before_build():
  1. Validate deployment service is loaded
  2. Check platform.json exists (unless dry-run)
  3. Load .strata/collectors.yaml via CollectorPluginLoader:
       - For type=lockfile_parser: import the module — __init_subclass__ auto-registers
         the class into DEFAULT_REGISTRY.  No explicit register() call needed.
       - For type=collector: import the module, instantiate the class, collect as extra_collectors.
  4. Append extra_collectors to self._collectors
```

Because `LockfileParser.__init_subclass__` fires on class body execution, importing
a plugin module is sufficient to register all parsers it defines.  `CollectorPluginLoader`
does not need to know which class name was the parser — just loading the module is enough.

Plugins are loaded once per `build()` call.  If `.strata/collectors.yaml` does not
exist, the step is skipped silently.

#### Writing a workspace plugin

A plugin file only needs to implement the correct base class and be importable:

```python
# .strata/plugins/cargo_collector.py
import tomllib
from pathlib import Path
from typing import List
from strata.builders.sbom.lockfile_parsers import LockfileParser, RawDependency

# Defining this class is all that's needed.  __init_subclass__ fires on class body
# execution and calls DEFAULT_REGISTRY.register(CargoLockParser()) automatically.
class CargoLockParser(LockfileParser):
    @property
    def ecosystem(self) -> str:
        return "cargo"

    def filename_patterns(self) -> List[str]:
        return ["Cargo.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return [
            RawDependency(name=pkg["name"], version=pkg.get("version"))
            for pkg in data.get("package", [])
        ]
```

Declared in `.strata/collectors.yaml` — only `path` is needed for `lockfile_parser`
type; `class` is optional (the module is imported for its side effects):

```yaml
collectors:
  - name: cargo
    path: .strata/plugins/cargo_collector.py
    type: lockfile_parser   # class not required — __init_subclass__ auto-registers
```

No installation, no packaging, no changes to strata required.

### Updated Default Collector Order

The full list after all three phases are complete:

```python
def _default_collectors() -> List[BaseSbomCollector]:
    return [
        ContainerImageCollector(),       # platform.spec.modules[].services[].image
        ComposeImageCollector(),          # docker-compose.yml service images   [Phase 1]
        HelmChartCollector(),             # Helm provisioner chart_name/version
        TerraformProviderCollector(),     # required_providers blocks in *.tf
        TerraformModuleCollector(),       # module{} blocks in *.tf             [Phase 1]
        AnsibleCollectionCollector(),     # requirements.yml collections + roles
        DependencyFileCollector(),        # requirements.txt, package-lock, etc [Phase 3]
    ]
```

Phase 1 adds only `ComposeImageCollector` and `TerraformModuleCollector`.
`DependencyFileCollector` is added in Phase 3 when the registry lands.

### `strata build sbom` — New Flags

The existing `--output` flag (`console|text|json`) is the standard strata output
format selector and must not be overloaded.  A new `--report` flag selects the SBOM
report style:

```
strata build sbom [OPTIONS]

Options (existing):
  --file PATH            Deployment YAML  (default: auto-detect)
  --work-path PATH       Workspace root
  --output FORMAT        console | text | json  (standard strata flag)
  --verbose / --quiet

Options (new):
  --report MODE          cyclonedx | inventory  (default: cyclonedx)
  --output-file PATH     Write SBOM/inventory to this path instead of the
                         default build directory location.
                         For cyclonedx: defaults to {build_path}/sbom.json
                         For inventory: defaults to stdout (console output)
```

### Inventory Output Format

`--report inventory` renders a human-readable grouped listing.  It does NOT write
`sbom.json` — it uses the same collectors to gather data and prints to stdout
(or `--output-file` if specified).

```
$ strata build sbom --report inventory

Platform Inventory — xyz-production  (built 2026-06-17T08:12:00Z)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Container Images (3)
  nginx                  1.27.0        registry.hub.docker.com
  app-api                2.4.1         ghcr.io/org/app-api
  postgres               16.2          registry.hub.docker.com

Compose Services (2)
  worker                 3.1.0         ghcr.io/org/worker
  redis                  7.2           registry.hub.docker.com       ⚠ floating

Helm Charts (1)
  traefik                28.3.0        https://helm.traefik.io/traefik

Terraform Providers (2)
  azurerm                4.12.0        registry.terraform.io/hashicorp
  azuread                3.1.0         registry.terraform.io/hashicorp

Terraform Modules (3)
  compute/azurerm        6.0.0         registry.terraform.io/Azure
  aks/azurerm            9.2.1         registry.terraform.io/Azure
  consul/aws             0.11.0        registry.terraform.io/hashicorp

Ansible Collections (2)
  community.general      9.4.0         https://galaxy.ansible.com
  ansible.posix          1.6.2         https://galaxy.ansible.com

Python Dependencies (14)
  click                  8.1.8         pypi.org
  pydantic               2.11.5        pypi.org
  structlog              24.4.0        pypi.org
  ...

Total: 27 components  |  ⚠ 1 floating tag
```

`⚠ floating` is shown for any component where
`strata:tag-stability=floating` is set (same detection as `ContainerImageCollector`).

### `strata guide` — Phase 8

The guide checklist currently has 7 phases ending at "Build artifact exists".  A new
Phase 8 is added after a successful build:

```
Phase 8 — Platform inventory generated
  Condition: {build_path}/{deployment}/sbom.json exists
  Status ok:      sbom.json present, component_count > 0
  Status warn:    sbom.json present, component_count == 0
  Status pending: sbom.json missing (build succeeded but sbom step skipped)
```

When Phase 8 is the first non-ok phase, the guide's next-step hint reads:

```
Next step: Generate the platform inventory

  strata build sbom -f {file}

  Or for a human-readable overview:

  strata build sbom -f {file} --report inventory

  See: docs/platform/builders.md
```

This means every new engineer who runs `strata guide` after setup is pointed toward
the inventory command naturally.

### Architecture Changes

```
builders/
  sbom/
    base_sbom_collector.py        — unchanged
    image_collector.py            — unchanged
    helm_collector.py             — unchanged
    terraform_collector.py        — unchanged (providers only)
    terraform_module_collector.py  — NEW: module{} blocks
    compose_collector.py           — NEW: docker-compose.yml images
    ansible_collector.py          — unchanged
    lockfile_parsers.py            — NEW: LockfileParser ABC, LockfileParserRegistry,
                                          DEFAULT_REGISTRY, all built-in parsers
    deps_collector.py              — NEW: DependencyFileCollector (thin — dispatches
                                          to LockfileParserRegistry)
  sbom_builder.py                — updated default collector list + inventory renderer

commands/
  builders/
    sbom_build_command.py        — add --report and --output-file handling
  cli_builders.py                — expose --report and --output-file flags
  guide/
    show_guide_command.py        — add Phase 8 checklist item + next-step hint
```

`SbomBuildCommand` routes on `--report`:
- `cyclonedx` → existing `SbomBuilder.build()` path (writes `sbom.json`)
- `inventory` → new `SbomBuilder.render_inventory()` method (returns formatted string)

The inventory renderer lives in `SbomBuilder` (not a separate command) because it
re-uses the exact same collector results — no second collection pass.

### `DependencyFileCollector` — Scope Control

Application source repos can have thousands of files.  The collector must not scan
the entire filesystem.  Scanning is scoped to:

1. Paths registered in the solution's `spec.repositories` (resolved at build time)
2. Explicit ignore patterns from `.strata/sbom-ignore.yaml`:

```yaml
# .strata/sbom-ignore.yaml  (optional)
ignore_paths:
  - "**/node_modules"
  - "**/.venv"
  - "**/dist"
  - "docs/**"
ignore_files:
  - "requirements-dev.txt"
  - "requirements-test.txt"
```

If `spec.repositories` is empty (no repos registered), the collector walks
`work_path` with the default ignore patterns applied.

---

## Implementation Phases

### Phase 1 — IaC gap closure + collector plugin loader

- `TerraformModuleCollector` (no new deps)
- `ComposeImageCollector` (no new deps)
- Updated default collector list (these two collectors only)
- `CollectorPluginLoader` with `type: collector` support only (importlib + append)
- `.strata/collectors.yaml` schema (parsed but only `collector` type is functional)
- Full test coverage for both collectors + plugin loader

### Phase 2 — Inventory output + guide integration

- `--report inventory` flag on `strata build sbom`
- `--output-file PATH` flag
- `SbomBuilder.render_inventory()` renderer
- `strata guide` Phase 8 checklist + hint
- Inventory output in `strata build run` verbose mode (optional, after build)

### Phase 3 — Application dependency scanning + lockfile plugin type

- `LockfileParser` ABC + `LockfileParserRegistry` + `DEFAULT_REGISTRY`
- Built-in parsers: `RequirementsTxtParser`, `PyprojectTomlParser`, `UvLockParser`,
  `PackageLockJsonParser`, `GoSumParser`
- `DependencyFileCollector` (thin dispatcher — uses registry)
- `DependencyFileCollector` added to default collector list
- `CollectorPluginLoader` gains `type: lockfile_parser` support (import-only, auto-registers)
- `.strata/sbom-ignore.yaml` support
- Scoped to `spec.repositories` paths
- Extension point documented: injectable `registry` parameter

---

## Risks and Mitigations

| Risk                                                               | Impact                                        | Mitigation                                                                                                                               |
| ------------------------------------------------------------------ | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| HCL2 module source formats vary (git, GitHub, bitbucket, local)    | Unknown sources → skipped silently            | Log as warning; local-path modules are intentionally excluded                                                                            |
| `docker-compose.yml` is not always in build output                 | Collector returns empty silently              | Check for file existence before scanning; no error                                                                                       |
| `DependencyFileCollector` scans slow on large repos                | Build time increases                          | Scope to `spec.repositories`; add `--no-deps` flag to opt out                                                                            |
| Lockfile format changes (e.g. uv TOML schema bump)                 | Parse failures in one parser                  | Each parser is isolated — only that parser needs updating; `DEFAULT_REGISTRY` can be replaced without changing `DependencyFileCollector` |
| Unknown lockfile format in a workspace repo                        | File silently ignored                         | `all_patterns()` on the registry shows exactly what is scanned; custom parsers can be registered via the injectable `registry` parameter |
| Inventory output is informal — not suitable for compliance tooling | Confusion about which format is authoritative | Document clearly: `cyclonedx` = supply chain/compliance; `inventory` = human overview                                                    |

---

## Related

- [ADR 0008 — Infrastructure drift detection](0008-infrastructure-drift-detection.md)
- [Builders reference](../platform/builders.md) — `SbomBuilder` and collector interface
- [Commands reference](../platform/commands.md) — `build sbom`
- [How deployments work](../guides/how-deployments-work.md)
