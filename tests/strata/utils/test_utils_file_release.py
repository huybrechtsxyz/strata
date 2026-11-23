#!/usr/bin/env python3
"""Tests for `when_released()`: a rename or removal waits for another process to let go of a file."""

import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from strata.utils.file_release import RELEASE_WAITS, when_released


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    return slept


def _failing(times: int, error: OSError, result: str = "done"):
    calls = {"count": 0}

    def action() -> str:
        calls["count"] += 1
        if calls["count"] <= times:
            raise error
        return result

    return action, calls


def test_an_action_that_works_runs_once_and_returns_its_result(sleeps):
    action, calls = _failing(0, PermissionError())
    assert when_released(action) == "done"
    assert (calls["count"], sleeps) == (1, [])


def test_a_held_file_is_waited_for(sleeps):
    action, calls = _failing(3, PermissionError(13, "Access is denied"))
    assert when_released(action) == "done"
    assert (calls["count"], sleeps) == (4, list(RELEASE_WAITS[:3]))


def test_a_file_held_for_good_raises_after_every_wait(sleeps):
    action, calls = _failing(99, PermissionError(13, "Access is denied"))
    with pytest.raises(PermissionError):
        when_released(action)
    assert (calls["count"], sleeps) == (len(RELEASE_WAITS) + 1, list(RELEASE_WAITS))


@pytest.mark.parametrize("error", [FileNotFoundError(2, "gone"), OSError(28, "No space left on device")])
def test_any_other_error_is_raised_at_once(sleeps, error):
    action, calls = _failing(99, error)
    with pytest.raises(type(error)):
        when_released(action)
    assert (calls["count"], sleeps) == (1, [])


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to remove a file another process holds open")
def test_a_tree_is_removed_once_a_scanner_lets_go_of_a_file_in_it(tmp_path: Path):
    """`build run --clean` and an asset slot reset: the previous build's files may still be held."""
    tree = tmp_path / "build"
    (tree / "infra").mkdir(parents=True)
    (tree / "infra" / "main.tf").write_text("#\n")
    handle = open(tree / "infra" / "main.tf", "rb")  # noqa: SIM115 - closed by the timer
    threading.Timer(0.3, handle.close).start()

    when_released(lambda: shutil.rmtree(tree))

    assert not tree.exists()
