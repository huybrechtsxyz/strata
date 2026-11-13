#!/usr/bin/env python3
"""HCL parsing reuses the result for a text parsed before (strata.utils.hcl)."""

from pathlib import Path

import pytest
from lark.exceptions import LarkError

from strata.utils import hcl

_MODULE = 'output "endpoint" {\n  value       = "x"\n  description = "The endpoint."\n}\n'


@pytest.fixture
def counted_parses(monkeypatch):
    """Counts the real parses, on an empty cache that is emptied again afterwards."""
    parses: list[str] = []
    real = hcl.hcl2.loads

    def _loads(text: str, *args, **kwargs):
        parses.append(text)
        return real(text, *args, **kwargs)

    hcl._parse.cache_clear()
    monkeypatch.setattr(hcl.hcl2, "loads", _loads)
    yield parses
    hcl._parse.cache_clear()


def test_the_same_text_is_parsed_once(counted_parses):
    first = hcl.parse_hcl(_MODULE)
    second = hcl.parse_hcl(_MODULE)

    assert first == second
    assert len(counted_parses) == 1


def test_each_caller_gets_its_own_copy(counted_parses):
    first = hcl.parse_hcl(_MODULE)
    first["output"].clear()

    assert hcl.parse_hcl(_MODULE)["output"]


def test_a_text_that_does_not_parse_raises_every_time(counted_parses):
    for _ in range(2):
        with pytest.raises(LarkError):
            hcl.parse_hcl('output "broken" {')

    assert len(counted_parses) == 2


def test_two_files_with_the_same_text_are_parsed_once(counted_parses, tmp_path: Path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "outputs.tf").write_text(_MODULE, encoding="utf-8")

    assert hcl.load_hcl_file(tmp_path / "a" / "outputs.tf") == hcl.load_hcl_file(tmp_path / "b" / "outputs.tf")
    assert len(counted_parses) == 1


def test_an_edited_file_is_parsed_again(counted_parses, tmp_path: Path):
    path = tmp_path / "outputs.tf"
    path.write_text(_MODULE, encoding="utf-8")
    hcl.load_hcl_file(path)

    path.write_text(_MODULE.replace("The endpoint.", "Changed."), encoding="utf-8")

    assert hcl.load_hcl_file(path)["output"][0]['"endpoint"']["description"] == '"Changed."'
    assert len(counted_parses) == 2


def test_a_missing_file_raises_os_error(tmp_path: Path):
    with pytest.raises(OSError):
        hcl.load_hcl_file(tmp_path / "missing.tf")
