"""Tests for terraform_input_validator — parsing and cross-check logic."""

from strata.validators.terraform_input_validator import (
    STRATA_INJECTED_KEYS,
    TerraformVariable,
    _find_closest,
    check_inputs,
    parse_variables_tf,
)

# ---------------------------------------------------------------------------
# parse_variables_tf
# ---------------------------------------------------------------------------


class TestParseVariablesTf:
    def test_parses_simple_variable(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "cluster_name" {
  type        = string
  description = "Name of the AKS cluster"
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert "cluster_name" in result
        assert result["cluster_name"].name == "cluster_name"
        assert result["cluster_name"].has_default is False
        assert result["cluster_name"].description == "Name of the AKS cluster"

    def test_parses_variable_with_default(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "enable_monitoring" {
  type    = bool
  default = false
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert "enable_monitoring" in result
        assert result["enable_monitoring"].has_default is True
        assert result["enable_monitoring"].default_value is False

    def test_parses_sensitive_variable(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "db_password" {
  type      = string
  sensitive = true
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert result["db_password"].sensitive is True

    def test_parses_multiple_files(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "name" {
  type = string
}
"""
        )
        (tmp_path / "vars_network.tf").write_text(
            """
variable "vnet_cidr" {
  type    = string
  default = "10.0.0.0/16"
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert "name" in result
        assert "vnet_cidr" in result
        assert result["vnet_cidr"].has_default is True

    def test_ignores_non_variable_blocks(self, tmp_path):
        (tmp_path / "main.tf").write_text(
            """
resource "azurerm_resource_group" "rg" {
  name     = "rg-test"
  location = "westeurope"
}

variable "location" {
  type    = string
  default = "westeurope"
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert "location" in result
        assert len(result) == 1

    def test_empty_directory_returns_empty(self, tmp_path):
        result = parse_variables_tf(tmp_path)
        assert result == {}

    def test_nonexistent_directory_returns_empty(self, tmp_path):
        result = parse_variables_tf(tmp_path / "nonexistent")
        assert result == {}

    def test_ignores_subdirectory_variables(self, tmp_path):
        """Terraform only reads root module variables."""
        subdir = tmp_path / "modules" / "child"
        subdir.mkdir(parents=True)
        (subdir / "variables.tf").write_text('variable "child_var" { type = string }')
        (tmp_path / "variables.tf").write_text('variable "root_var" { type = string }')

        result = parse_variables_tf(tmp_path)
        assert "root_var" in result
        assert "child_var" not in result

    def test_handles_malformed_tf_gracefully(self, tmp_path):
        """Malformed files are skipped, not crashing."""
        (tmp_path / "bad.tf").write_text("this is not valid HCL {{{{")
        (tmp_path / "good.tf").write_text('variable "ok" { type = string }')

        result = parse_variables_tf(tmp_path)
        assert "ok" in result

    def test_variable_with_validation_blocks(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Must be dev, staging, or prod."
  }
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert result["environment"].validation_rules == 1

    def test_variable_with_null_default(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "optional_tag" {
  type    = string
  default = null
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert result["optional_tag"].has_default is True
        assert result["optional_tag"].default_value is None


# ---------------------------------------------------------------------------
# check_inputs
# ---------------------------------------------------------------------------


class TestCheckInputs:
    def _vars(self, *names, defaults=None):
        """Helper to create a dict of TerraformVariables."""
        defaults = defaults or set()
        return {
            name: TerraformVariable(
                name=name,
                has_default=(name in defaults),
                default_value="default" if name in defaults else None,
            )
            for name in names
        }

    def test_all_inputs_match(self):
        module_vars = self._vars("cluster_name", "enable_ha", "region")
        declared = {"cluster_name", "enable_ha", "region"}
        result = check_inputs(declared, module_vars)
        assert not result.has_errors
        assert result.errors == []
        assert result.warnings == []

    def test_undeclared_input_is_error(self):
        module_vars = self._vars("cluster_name", "enable_ha")
        declared = {"cluster_name", "enable_ha", "typo_var"}
        result = check_inputs(declared, module_vars)
        assert result.has_errors
        assert any("typo_var" in e for e in result.errors)

    def test_typo_suggestion_provided(self):
        module_vars = self._vars("enabled_monitoring", "cluster_name")
        declared = {"enabld_monitoring", "cluster_name"}
        result = check_inputs(declared, module_vars)
        assert result.has_errors
        assert any("did you mean" in e for e in result.errors)
        assert any("enabled_monitoring" in e for e in result.errors)

    def test_required_variable_not_supplied_is_warning(self):
        module_vars = self._vars("cluster_name", "subscription_id")
        declared = {"cluster_name"}
        result = check_inputs(declared, module_vars)
        assert not result.has_errors  # warnings don't block
        assert any("subscription_id" in w for w in result.warnings)
        assert any("Required" in w for w in result.warnings)

    def test_optional_variable_not_supplied_is_info(self):
        module_vars = self._vars("cluster_name", "tags", defaults={"tags"})
        declared = {"cluster_name"}
        result = check_inputs(declared, module_vars)
        assert not result.has_errors
        assert result.warnings == []  # tags has default, not a warning
        assert any("tags" in i for i in result.info)

    def test_optional_variable_not_supplied_defaults_environment_keys_to_injected_keys(self):
        """Omitting environment_keys makes every optional-variable case fall through to
        info — i.e. today's (unscoped) behaviour, unchanged. Regression guard for the
        ADR-0078 rule 3a/3b split not silently becoming the default behaviour."""
        module_vars = self._vars("cluster_name", "tags", defaults={"tags"})
        declared = {"cluster_name"}
        result = check_inputs(declared, module_vars)
        assert result.warnings == []
        assert any("tags" in i for i in result.info)

    def test_rule_3b_optional_variable_present_in_environment_but_not_injected_is_warning(self):
        """ADR-0078 rule 3b: the environment DOES have a value for 'tags', but scoping
        excluded it (no component referenced it) — this must warn, not stay quiet as info,
        because it's a value silently falling back to the module default."""
        module_vars = self._vars("cluster_name", "tags", defaults={"tags"})
        injected = {"cluster_name"}
        environment = {"cluster_name", "tags"}
        result = check_inputs(injected, module_vars, environment_keys=environment)
        assert not result.has_errors
        assert any("tags" in w for w in result.warnings)
        assert not any("tags" in i for i in result.info)

    def test_rule_3a_optional_variable_absent_everywhere_stays_info_even_when_scoped(self):
        """ADR-0078 rule 3a: nobody supplied 'tags' anywhere (not even in the wider
        environment) — the module's default is exactly what's intended, must stay info
        even when environment_keys is explicitly passed (i.e. the provisioner is scoped)."""
        module_vars = self._vars("cluster_name", "tags", defaults={"tags"})
        injected = {"cluster_name"}
        environment = {"cluster_name"}  # 'tags' genuinely nowhere
        result = check_inputs(injected, module_vars, environment_keys=environment)
        assert result.warnings == []
        assert any("tags" in i for i in result.info)

    def test_excluded_keys_not_checked(self):
        module_vars = self._vars("cluster_name")
        declared = {"cluster_name", "strata_injected"}
        result = check_inputs(declared, module_vars, excluded_keys={"strata_injected"})
        assert not result.has_errors

    def test_excluded_keys_not_warned_as_unsupplied(self):
        module_vars = self._vars("workspace_name", "cluster_name")
        declared = {"cluster_name"}
        result = check_inputs(declared, module_vars, excluded_keys={"workspace_name"})
        assert not result.has_errors
        assert not any("workspace_name" in w for w in result.warnings)

    def test_empty_declared_keys(self):
        module_vars = self._vars("a", "b", defaults={"b"})
        result = check_inputs(set(), module_vars)
        assert not result.has_errors
        assert any("'a'" in w for w in result.warnings)  # required, not supplied

    def test_empty_module_vars(self):
        declared = {"some_key"}
        result = check_inputs(declared, {})
        assert result.has_errors  # some_key not in empty module
        assert any("some_key" in e for e in result.errors)

    def test_multiple_errors(self):
        module_vars = self._vars("a", "b", "c")
        declared = {"a", "typo1", "typo2"}
        result = check_inputs(declared, module_vars)
        assert len(result.errors) == 2


class TestUndeclaredInputSeverity:
    """ADR-0084: rule 1's severity depends on *who asked* for the key.

    A component that declares `spec.references: [x]` for a root that cannot accept
    `x` has made a real mistake — error. A key that merely exists in the shared
    environment and this root does not use is normal: Terraform itself emits
    "Value for undeclared variable" and plans regardless (verified on 1.12.2 with
    21 such keys — all warnings, plan produced).

    Without this split, composing an externally-authored root into a shared
    environment is impossible: every key the root does not consume is fatal.
    """

    def _vars(self, *names):
        return {name: TerraformVariable(name=name, has_default=True, default_value="d") for name in names}

    def test_referenced_but_undeclared_is_an_error(self):
        result = check_inputs(
            {"vnet_name"},
            self._vars("location"),
            referenced_keys={"vnet_name"},
        )

        assert any("vnet_name" in e for e in result.errors)
        assert not result.warnings

    def test_merely_present_in_environment_is_a_warning(self):
        result = check_inputs(
            {"enable_aks"},
            self._vars("location"),
            referenced_keys=set(),
        )

        assert not result.errors
        assert any("enable_aks" in w for w in result.warnings)

    def test_omitting_referenced_keys_preserves_legacy_behaviour(self):
        """Regression guard: every pre-existing call site must be unaffected."""
        result = check_inputs({"enable_aks"}, self._vars("location"))

        assert any("enable_aks" in e for e in result.errors)
        assert not result.warnings

    def test_suggestion_survives_the_demotion_to_warning(self):
        """The near-miss is the signal worth keeping — 'daily_quota_gb' against a
        root declaring 'log_workspace_daily_quota_gb' is a real estate-wiring
        mismatch, and demoting the severity must not demote the content."""
        result = check_inputs(
            {"daily_quota_gb"},
            self._vars("log_workspace_daily_quota_gb"),
            referenced_keys=set(),
        )

        assert not result.errors
        assert len(result.warnings) == 1
        assert "did you mean 'log_workspace_daily_quota_gb'" in result.warnings[0]

    def test_mixed_referenced_and_incidental(self):
        result = check_inputs(
            {"typo_key", "enable_aks"},
            self._vars("location"),
            referenced_keys={"typo_key"},
        )

        assert any("typo_key" in e for e in result.errors)
        assert any("enable_aks" in w for w in result.warnings)

    def test_excluded_keys_still_skipped_entirely(self):
        result = check_inputs(
            {"enable_aks"},
            self._vars("location"),
            referenced_keys=set(),
            excluded_keys={"enable_aks"},
        )

        assert not result.errors
        assert not result.warnings

    def test_a_stage_allowlisted_secret_counts_as_referenced(self):
        """`stages[].secrets: [X]` is an explicit request for X, the same as a
        `references` entry — so a root that cannot accept X is a real mismatch.

        The builder folds literal stage-secret names into `referenced_keys` for
        this reason; `['*']` and the no-matching-stages fallback are excluded,
        because neither names a particular key.
        """
        result = check_inputs(
            {"TYPO_SECRET"},
            self._vars("SOME_OTHER_VAR"),
            referenced_keys={"TYPO_SECRET"},
        )

        assert any("TYPO_SECRET" in e for e in result.errors)


# ---------------------------------------------------------------------------
# _find_closest
# ---------------------------------------------------------------------------


class TestFindClosest:
    def test_close_match(self):
        assert _find_closest("enabld", {"enabled", "disabled", "cluster"}) == "enabled"

    def test_no_match(self):
        assert _find_closest("xyz", {"abc", "def", "ghi"}) is None

    def test_exact_match_not_needed(self):
        # Exact matches won't happen in practice (they'd pass the check)
        assert _find_closest("enabled", {"enabled", "disabled"}) == "enabled"

    def test_underscore_typo(self):
        assert _find_closest("enable_ha", {"enabled_ha", "region"}) == "enabled_ha"


# ---------------------------------------------------------------------------
# STRATA_INJECTED_KEYS
# ---------------------------------------------------------------------------


class TestStrataInjectedKeys:
    def test_contains_workspace_name(self):
        assert "workspace_name" in STRATA_INJECTED_KEYS

    def test_contains_platform_providers(self):
        assert "platform_providers" in STRATA_INJECTED_KEYS

    def test_is_frozen(self):
        assert isinstance(STRATA_INJECTED_KEYS, frozenset)
