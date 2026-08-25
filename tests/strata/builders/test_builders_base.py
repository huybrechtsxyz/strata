"""Unit tests for BaseBuilder."""

import pytest

from strata.builders.base_builder import BaseBuilder


class _ConcreteBuilder(BaseBuilder):
    """Minimal concrete implementation for testing the abstract base."""

    def build(self, deployment_service, work_path, build_path, dry_run=False):
        return True

    def before_build(self, deployment_service, work_path, build_path):
        return True

    def after_build(self, deployment_service, work_path, build_path, dry_run=False):
        return True


class TestBaseBuilderAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseBuilder()  # type: ignore


class TestBaseBuilderInit:
    def test_default_verbose_false(self):
        builder = _ConcreteBuilder()
        assert builder.verbose is False

    def test_verbose_true(self):
        builder = _ConcreteBuilder(verbose=True)
        assert builder.verbose is True

    def test_messages_empty_on_init(self):
        builder = _ConcreteBuilder()
        assert builder._messages == []

    def test_errors_empty_on_init(self):
        builder = _ConcreteBuilder()
        assert builder._errors == []

    def test_has_errors_false_initially(self):
        builder = _ConcreteBuilder()
        assert builder.has_errors() is False

    def test_has_messages_false_initially(self):
        builder = _ConcreteBuilder()
        assert builder.has_messages() is False


class TestBaseBuilderHelpers:
    def test_get_errors_empty(self):
        builder = _ConcreteBuilder()
        assert builder.get_errors() == []

    def test_get_messages_empty(self):
        builder = _ConcreteBuilder()
        assert builder.get_messages() == []

    def test_has_errors_after_append(self):
        builder = _ConcreteBuilder()
        builder._errors.append("something broke")
        assert builder.has_errors() is True

    def test_has_messages_after_append(self):
        builder = _ConcreteBuilder()
        builder._messages.append("progress")
        assert builder.has_messages() is True

    def test_get_errors_returns_list(self):
        builder = _ConcreteBuilder()
        builder._errors.extend(["e1", "e2"])
        assert builder.get_errors() == ["e1", "e2"]

    def test_get_messages_returns_list(self):
        builder = _ConcreteBuilder()
        builder._messages.extend(["m1", "m2"])
        assert builder.get_messages() == ["m1", "m2"]

    def test_instances_are_independent(self):
        b1 = _ConcreteBuilder()
        b2 = _ConcreteBuilder()
        b1._errors.append("only b1")
        assert b2.has_errors() is False
        assert b1.get_errors() == ["only b1"]


class TestApplyTemplatesToDir:
    """Regression tests for the Helm local-chart Jinja2 crash (Go-template syntax)."""

    def test_renders_matching_placeholder(self, tmp_path):
        (tmp_path / "values.yaml").write_text("name: {{ STRATA_DEPLOYMENT_NAME }}\n", encoding="utf-8")
        builder = _ConcreteBuilder()

        builder._apply_templates_to_dir(tmp_path, {"STRATA_DEPLOYMENT_NAME": "prd"})

        assert (tmp_path / "values.yaml").read_text(encoding="utf-8") == "name: prd\n"

    def test_go_template_syntax_does_not_raise(self, tmp_path):
        """A Helm chart's templates/ file using `{{ .Release.Name }}` is invalid
        Jinja2 syntax (leading '.' is not a valid expression start) and must not
        crash the whole build — it should be skipped and logged instead."""
        chart_file = tmp_path / "templates" / "deployment.yaml"
        chart_file.parent.mkdir(parents=True)
        chart_file.write_text("metadata:\n  name: {{ .Release.Name }}\n", encoding="utf-8")
        builder = _ConcreteBuilder()

        builder._apply_templates_to_dir(tmp_path, {"STRATA_DEPLOYMENT_NAME": "prd"})

        # Untouched — rendering failed and the original file is left as-is.
        assert chart_file.read_text(encoding="utf-8") == "metadata:\n  name: {{ .Release.Name }}\n"

    def test_exclude_dirs_skips_helm_templates_subdirectory(self, tmp_path):
        chart_file = tmp_path / "templates" / "deployment.yaml"
        chart_file.parent.mkdir(parents=True)
        chart_file.write_text("metadata:\n  name: {{ .Release.Name }}\n", encoding="utf-8")
        values_file = tmp_path / "values.yaml"
        values_file.write_text("name: {{ STRATA_DEPLOYMENT_NAME }}\n", encoding="utf-8")
        builder = _ConcreteBuilder()

        builder._apply_templates_to_dir(
            tmp_path,
            {"STRATA_DEPLOYMENT_NAME": "prd"},
            exclude_dirs={"templates"},
        )

        # templates/ is Helm's own Go-template scope — left completely untouched.
        assert chart_file.read_text(encoding="utf-8") == "metadata:\n  name: {{ .Release.Name }}\n"
        # Files outside templates/ still get strata's substitution.
        assert values_file.read_text(encoding="utf-8") == "name: prd\n"

    def test_nested_templates_directory_excluded_at_any_depth(self, tmp_path):
        """A subchart's templates/ (charts/foo/templates/) must also be excluded."""
        chart_file = tmp_path / "charts" / "foo" / "templates" / "deployment.yaml"
        chart_file.parent.mkdir(parents=True)
        chart_file.write_text("{{ .Release.Name }}\n", encoding="utf-8")
        builder = _ConcreteBuilder()

        builder._apply_templates_to_dir(tmp_path, {"STRATA_DEPLOYMENT_NAME": "prd"}, exclude_dirs={"templates"})

        assert chart_file.read_text(encoding="utf-8") == "{{ .Release.Name }}\n"


class TestApplyTemplateToFile:
    def test_go_template_syntax_does_not_raise(self, tmp_path):
        f = tmp_path / "NOTES.txt"
        f.write_text("Release: {{ .Release.Name }}\n", encoding="utf-8")
        builder = _ConcreteBuilder()

        builder._apply_template_to_file(f, {"STRATA_DEPLOYMENT_NAME": "prd"})

        assert f.read_text(encoding="utf-8") == "Release: {{ .Release.Name }}\n"
