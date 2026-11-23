#!/usr/bin/env python3
"""Waiting for another process to let go of a file, before a rename or a removal that needs it.

On Windows a file cannot be deleted, nor a directory renamed or removed, while another process holds a file in it
open without sharing the delete right. A virus scanner opens every file just written for a moment, and under load
(a CI agent, a full parallel test run) that moment is long enough to fail the operation with "Access is denied"
(`PermissionError`). strata waits briefly and tries again, only for that error: any other one is real.

Used where strata renames or removes what it wrote moments ago (docs/design/helm-chart-deployment.md, "Registry
charts"): the chart cache's rename into place, `build run --clean`, and an asset slot cleared before copying.
Cleanups that are best effort (`ignore_errors=True`) do not need it.
"""

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

#: The waits, in seconds, before each new attempt: about 3 seconds in all, longer than a scanner holds a file.
RELEASE_WAITS: tuple[float, ...] = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)


def when_released(action: Callable[[], T], waits: tuple[float, ...] = RELEASE_WAITS) -> T:
    """Run `action` and return its result, retrying it after each of `waits` while it fails with
    `PermissionError`. Any other error, and the last attempt's `PermissionError`, is raised.

    `action` must be safe to run again after a partial failure, as `os.replace` and `shutil.rmtree` (which carries
    on with what is left) are.
    """
    for wait in waits:
        try:
            return action()
        except PermissionError:
            time.sleep(wait)
    return action()
