"""Tests for helm_input_validator — chart values cross-check logic."""

import yaml

from strata.validators.helm_input_validator import (
    _find_closest,
    check_helm_values,
    collect_all_key_paths,
    parse_chart_values,
)

# ---------------------------------------------------------------------------
# parse_chart_values
# ---------------------------------------------------------------------------


class TestParseChartValues:
    def test_parses_simple_values(self, tmp_path):
        (tmp_path / "values.yaml").write_text(
            yaml.dump({"replicaCount": 1, "image": {"repository": "nginx", "tag": "latest"}})
        )
        result = parse_chart_values(tmp_path)
        assert result["replicaCount"] == 1
        assert result["image"]["repository"] == "nginx"

    def test_no_values_file_returns_empty(self, tmp_path):
        result = parse_chart_values(tmp_path)
        assert result == {}

    def test_empty_values_file_returns_empty(self, tmp_path):
        (tmp_path / "values.yaml").write_text("")
        result = parse_chart_values(tmp_path)
        assert result == {}

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        result = parse_chart_values(tmp_path / "nonexistent")
        assert result == {}

    def test_malformed_yaml_returns_empty(self, tmp_path):
        (tmp_path / "values.yaml").write_text("{{invalid yaml}}: [")
        result = parse_chart_values(tmp_path)
        assert result == {}

    def test_non_dict_values_returns_empty(self, tmp_path):
        (tmp_path / "values.yaml").write_text("- item1\n- item2\n")
        result = parse_chart_values(tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# collect_all_key_paths
# ---------------------------------------------------------------------------


class TestCollectAllKeyPaths:
    def test_flat_dict(self):
        paths = collect_all_key_paths({"a": 1, "b": 2})
        assert paths == {"a", "b"}

    def test_nested_dict(self):
        paths = collect_all_key_paths({"a": {"b": 1, "c": 2}})
        assert paths == {"a", "a.b", "a.c"}

    def test_deeply_nested(self):
        paths = collect_all_key_paths({"a": {"b": {"c": 1}}})
        assert "a.b.c" in paths

    def test_empty_dict(self):
        assert collect_all_key_paths({}) == set()


# ---------------------------------------------------------------------------
# check_helm_values
# ---------------------------------------------------------------------------


class TestCheckHelmValues:
    def _chart(self, **kwargs):
        return kwargs

    def test_matching_keys_no_errors(self):
        chart = self._chart(replicaCount=1, image={"repository": "nginx"}, service={"port": 80})
        override = {"replicaCount": 3, "image": {"repository": "myapp"}}
        errors, warnings = check_helm_values(override, chart, "ns/mod")
        assert errors == []

    def test_undeclared_top_level_key(self):
        chart = self._chart(replicaCount=1, image={"repository": "nginx"})
        override = {"replicaCount": 3, "resoruces": {"limits": {"cpu": "100m"}}}
        errors, _ = check_helm_values(override, chart, "ns/mod")
        assert len(errors) == 1
        assert "resoruces" in errors[0]

    def test_fuzzy_suggestion_provided(self):
        chart = self._chart(resources={"limits": {}}, replicaCount=1)
        override = {"resoruces": {"limits": {"cpu": "100m"}}}
        errors, _ = check_helm_values(override, chart, "ns/mod")
        assert any("did you mean" in e for e in errors)
        assert any("resources" in e for e in errors)

    def test_undeclared_nested_key(self):
        chart = self._chart(image={"repository": "nginx", "tag": "latest", "pullPolicy": "Always"})
        override = {"image": {"repostory": "myapp"}}  # typo: repostory
        errors, _ = check_helm_values(override, chart, "ns/mod")
        assert len(errors) == 1
        assert "image.repostory" in errors[0]
        assert "repository" in errors[0]  # suggestion

    def test_nested_suggestion_includes_parent_path(self):
        chart = self._chart(service={"type": "ClusterIP", "port": 80})
        override = {"service": {"tpye": "NodePort"}}  # typo
        errors, _ = check_helm_values(override, chart, "ns/mod")
        assert any("service.tpye" in e for e in errors)
        assert any("service.type" in e for e in errors)

    def test_empty_override_no_errors(self):
        chart = self._chart(replicaCount=1)
        errors, warnings = check_helm_values({}, chart, "ns/mod")
        assert errors == []
        assert warnings == []

    def test_empty_chart_values_no_errors(self):
        errors, warnings = check_helm_values({"some_key": "value"}, {}, "ns/mod")
        assert errors == []

    def test_multiple_errors(self):
        chart = self._chart(replicaCount=1, image={})
        override = {"replicaCoutn": 2, "imagee": {}}
        errors, _ = check_helm_values(override, chart, "ns/mod")
        assert len(errors) == 2

    def test_label_in_error_message(self):
        chart = self._chart(replicaCount=1)
        override = {"typo_key": "value"}
        errors, _ = check_helm_values(override, chart, "myns/mymod")
        assert any("myns/mymod" in e for e in errors)

    def test_non_dict_override_value_skips_nesting(self):
        """When override has a scalar where chart has a dict, only top-level check applies."""
        chart = self._chart(image={"repository": "nginx"})
        override = {"image": "just-a-string"}  # valid top-level key, unusual value
        errors, _ = check_helm_values(override, chart, "ns/mod")
        assert errors == []  # key is valid at top level

    def test_non_dict_chart_value_skips_nesting(self):
        """When chart has a scalar where override has a dict, only top-level check applies."""
        chart = self._chart(replicaCount=1)
        override = {"replicaCount": {"min": 1, "max": 3}}  # unusual but valid key
        errors, _ = check_helm_values(override, chart, "ns/mod")
        assert errors == []


# ---------------------------------------------------------------------------
# _find_closest
# ---------------------------------------------------------------------------


class TestFindClosest:
    def test_close_match(self):
        assert _find_closest("resoruces", {"resources", "replicaCount"}) == "resources"

    def test_no_match(self):
        assert _find_closest("xyz", {"abc", "def"}) is None
