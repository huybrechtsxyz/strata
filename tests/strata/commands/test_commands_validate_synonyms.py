"""Tests for the 'did you mean' synonym hint on unknown fields (ADR-0083 D9).

`enabled` is the single cross-schema inclusion keyword and no alias is accepted,
so the error message is where the word gets taught. `difflib` scores
"condition"/"when"/"if" far below its 0.5 cutoff against "enabled", which is why
an explicit map is needed rather than relying on fuzzy matching.
"""

from unittest.mock import MagicMock

from strata.commands.validate.run_validate_command import _FIELD_SYNONYMS, ValidateCommand


def _command(bad_field: str, valid_fields) -> ValidateCommand:
    cmd = ValidateCommand.__new__(ValidateCommand)
    error = MagicMock()
    error.field = bad_field
    error.context = {"type": "extra_forbidden"}
    validator = MagicMock()
    validator.get_structured_errors.return_value = [error]
    cmd._validator = validator
    cmd._get_valid_fields_for_path = MagicMock(return_value=valid_fields)  # type: ignore[method-assign]
    return cmd


class TestSynonymMap:
    def test_every_synonym_points_at_enabled(self):
        assert set(_FIELD_SYNONYMS.values()) == {"enabled"}

    def test_covers_the_words_other_tools_use(self):
        """Azure Pipelines/CloudFormation use `condition`, Ansible/Jenkins `when`, GH Actions `if`."""
        assert {"condition", "when", "if"} <= set(_FIELD_SYNONYMS)


class TestSynonymSuggestion:
    def test_condition_is_redirected_to_enabled(self):
        cmd = _command("condition", ["enabled", "name", "provisioner"])

        suggestions = cmd._generate_fix_suggestions()

        assert len(suggestions) == 1
        assert "Did you mean 'enabled'?" in suggestions[0]
        assert "${feature:KEY}" in suggestions[0], "should teach the accepted values too"

    def test_when_and_if_are_redirected_too(self):
        for word in ("when", "if"):
            suggestions = _command(word, ["enabled", "name"])._generate_fix_suggestions()

            assert "Did you mean 'enabled'?" in suggestions[0], word

    def test_matching_is_case_insensitive(self):
        suggestions = _command("Condition", ["enabled", "name"])._generate_fix_suggestions()

        assert "Did you mean 'enabled'?" in suggestions[0]

    def test_no_synonym_hint_where_enabled_is_not_a_valid_field(self):
        """Only suggest the word on schemas that actually accept it."""
        suggestions = _command("condition", ["name", "provisioner"])._generate_fix_suggestions()

        assert "Did you mean 'enabled'?" not in suggestions[0]

    def test_difflib_behaviour_is_unchanged_for_ordinary_typos(self):
        suggestions = _command("provisoner", ["enabled", "provisioner"])._generate_fix_suggestions()

        assert "provisioner" in suggestions[0]

    def test_unrelated_unknown_field_still_lists_valid_fields(self):
        suggestions = _command("wibble", ["enabled", "name"])._generate_fix_suggestions()

        assert "Valid fields:" in suggestions[0]

    def test_synonym_wins_over_a_weak_difflib_match(self):
        """`difflib` would not reach 'enabled' from 'condition' anyway — pin that."""
        import difflib

        assert difflib.get_close_matches("condition", ["enabled"], n=3, cutoff=0.5) == []
