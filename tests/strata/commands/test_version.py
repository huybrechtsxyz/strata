import pytest

import strata
from strata.utils.version import get_version


@pytest.fixture
def version_from_file() -> str:
    with open("../../VERSION.txt") as f:
        return f.read()


def test_version_function(version_from_file: str) -> None:
    assert get_version() == version_from_file


def test_version(version_from_file: str) -> None:
    assert strata.__version__ == version_from_file
