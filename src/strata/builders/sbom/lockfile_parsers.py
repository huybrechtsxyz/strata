"""LockfileParser registry and built-in parser implementations.

Module load order is intentional — the registry must exist before the ABC,
and the ABC must exist before any concrete parsers:

1. ``LockfileParserRegistry`` is defined.
2. ``DEFAULT_REGISTRY = LockfileParserRegistry()`` is created.
3. ``LockfileParser`` (ABC) is defined — its ``__init_subclass__`` hook
   references ``DEFAULT_REGISTRY`` at module scope.
4. Each built-in parser class body executes → ``__init_subclass__`` fires →
   instance is added to ``DEFAULT_REGISTRY``.

No explicit ``DEFAULT_REGISTRY.register(...)`` calls are needed anywhere.

Test stubs must use ``register=False`` to avoid polluting the global registry::

    class FakeParser(LockfileParser, register=False):
        ...
"""

import fnmatch
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, NamedTuple, Optional


class RawDependency(NamedTuple):
    """A (name, version) pair extracted from a dependency manifest.

    ``version`` is ``None`` when the dependency is unpinned or the format
    does not express an exact version.  A purl without ``@version`` is valid
    per the purl spec and means "version unspecified".
    """

    name: str
    version: Optional[str]


class LockfileParserRegistry:
    """Maps filename glob patterns to ``LockfileParser`` instances.

    Parsers are matched by **filename** (not full path).  Last registered
    parser wins — custom parsers registered after the defaults shadow any
    earlier registration for the same pattern.
    """

    def __init__(self) -> None:
        self._parsers: List["LockfileParser"] = []

    def register(self, parser: "LockfileParser") -> None:
        """Append *parser* to the registry.  Later registrations take precedence."""
        self._parsers.append(parser)

    def find(self, filename: str) -> Optional["LockfileParser"]:
        """Return the last-registered parser whose pattern matches *filename*."""
        for parser in reversed(self._parsers):
            for pattern in parser.filename_patterns():
                if fnmatch.fnmatch(filename, pattern):
                    return parser
        return None

    def all_patterns(self) -> List[str]:
        """Return all registered filename patterns (preserving insertion order, de-duplicated)."""
        seen: set[str] = set()
        result: List[str] = []
        for parser in self._parsers:
            for pattern in parser.filename_patterns():
                if pattern not in seen:
                    seen.add(pattern)
                    result.append(pattern)
        return result

    def copy(self) -> "LockfileParserRegistry":
        """Return a shallow copy — base for custom registries."""
        new = LockfileParserRegistry()
        new._parsers = list(self._parsers)
        return new


# DEFAULT_REGISTRY must be defined BEFORE LockfileParser so that
# __init_subclass__ can reference it at class-body execution time.
DEFAULT_REGISTRY = LockfileParserRegistry()


