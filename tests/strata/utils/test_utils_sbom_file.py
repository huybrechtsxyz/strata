"""`strata.utils.sbom_file`: the one loader every `sbom.json` reader shares."""

from pathlib import Path

import pytest

from strata.utils.sbom_file import SBOM_FILENAME, component_properties, parse_sbom, read_sbom


def test_no_sbom_is_none(tmp_path):
    assert read_sbom(tmp_path) is None


def test_an_sbom_without_components_has_an_empty_list(tmp_path):
    # CycloneDX leaves `components` out when there are none: that is an empty SBOM, not a broken one.
    (tmp_path / SBOM_FILENAME).write_text('{"bomFormat": "CycloneDX"}', encoding="utf-8")
    assert read_sbom(tmp_path) == {"bomFormat": "CycloneDX", "components": []}


def test_a_valid_sbom_is_returned_whole(tmp_path):
    (tmp_path / SBOM_FILENAME).write_text('{"components": [], "metadata": {"timestamp": "t"}}', encoding="utf-8")
    assert read_sbom(tmp_path) == {"components": [], "metadata": {"timestamp": "t"}}


@pytest.mark.parametrize(
    ("text", "message"),
    [("{not json", "not valid JSON"), ("[]", "no components list"), ('{"components": {}}', "no components list")],
)
def test_an_unusable_sbom_is_a_value_error(tmp_path, text, message):
    (tmp_path / SBOM_FILENAME).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_sbom(tmp_path)


def test_a_file_that_cannot_be_read_is_a_value_error(tmp_path, monkeypatch):
    # A scanner holding the file on Windows, or no permission: the reader decides what that means, it never crashes.
    (tmp_path / SBOM_FILENAME).write_text("{}", encoding="utf-8")

    def locked(*_args, **_kwargs):
        raise PermissionError("locked by a scanner")

    monkeypatch.setattr(Path, "read_text", locked)
    with pytest.raises(ValueError, match="cannot be read"):
        read_sbom(tmp_path)


def test_parse_sbom_takes_bytes_and_names_the_file(tmp_path):
    assert parse_sbom(b'{"components": [1]}', tmp_path / "x.json") == {"components": [1]}
    with pytest.raises(ValueError, match="x.json"):
        parse_sbom(b"\xff\xfe", tmp_path / "x.json")


def test_component_properties_keeps_the_first_string_value():
    component = {
        "properties": [
            {"name": "a", "value": "1"},
            {"name": "a", "value": "2"},
            {"name": "b", "value": 3},
            "junk",
            {"value": "no name"},
        ]
    }
    assert component_properties(component) == {"a": "1"}
    assert component_properties("not a component") == {}
    assert component_properties({}) == {}
