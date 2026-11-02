"""Strata — infrastructure as code platform."""

from typing import Any

__all__ = ["__version__"]


def __getattr__(name: str) -> Any:
    """Resolve `__version__` on first access (PEP 562).

    Derived, never declared: a literal here would be a second source of truth
    alongside `VERSION.txt`. Resolved lazily because determining it scans the
    environment's installed distributions, and `import strata` should not pay
    that cost just to reach a model.
    """
    if name == "__version__":
        from strata.utils.version import get_version

        return get_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
