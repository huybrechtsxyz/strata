import pathlib
from typing import Generator

import pytest


def get_test_dir() -> str:
    return str(pathlib.Path(__file__).parent)


@pytest.fixture(scope="session", autouse=True)
def init() -> Generator[None, None, None]:
    with pytest.MonkeyPatch().context() as mp:
        mp.chdir(get_test_dir())
        yield
