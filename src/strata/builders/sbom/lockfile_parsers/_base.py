"""Base classes for lockfile parsers."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, NamedTuple, Optional

from strata.builders.sbom.lockfile_parsers._registry import DEFAULT_REGISTRY


class RawDependency(NamedTuple):
    """A (name, version) pair extracted from a dependency manifest.

    ``version`` is ``None`` when the dependency is unpinned or the format
    does not express an exact version.  A purl without ``@version`` is valid
    per the purl spec and means "version unspecified".
    """

    name: str
    version: Optional[str]


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