class LockfileParser(ABC):
    """Parser for one dependency manifest / lockfile format.

    Concrete subclasses auto-register into ``DEFAULT_REGISTRY`` on class
    definition.  Use ``register=False`` to suppress auto-registration
    (abstract subclasses, test stubs, or parsers intended for a custom
    registry only)::

        class MyAbstractParser(LockfileParser, register=False): ...
    """

    def __init_subclass__(cls, register: bool = True, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Skip abstract subclasses (still have unimplemented abstractmethods).
        if register and not getattr(cls, "__abstractmethods__", None):
            DEFAULT_REGISTRY.register(cls())

    @property
    @abstractmethod
    def ecosystem(self) -> str:
        """Purl type identifier.  Examples: ``"pypi"``, ``"npm"``, ``"golang"``."""

    @abstractmethod
    def filename_patterns(self) -> List[str]:
        """Glob patterns matched against the **filename** (not the full path).

        Examples: ``["requirements*.txt"]``, ``["package-lock.json"]``.
        """

    @abstractmethod
    def parse(self, path: Path) -> List[RawDependency]:
        """Extract dependency pairs from *path*.

        Parse failures **must** raise ``ValueError`` — the collector catches
        these, emits a warning, and continues.  Never raise any other exception.
        """


# ---------------------------------------------------------------------------
# Built-in parsers — each class body executes → __init_subclass__ fires →
# instance added to DEFAULT_REGISTRY automatically.
# ---------------------------------------------------------------------------


class RequirementsTxtParser(LockfileParser):
    """Parse ``requirements*.txt`` files (pip format).

    Only strict pin (``==``) produces a versioned ``RawDependency``.
    Loose constraints (``>=``, ``~=``, etc.) produce ``version=None``.
    Skips blank lines, comments (``#``), options (``-r``, ``-e``, ``--``),
    and URL-based installs.
    """

    @property
    def ecosystem(self) -> str:
        return "pypi"

    def filename_patterns(self) -> List[str]:
        return ["requirements*.txt"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for raw_line in lines:
            line = raw_line.strip()
            # Skip blanks, comments, options, editable/URL installs
            if not line or line.startswith(("#", "-", "http")):
                continue
            # Strip inline comments
            line = line.split(" #")[0].split("\t#")[0].strip()
            if not line:
                continue
            # Strict pin
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s,;\[\\]+)", line)
            if m:
                deps.append(RawDependency(name=m.group(1), version=m.group(2)))
                continue
            # Unpinned — extract name only
            m2 = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if m2:
                deps.append(RawDependency(name=m2.group(1), version=None))
        return deps


class PyprojectTomlParser(LockfileParser):
    """Parse ``pyproject.toml`` — reads ``[project.dependencies]`` (PEP 508).

    Only ``==`` pins produce a versioned ``RawDependency``.
    """

    @property
    def ecosystem(self) -> str:
        return "pypi"

    def filename_patterns(self) -> List[str]:
        return ["pyproject.toml"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        raw_deps = (data.get("project") or {}).get("dependencies") or []
        deps: List[RawDependency] = []
        for dep_str in raw_deps:
            if not isinstance(dep_str, str):
                continue
            dep_str = dep_str.strip()
            # Name is everything up to the first version spec, extras marker, or env marker
            name_m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", dep_str)
            if not name_m:
                continue
            name = name_m.group(1)
            pin_m = re.search(r"==([^\s,;\[\\]+)", dep_str)
            version = pin_m.group(1) if pin_m else None
            deps.append(RawDependency(name=name, version=version))
        return deps


class UvLockParser(LockfileParser):
    """Parse ``uv.lock`` — reads ``[[package]]`` TOML entries (uv lockfile format).

    Every entry in a uv lockfile has an exact ``version`` — no version=None.
    """

    @property
    def ecosystem(self) -> str:
        return "pypi"

    def filename_patterns(self) -> List[str]:
        return ["uv.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for pkg in data.get("package") or []:
            name = pkg.get("name")
            if not name:
                continue
            version = pkg.get("version")
            deps.append(RawDependency(name=str(name), version=str(version) if version else None))
        return deps


class PackageLockJsonParser(LockfileParser):
    """Parse ``package-lock.json`` (npm v2/v3 lockfile format).

    Reads the ``packages`` dict.  The root package (empty string key) is
    skipped.  Scoped packages (``@scope/name``) are kept as-is.
    """

    @property
    def ecosystem(self) -> str:
        return "npm"

    def filename_patterns(self) -> List[str]:
        return ["package-lock.json"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        packages = data.get("packages") or {}
        deps: List[RawDependency] = []
        for key, pkg_data in packages.items():
            if not key:  # skip root ""
                continue
            # Key: "node_modules/pkgname" or "node_modules/@scope/pkgname"
            name = key.removeprefix("node_modules/")
            version = pkg_data.get("version") if isinstance(pkg_data, dict) else None
            deps.append(RawDependency(name=name, version=str(version) if version else None))
        return deps


class GoSumParser(LockfileParser):
    """Parse ``go.sum`` — line-by-line ``module version h1:...`` format.

    Skips ``/go.mod`` entries (checksums for module descriptors).
    De-duplicates by module path — first occurrence wins.
    """

    @property
    def ecosystem(self) -> str:
        return "golang"

    def filename_patterns(self) -> List[str]:
        return ["go.sum"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(str(exc)) from exc

        seen: dict[str, str] = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            module_path = parts[0]
            version_str = parts[1]
            # Skip /go.mod checksum entries
            if version_str.endswith("/go.mod"):
                continue
            if module_path not in seen:
                seen[module_path] = version_str

        return [RawDependency(name=mod, version=ver) for mod, ver in seen.items()]


class NugetPackagesLockParser(LockfileParser):
    """Parse ``packages.lock.json`` (NuGet .NET lockfile, v1/v2 format).

    Reads ``dependencies.<framework>.<package>`` entries.  De-duplicates
    across target frameworks — highest version wins to avoid duplication.
    """

    @property
    def ecosystem(self) -> str:
        return "nuget"

    def filename_patterns(self) -> List[str]:
        return ["packages.lock.json"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        # Shape: {"dependencies": {"<framework>": {"<pkg>": {"resolved": "x.y.z", ...}}}}
        seen: dict[str, str | None] = {}
        for framework_deps in (data.get("dependencies") or {}).values():
            if not isinstance(framework_deps, dict):
                continue
            for pkg_name, pkg_info in framework_deps.items():
                if not pkg_name:
                    continue
                resolved = pkg_info.get("resolved") if isinstance(pkg_info, dict) else None
                version = str(resolved) if resolved else None
                # Keep the entry; if already present keep whichever has a version
                if pkg_name not in seen or (version and not seen[pkg_name]):
                    seen[pkg_name] = version

        return [RawDependency(name=n, version=v) for n, v in seen.items()]


class PackagesConfigParser(LockfileParser):
    """Parse ``packages.config`` (legacy NuGet XML format).

    Each ``<package id="..." version="..." />`` element becomes a dependency.
    """

    @property
    def ecosystem(self) -> str:
        return "nuget"

    def filename_patterns(self) -> List[str]:
        return ["packages.config"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            tree = ET.parse(path)
        except (OSError, ET.ParseError) as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for elem in tree.iter("package"):
            pkg_id = elem.get("id")
            if not pkg_id:
                continue
            version = elem.get("version")
            deps.append(RawDependency(name=pkg_id, version=version))
        return deps


class MavenPomParser(LockfileParser):
    """Parse ``pom.xml`` (Apache Maven project descriptor).

    Reads ``<dependencies><dependency>`` elements at the top level.
    Handles the default XML namespace ``http://maven.apache.org/POM/4.0.0``.
    Reports ``groupId:artifactId`` as the package name and ``version`` when
    present (property interpolation is intentionally not resolved).
    """

    @property
    def ecosystem(self) -> str:
        return "maven"

    def filename_patterns(self) -> List[str]:
        return ["pom.xml"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            tree = ET.parse(path)
        except (OSError, ET.ParseError) as exc:
            raise ValueError(str(exc)) from exc

        root = tree.getroot()
        # pom.xml may or may not carry the namespace
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        deps: List[RawDependency] = []
        for dep in root.iter(f"{ns}dependency"):
            group_id = dep.findtext(f"{ns}groupId") or ""
            artifact_id = dep.findtext(f"{ns}artifactId") or ""
            if not artifact_id:
                continue
            name = f"{group_id}:{artifact_id}" if group_id else artifact_id
            version_text = dep.findtext(f"{ns}version")
            # Skip property references like ${project.version}
            version = version_text if version_text and not version_text.startswith("$") else None
            deps.append(RawDependency(name=name, version=version))
        return deps


class GradleLockParser(LockfileParser):
    """Parse ``gradle.lockfile`` (Gradle dependency locking output).

    Format: ``group:artifact:version=<configurations...>``  (one per line).
    Lines starting with ``#`` or ``empty=`` are skipped.
    """

    @property
    def ecosystem(self) -> str:
        return "maven"

    def filename_patterns(self) -> List[str]:
        return ["gradle.lockfile"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("empty="):
                continue
            # format: group:artifact:version=config1,config2
            coord, _, _ = line.partition("=")
            parts = coord.split(":")
            if len(parts) < 3:
                continue
            group, artifact, version = parts[0], parts[1], parts[2]
            name = f"{group}:{artifact}" if group else artifact
            deps.append(RawDependency(name=name, version=version or None))
        return deps


class GemfileLockParser(LockfileParser):
    """Parse ``Gemfile.lock`` (Bundler / Ruby lockfile).

    Reads the ``GEM`` and ``GIT`` sections.  Each ``    <name> (<version>)``
    line inside a ``specs:`` block becomes a dependency.
    """

    @property
    def ecosystem(self) -> str:
        return "gem"

    def filename_patterns(self) -> List[str]:
        return ["Gemfile.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        in_specs = False
        gem_sections = {"GEM", "GIT", "PATH"}
        current_section: str | None = None

        for line in lines:
            stripped = line.rstrip()
            # Detect section headers (no leading spaces)
            if stripped and not stripped[0].isspace():
                current_section = stripped.split()[0].rstrip(":")
                in_specs = False
                continue

            if current_section in gem_sections:
                if stripped.strip() == "specs:":
                    in_specs = True
                    continue
                if in_specs:
                    # Spec lines: "    name (version)" — exactly 4 spaces of indent
                    m = re.match(r"^    ([^\s(]+) \(([^)]+)\)$", stripped)
                    if m:
                        deps.append(RawDependency(name=m.group(1), version=m.group(2)))
                    elif stripped and not stripped[0] == " ":
                        in_specs = False

        return deps


class CargoLockParser(LockfileParser):
    """Parse ``Cargo.lock`` (Rust/Cargo lockfile, v3 TOML format).

    Reads ``[[package]]`` entries.  Each entry has ``name`` and ``version``.
    """

    @property
    def ecosystem(self) -> str:
        return "cargo"

    def filename_patterns(self) -> List[str]:
        return ["Cargo.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for pkg in data.get("package") or []:
            name = pkg.get("name")
            if not name:
                continue
            version = pkg.get("version")
            deps.append(RawDependency(name=str(name), version=str(version) if version else None))
        return deps


class ComposerLockParser(LockfileParser):
    """Parse ``composer.lock`` (PHP Composer lockfile).

    Reads ``packages`` and ``packages-dev`` arrays.  Each entry has ``name``
    and ``version`` fields.
    """

    @property
    def ecosystem(self) -> str:
        return "packagist"

    def filename_patterns(self) -> List[str]:
        return ["composer.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for section in ("packages", "packages-dev"):
            for pkg in data.get(section) or []:
                name = pkg.get("name")
                if not name:
                    continue
                version = pkg.get("version")
                deps.append(RawDependency(name=str(name), version=str(version) if version else None))
        return deps
