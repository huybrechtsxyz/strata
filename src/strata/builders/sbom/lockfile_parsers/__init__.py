"""LockfileParser registry and built-in parser implementations.

Import order is critical — the registry must exist before the ABC,
and the ABC must exist before any concrete parsers:

1. ``_registry`` defines ``LockfileParserRegistry`` and ``DEFAULT_REGISTRY``.
2. ``_base`` defines ``RawDependency`` and the ``LockfileParser`` ABC whose
   ``__init_subclass__`` hook references ``DEFAULT_REGISTRY``.
3. Each ecosystem module imports ``LockfileParser`` → class bodies execute →
   ``__init_subclass__`` fires → instances are added to ``DEFAULT_REGISTRY``.

No explicit ``DEFAULT_REGISTRY.register(...)`` calls are needed anywhere.

Test stubs must use ``register=False`` to avoid polluting the global registry::

    class FakeParser(LockfileParser, register=False):
        ...

To add a new parser:
    1. Create a new file in this package (e.g. ``swift.py``).
    2. Subclass ``LockfileParser`` — implement ``ecosystem``, ``filename_patterns()``, ``parse()``.
    3. Import the new class in this ``__init__.py``.
    That's it — auto-registration handles the rest.
"""

# --- Foundation (must be imported first) ---
from strata.builders.sbom.lockfile_parsers._base import (
    LockfileParser,
    RawDependency,
)
from strata.builders.sbom.lockfile_parsers._registry import (
    DEFAULT_REGISTRY,
    LockfileParserRegistry,
)
from strata.builders.sbom.lockfile_parsers.golang import GoSumParser
from strata.builders.sbom.lockfile_parsers.java import (
    GradleLockParser,
    MavenPomParser,
)
from strata.builders.sbom.lockfile_parsers.nodejs import PackageLockJsonParser
from strata.builders.sbom.lockfile_parsers.nuget import (
    NugetPackagesLockParser,
    PackagesConfigParser,
)
from strata.builders.sbom.lockfile_parsers.php import ComposerLockParser

# --- Built-in parsers (import triggers auto-registration) ---
from strata.builders.sbom.lockfile_parsers.python import (
    PyprojectTomlParser,
    RequirementsTxtParser,
    UvLockParser,
)
from strata.builders.sbom.lockfile_parsers.ruby import GemfileLockParser
from strata.builders.sbom.lockfile_parsers.rust import CargoLockParser

__all__ = [
    # Registry
    "DEFAULT_REGISTRY",
    "LockfileParserRegistry",
    # Base
    "LockfileParser",
    "RawDependency",
    # Python
    "RequirementsTxtParser",
    "PyprojectTomlParser",
    "UvLockParser",
    # Node.js
    "PackageLockJsonParser",
    # Go
    "GoSumParser",
    # .NET / NuGet
    "NugetPackagesLockParser",
    "PackagesConfigParser",
    # Java / JVM
    "MavenPomParser",
    "GradleLockParser",
    # Ruby
    "GemfileLockParser",
    # Rust
    "CargoLockParser",
    # PHP
    "ComposerLockParser",
]
