#!/usr/bin/env python3
"""Context bound to every log entry in the current task or thread.

`run_id` is a single correlation key threaded through every log line for the
current execution (a validate/build/deploy run), so it gets a named binding
rather than being one more keyword argument a call site can forget to pass.

Wraps `structlog.contextvars` so no other layer needs to import structlog
directly just to bind/read context.
"""

from types import TracebackType
from typing import Any

import structlog.contextvars

RUN_ID_KEY = "run_id"


def bind_run(run_id: str) -> None:
    """Bind `run_id` to every subsequent log entry in this context."""
    structlog.contextvars.bind_contextvars(**{RUN_ID_KEY: run_id})


def get_run_id() -> str | None:
    """Return the `run_id` bound to the current context, if any."""
    value = structlog.contextvars.get_contextvars().get(RUN_ID_KEY)
    return value if isinstance(value, str) else None


def get_context() -> dict[str, Any]:
    """Return everything currently bound to the logging context."""
    return dict(structlog.contextvars.get_contextvars())


def clear_context() -> None:
    """Remove every bound value, including `run_id`."""
    structlog.contextvars.clear_contextvars()


class LogContext:
    """Bind fields for the duration of a block and restore on exit.

    Restores shadowed values rather than deleting the keys, so nesting is
    safe — an inner block that rebinds a key does not leave the outer block
    without one::

        with LogContext(workspace="acme-prod", stage="plan"):
            log.info("provisioning started")
    """

    def __init__(self, **values: Any) -> None:
        self._values = values
        self._tokens: list[Any] = []

    def __enter__(self) -> "LogContext":
        self._tokens = [structlog.contextvars.bind_contextvars(**self._values)]
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for token in self._tokens:
            structlog.contextvars.reset_contextvars(**token)
        self._tokens = []
