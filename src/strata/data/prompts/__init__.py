"""Prompt loader with .strata/prompts/ override support.

Resolution order:
  1. ``get_ai_prompts_dir(work_path) / <name>.md``  — operator override (system prompt only)
  2. Built-in ``strata.data.prompts.<name>`` Python module — fallback default

Usage::

    prompt = PromptLoader.load("plan_review", work_path=Path("/path/to/workspace"))
    response = provider.complete(prompt.SYSTEM, prompt.build_user_prompt(plan_json, ctx))
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Optional

from strata.logger import get_logger
from strata.utils.config import get_ai_prompts_dir

logger = get_logger(__name__)


class _PromptWrapper:
    """Wraps a built-in prompt class, optionally overriding its SYSTEM attribute."""

    def __init__(self, cls: Any, system_override: Optional[str] = None) -> None:
        self._cls = cls
        self.SYSTEM: str = system_override if system_override is not None else cls.SYSTEM

    def build_user_prompt(self, *args: Any, **kwargs: Any) -> str:
        return self._cls.build_user_prompt(*args, **kwargs)


class PromptLoader:
    """Load prompt classes with optional workspace-level overrides."""

    @staticmethod
    def load(name: str, work_path: Optional[Path] = None) -> _PromptWrapper:
        """Return a prompt wrapper for *name*.

        Args:
            name:       Prompt name, e.g. ``"plan_review"``.
            work_path:  Workspace root.  If supplied, checks for
                        ``<work_path>/.strata/prompts/<name>.md``.

        Returns:
            ``_PromptWrapper`` with ``.SYSTEM`` and ``.build_user_prompt()``.

        Raises:
            ImportError: If the built-in prompt module does not exist.
        """
        # Load built-in prompt class
        module_path = f"strata.data.prompts.{name}"
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise ImportError(f"No built-in prompt named '{name}' ({module_path})") from exc

        # Derive the class name: plan_review -> PlanReviewPrompt
        class_name = "".join(part.capitalize() for part in name.split("_")) + "Prompt"
        cls = getattr(module, class_name, None)
        if cls is None:
            available = [c for c in dir(module) if c.endswith("Prompt") and not c.startswith("_")]
            raise ImportError(
                f"Module '{module_path}' has no class '{class_name}'. Available Prompt classes: {available}"
            )

        # Check for workspace override
        system_override: Optional[str] = None
        if work_path is not None:
            override_path = get_ai_prompts_dir(work_path) / f"{name}.md"
            if override_path.exists():
                system_override = override_path.read_text(encoding="utf-8").strip()
                logger.debug("ai_prompt_override_loaded", name=name, path=str(override_path))

        return _PromptWrapper(cls=cls, system_override=system_override)
