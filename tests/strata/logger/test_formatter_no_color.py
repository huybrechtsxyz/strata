"""Tests for NO_COLOR support in the structlog console formatter."""

from strata.logger.formatters import make_console_formatter


class TestMakeConsoleFormatterNoColor:
    """make_console_formatter() respects the NO_COLOR env var."""

    def test_colors_disabled_when_no_color_set(self, monkeypatch):
        """ConsoleRenderer is created with colors=False when NO_COLOR is in env."""
        monkeypatch.setenv("NO_COLOR", "1")
        import structlog.dev

        captured = {}

        original_init = structlog.dev.ConsoleRenderer.__init__

        def patched_init(self, *args, **kwargs):
            captured["colors"] = kwargs.get("colors", True)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(structlog.dev.ConsoleRenderer, "__init__", patched_init)
        make_console_formatter()
        assert captured.get("colors") is False

    def test_colors_disabled_when_no_color_empty(self, monkeypatch):
        """NO_COLOR='' (any value including empty) disables color per no-color.org."""
        monkeypatch.setenv("NO_COLOR", "")
        import structlog.dev

        captured = {}
        original_init = structlog.dev.ConsoleRenderer.__init__

        def patched_init(self, *args, **kwargs):
            captured["colors"] = kwargs.get("colors", True)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(structlog.dev.ConsoleRenderer, "__init__", patched_init)
        make_console_formatter()
        assert captured.get("colors") is False

    def test_colors_enabled_when_no_color_absent(self, monkeypatch):
        """When NO_COLOR is not set and stdout is a TTY, colors=True."""
        monkeypatch.delenv("NO_COLOR", raising=False)
        # Pretend stdout is a TTY so the isatty() branch resolves to True.
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        import structlog.dev

        captured = {}
        original_init = structlog.dev.ConsoleRenderer.__init__

        def patched_init(self, *args, **kwargs):
            captured["colors"] = kwargs.get("colors")
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(structlog.dev.ConsoleRenderer, "__init__", patched_init)
        make_console_formatter()
        # NO_COLOR absent + isatty=True → colors should be True
        assert captured.get("colors") is True

    def test_returns_processor_formatter(self, monkeypatch):
        """make_console_formatter() returns a ProcessorFormatter regardless of NO_COLOR."""
        monkeypatch.setenv("NO_COLOR", "1")
        import structlog.stdlib

        formatter = make_console_formatter()
        assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
