#!/usr/bin/env python3
"""Tests for the image tag policy model (docs/design/image-tag-policy.md)."""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from strata.models.image_tag_policy_model import ImageTagAllowEntryModel, ImageTagPolicyModel
from strata.models.schema_export import render_schema
from strata.models.workspace_model import WorkspaceModel
from strata.utils.sbom_utils import MUTABLE_ALIAS_TAGS


def _workspace(**policy) -> dict:
    return {
        "meta": {"name": "myapp-workspace"},
        "spec": {
            "providers": ["azure-main"],
            "provisioners": [{"name": "tf", "tool": "terraform", "source": {"source_path": "terraform/main"}}],
            "image_tag_policy": policy,
        },
    }


def test_empty_policy_is_valid_and_strict_by_default():
    policy = ImageTagPolicyModel.model_validate({})
    assert policy.enforcement == "deny"
    assert policy.require_tag is True
    assert policy.require_pin is False
    assert policy.origins is None
    assert policy.reject_tags is None
    assert policy.tag_pattern is None
    assert policy.rings is None
    assert policy.allow is None


def test_policy_accepts_every_field():
    policy = ImageTagPolicyModel.model_validate(
        {
            "enforcement": "warn",
            "origins": ["artifact"],
            "require_tag": False,
            "reject_tags": ["latest", "main"],
            "tag_pattern": r"^\d+\.\d+\.\d+$",
            "require_pin": True,
            "rings": ["acc", "prd"],
            "allow": [{"name": "redis", "reason": "third party", "expires": "2026-12-31"}],
        }
    )
    assert policy.enforcement == "warn"
    assert policy.origins == ["artifact"]
    assert policy.allow[0].expires == "2026-12-31"


def test_reject_tags_are_stored_lower_case():
    assert ImageTagPolicyModel.model_validate({"reject_tags": ["Latest", "MAIN"]}).reject_tags == ["latest", "main"]


def test_empty_reject_tags_is_kept_distinct_from_omitted():
    assert ImageTagPolicyModel.model_validate({"reject_tags": []}).reject_tags == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"enforcement": "block"},
        {"on_missing_data": "skip"},
        {"origins": []},
        {"origins": ["registry"]},
        {"reject_tags": [""]},
        {"tag_pattern": ""},
        {"tag_pattern": "(unclosed"},
        {"rings": []},
        {"rings": [""]},
        {"allow": [{"name": "redis"}]},
        {"allow": [{"reason": "third party"}]},
        {"allow": [{"name": "", "reason": "third party"}]},
        {"allow": [{"name": "redis", "reason": ""}]},
        {"allow": [{"name": "redis", "reason": "third party", "expires": "soon"}]},
        {"allow": [{"name": "redis", "reason": "third party", "unknown": True}]},
        {"allow": [{"name": "redis", "reason": "a"}, {"name": "redis", "reason": "b"}]},
        {"unknown_field": True},
    ],
)
def test_policy_rejects(overrides):
    with pytest.raises(ValidationError):
        ImageTagPolicyModel.model_validate(overrides)


def test_duplicate_allow_names_are_named_in_the_error():
    with pytest.raises(ValidationError, match="redis"):
        ImageTagPolicyModel.model_validate(
            {"allow": [{"name": "redis", "reason": "a"}, {"name": "redis", "reason": "b"}]}
        )


def test_allow_entry_expires_is_optional():
    assert ImageTagAllowEntryModel.model_validate({"name": "redis", "reason": "third party"}).expires is None


def test_an_unquoted_yaml_date_is_accepted_as_expires():
    entry = ImageTagAllowEntryModel.model_validate({"name": "redis", "reason": "x", "expires": date(2026, 12, 31)})
    assert entry.expires == "2026-12-31"


def test_workspace_policy_is_optional():
    data = _workspace()
    del data["spec"]["image_tag_policy"]
    assert WorkspaceModel.model_validate(data).spec.image_tag_policy is None


def test_workspace_accepts_an_empty_policy():
    assert WorkspaceModel.model_validate(_workspace()).spec.image_tag_policy == ImageTagPolicyModel()


def test_workspace_rejects_an_unknown_policy_field():
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(_workspace(tag_regex="x"))


def test_the_aliases_named_in_the_reject_tags_description_are_the_default_alias_set():
    description = render_schema(ImageTagPolicyModel)["properties"]["reject_tags"]["description"]
    named = {alias for alias in MUTABLE_ALIAS_TAGS if f"`{alias}`" in description}
    assert named == set(MUTABLE_ALIAS_TAGS)


def test_an_unquoted_yaml_datetime_is_refused_with_a_message_about_the_time_of_day():
    with pytest.raises(ValidationError, match="has a time of day; give a date only"):
        ImageTagAllowEntryModel.model_validate(
            {"name": "redis", "reason": "x", "expires": datetime(2026, 12, 31, 10, 0)}
        )
