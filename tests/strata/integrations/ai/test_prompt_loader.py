"""Tests for PromptLoader and built-in prompt classes."""

import json
from pathlib import Path

import pytest

from strata.data.prompts import PromptLoader


class TestPromptLoaderBuiltins:
    def test_load_plan_review(self):
        prompt = PromptLoader.load("plan_review")
        assert prompt.SYSTEM
        assert "risk" in prompt.SYSTEM.lower()

    def test_load_failure_diagnosis(self):
        prompt = PromptLoader.load("failure_diagnosis")
        assert prompt.SYSTEM
        assert "root_cause" in prompt.SYSTEM.lower()

    def test_load_sbom_analysis(self):
        prompt = PromptLoader.load("sbom_analysis")
        assert prompt.SYSTEM

    def test_load_drift_explanation(self):
        prompt = PromptLoader.load("drift_explanation")
        assert prompt.SYSTEM

    def test_load_deployment_summary(self):
        prompt = PromptLoader.load("deployment_summary")
        assert prompt.SYSTEM

    def test_load_policy_review(self):
        prompt = PromptLoader.load("policy_review")
        assert prompt.SYSTEM

    def test_unknown_prompt_raises(self):
        with pytest.raises(ImportError, match="No built-in prompt named"):
            PromptLoader.load("nonexistent_prompt")


class TestPromptLoaderOverride:
    def test_workspace_override_replaces_system(self, tmp_path: Path):
        prompts_dir = tmp_path / ".strata" / "prompts"
        prompts_dir.mkdir(parents=True)
        custom_system = "Custom system prompt for testing."
        (prompts_dir / "plan_review.md").write_text(custom_system)

        prompt = PromptLoader.load("plan_review", work_path=tmp_path)
        assert prompt.SYSTEM == custom_system

    def test_workspace_override_preserves_build_user_prompt(self, tmp_path: Path):
        prompts_dir = tmp_path / ".strata" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "plan_review.md").write_text("override")

        prompt = PromptLoader.load("plan_review", work_path=tmp_path)
        user_prompt = prompt.build_user_prompt({"stages": []}, {"deployment": "test"})
        assert isinstance(user_prompt, str)
        assert len(user_prompt) > 0

    def test_no_override_uses_builtin(self, tmp_path: Path):
        prompt = PromptLoader.load("plan_review", work_path=tmp_path)
        from strata.data.prompts.plan_review import PlanReviewPrompt

        assert prompt.SYSTEM == PlanReviewPrompt.SYSTEM

    def test_missing_work_path_uses_builtin(self):
        prompt = PromptLoader.load("plan_review", work_path=None)
        from strata.data.prompts.plan_review import PlanReviewPrompt

        assert prompt.SYSTEM == PlanReviewPrompt.SYSTEM


class TestPlanReviewPromptUserPrompt:
    def test_contains_deployment_name(self):
        from strata.data.prompts.plan_review import PlanReviewPrompt

        up = PlanReviewPrompt.build_user_prompt({"stages": []}, {"deployment": "my-deploy", "environment": "prd"})
        assert "my-deploy" in up
        assert "prd" in up

    def test_truncates_large_plan(self):
        from strata.data.prompts.plan_review import PlanReviewPrompt

        big_plan = {"stages": [{"stage": "infra", "ok": True, "messages": ["msg"] * 50}]}
        up = PlanReviewPrompt.build_user_prompt(big_plan, {})
        parsed = json.loads(up.split("```json\n")[1].split("\n```")[0])
        assert parsed[0]["messages_truncated"] == 30


class TestFailureDiagnosisUserPrompt:
    def test_truncates_long_output(self):
        from strata.data.prompts.failure_diagnosis import FailureDiagnosisPrompt

        long_error = "\n".join(f"line {i}" for i in range(200))
        up = FailureDiagnosisPrompt.build_user_prompt(long_error, "apply", {})
        assert "last 100 of 200 lines" in up

    def test_short_output_not_truncated(self):
        from strata.data.prompts.failure_diagnosis import FailureDiagnosisPrompt

        short_error = "Error: resource not found"
        up = FailureDiagnosisPrompt.build_user_prompt(short_error, "plan", {"deployment": "d1"})
        assert "Error: resource not found" in up
        assert "truncated" not in up
