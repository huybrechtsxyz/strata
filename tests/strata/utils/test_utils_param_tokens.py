#!/usr/bin/env python3
"""Tests for `${param:}` merge and substitution (`base-parameters-v2.md` (work doc), rules 3, 4, 6, 7, 8)."""

from datetime import date

import pytest

from strata.utils.param_tokens import merge_param_values, substitute_params

SETS = {"spoke": ("spoke-acme-001",), "hub": ("hub-defaults", "hub-hub02")}
VALUES = {
    "spoke": {
        "name": "acme-001",
        "seq": "001",
        "count": 3,
        "enabled": True,
        "since": date(2026, 1, 31),
        "network": {"cidr": "10.1.0.0/16", "subnets": [{"name": "app", "cidr": "10.1.1.0/24"}]},
        "customers": ["acmep01", "acmep02"],
        "nothing": None,
    },
    "hub": {"provider": "azure-westus2"},
}


def _sub(node, values=VALUES, sets=SETS):
    return substitute_params(node, values, sets, "spec")


# ---------------------------------------------------------------------------
# merge_param_values (rule 3)
# ---------------------------------------------------------------------------


def test_sets_of_one_role_merge_in_order_later_winning_per_leaf_key():
    values_of = {
        "defaults": {"provider": "azure-westeurope", "tags": {"owner": "platform", "tier": "std"}, "dns": ["a"]},
        "hub02": {"provider": "azure-westus2", "tags": {"tier": "premium"}, "dns": ["b", "c"]},
    }
    merged = merge_param_values({"hub": ("defaults", "hub02")}, values_of)
    assert merged == {
        "hub": {
            "provider": "azure-westus2",
            "tags": {"owner": "platform", "tier": "premium"},  # maps merge per leaf key
            "dns": ["b", "c"],  # lists are replaced
        }
    }


def test_two_roles_with_the_same_key_stay_apart():
    values_of = {"spoke": {"code": "acme"}, "customer": {"code": "acmep01"}}
    merged = merge_param_values({"spoke": ("spoke",), "customer": ("customer",)}, values_of)
    assert merged == {"spoke": {"code": "acme"}, "customer": {"code": "acmep01"}}


def test_one_set_under_two_roles_is_read_under_both():
    merged = merge_param_values({"a": ("shared",), "b": ("shared",)}, {"shared": {"x": 1}})
    assert merged == {"a": {"x": 1}, "b": {"x": 1}}


def test_merging_does_not_change_a_parameter_set():
    values_of = {"one": {"tags": {"a": 1}}, "two": {"tags": {"b": 2}}}
    merge_param_values({"r": ("one", "two")}, values_of)
    assert values_of == {"one": {"tags": {"a": 1}}, "two": {"tags": {"b": 2}}}


# ---------------------------------------------------------------------------
# substitute_params: whole value and embedded (rules 4, 6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("${param:spoke.name}", "acme-001"),
        ("${param:spoke.count}", 3),
        ("${param:spoke.enabled}", True),
        ("${param:spoke.nothing}", None),
        ("${param:spoke.customers}", ["acmep01", "acmep02"]),
        ("${param:spoke.network}", VALUES["spoke"]["network"]),
        ("${param:spoke.network.subnets.app.cidr}", "10.1.1.0/24"),  # list item by name, as `${value:}` walks
        ("${param:spoke.customers.1}", "acmep02"),
        ("${param:spoke}", VALUES["spoke"]),
    ],
)
def test_a_whole_value_token_becomes_the_value_with_its_type(text, expected):
    result, problems = _sub({"x": text})
    assert problems == []
    assert result == {"x": expected}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sub-${param:spoke.name}-env", "sub-acme-001-env"),
        ("st${param:spoke.name}${param:spoke.seq}", "stacme-001001"),
        ("n=${param:spoke.count}", "n=3"),
        ("on=${param:spoke.enabled}", "on=true"),
        ("since ${param:spoke.since}", "since 2026-01-31"),
        ("${param:hub.provider} ", "azure-westus2 "),
    ],
)
def test_an_embedded_token_renders_a_scalar(text, expected):
    result, problems = _sub({"x": text})
    assert problems == []
    assert result == {"x": expected}


def test_strings_are_substituted_at_any_depth_and_keys_are_not():
    node = {"${param:spoke.name}": [{"a": ["${param:spoke.seq}"]}], "n": 1}
    result, problems = _sub(node)
    assert problems == []
    assert result == {"${param:spoke.name}": [{"a": ["001"]}], "n": 1}


def test_an_inserted_list_is_a_copy():
    result, _ = _sub({"x": "${param:spoke.customers}"})
    result["x"].append("changed")
    assert VALUES["spoke"]["customers"] == ["acmep01", "acmep02"]


def test_the_input_is_not_mutated():
    node = {"x": ["${param:spoke.name}"]}
    _sub(node)
    assert node == {"x": ["${param:spoke.name}"]}


# ---------------------------------------------------------------------------
# Problems (rule 7) and the single pass (rule 8)
# ---------------------------------------------------------------------------


def test_a_missing_key_names_the_role_and_the_sets_consulted():
    result, problems = _sub({"envs": ["hub-${param:hub.region}-env"]})
    (problem,) = problems
    assert problem.location == "spec.envs[0]"
    assert problem.code == "param_missing"
    assert problem.message == "${param:hub.region} not found in hub: [hub-defaults, hub-hub02]"
    assert result == {"envs": ["hub-${param:hub.region}-env"]}


def test_a_role_no_document_names_is_a_missing_key():
    _, problems = _sub({"x": "${param:customer.code}"})
    (problem,) = problems
    assert problem.code == "param_missing"
    assert "no document in the chain names a parameter set for role 'customer'" in problem.message


@pytest.mark.parametrize("key", ["spoke.customers", "spoke.network", "spoke.nothing"])
def test_an_embedded_token_needs_a_scalar(key):
    _, problems = _sub({"x": f"a-${{param:{key}}}"})
    (problem,) = problems
    assert problem.code == "param_not_scalar"
    assert "whole value" in problem.message


def test_every_problem_in_a_string_is_reported():
    _, problems = _sub({"x": "${param:spoke.a}-${param:spoke.b}"})
    assert [p.message.split(" ")[0] for p in problems] == ["${param:spoke.a}", "${param:spoke.b}"]


def test_substituted_text_is_not_scanned_again():
    values = {"spoke": {"a": "$${param:spoke.b}", "b": "never"}}
    result, problems = _sub({"x": "${param:spoke.a}", "y": "pre-${param:spoke.a}"}, values, {"spoke": ("s",)})
    assert problems == []
    assert result == {"x": "$${param:spoke.b}", "y": "pre-$${param:spoke.b}"}


def test_run_time_tokens_and_escapes_are_left_alone():
    node = {"x": "${var:region}-${param:spoke.name}-$${param:spoke.name}-${secret:pw}-$${TOKEN}"}
    result, problems = _sub(node)
    assert problems == []
    assert result == {"x": "${var:region}-acme-001-$${param:spoke.name}-${secret:pw}-$${TOKEN}"}


def test_an_escaped_whole_value_is_left_alone():
    result, problems = _sub({"x": "$${param:spoke.name}"})
    assert problems == []
    assert result == {"x": "$${param:spoke.name}"}
