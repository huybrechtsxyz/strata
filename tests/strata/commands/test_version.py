import pathlib

import pytest

import strata
from strata.utils.version import get_version

# Navigate from tests/strata/commands/ up to the project root.
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent


@pytest.fixture
def version_from_file() -> str:
    return (_PROJECT_ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()


def test_version_function(version_from_file: str) -> None:
    assert get_version() == version_from_file


def test_version(version_from_file: str) -> None:
    assert strata.__version__ == version_from_file
