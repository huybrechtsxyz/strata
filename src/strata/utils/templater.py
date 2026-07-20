"""Template processing for files with placeholders using Jinja2."""

import os
from pathlib import Path

from jinja2 import BaseLoader, DebugUndefined, Environment, StrictUndefined, UndefinedError

from strata.logger import get_logger

logger = get_logger(__name__)

# Strict env — used by process_single_template (env var files).
# Missing variables raise UndefinedError.
_STRICT_ENV = Environment(
    loader=BaseLoader(),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    autoescape=False,
)

# Lenient env — used by render() (scaffold templates, builders).
# Missing variables stay visible as {{ var }} in output.
_LENIENT_ENV = Environment(
    loader=BaseLoader(),
    undefined=DebugUndefined,
    keep_trailing_newline=True,
    autoescape=False,
)


class TemplateProcessor:
    """Jinja2-based template processing for files with placeholders.

    This class provides functionality to:
    - Process ``*.template.*`` files with environment variable substitution
    - Support custom variable substitution via ``render()``
    - Clean up template files after processing

    All templates use Jinja2 syntax: ``{{ var }}``, ``{% if %}``, ``{% for %}``.
    Missing variables raise ``jinja2.UndefinedError`` (StrictUndefined).

    Example::

        # main.template.tf contains:
        # organization = "{{ organization }}"
        #
        # After processing with organization="my-org" in env:
        # main.tf contains:
        # organization = "my-org"
    """

    def __init__(self, template_dir: Path, cleanup_templates: bool = True):
        """Initialize the template processor.

        Args:
            template_dir: Directory containing template files.
            cleanup_templates: Whether to remove template files after processing.
        """
        self.template_dir = template_dir
        self.cleanup_templates = cleanup_templates

    def process_all_templates(self) -> bool:
        """Process all template files in the template directory."""
        try:
            logger.debug(
                "Searching for template files",
                template_dir=str(self.template_dir),
            )

            template_files = list(self.template_dir.glob("*.template.*"))

            if not template_files:
                logger.debug(
                    "No template files found",
                    template_dir=str(self.template_dir),
                )
                return True

            for template_file in template_files:
                success = self.process_single_template(template_file)
                if not success:
                    logger.warning(
                        "Skipping further processing due to template error",
                        failed_template=str(template_file),
                    )
                    return False

            logger.info(
                "Template processing completed",
                processed_count=len(template_files),
                template_dir=str(self.template_dir),
            )
            return True

        except Exception as e:
            logger.error(
                "Template processing failed",
                template_dir=str(self.template_dir),
                error=str(e),
                exc_info=True,
            )
            return False

    def process_single_template(self, template_path: Path) -> bool:
        """Process a single template file using environment variables as context."""
        try:
            if not template_path.exists():
                logger.error(
                    "Template file not found",
                    template_path=str(template_path),
                )
                return False

            logger.debug(
                "Processing template",
                template_file=template_path.name,
            )

            content = template_path.read_text(encoding="utf-8")

            # Use environment variables as context
            processed_content = _STRICT_ENV.from_string(content).render(dict(os.environ))

            # Determine output path
            output_filename = template_path.name.replace(".template.", ".")
            output_path = template_path.parent / output_filename

            output_path.write_text(processed_content, encoding="utf-8")

            if self.cleanup_templates:
                self.cleanup_template_file(template_path)

            logger.debug(
                "Generated output from template",
                template_file=template_path.name,
                output_file=output_path.name,
            )
            return True

        except UndefinedError as e:
            logger.error(
                "Missing template variable",
                template_path=str(template_path),
                error=str(e),
            )
            return False
        except Exception as e:
            logger.error(
                "Failed to process template",
                template_path=str(template_path),
                error=str(e),
                exc_info=True,
            )
            return False

    def cleanup_template_file(self, template_path: Path) -> bool:
        """Remove template files after processing."""
        try:
            logger.debug(
                "Cleaning up template file",
                template_file=template_path.name,
            )
            template_path.unlink()
            return True

        except Exception as e:
            logger.error(
                "Failed to cleanup template file",
                template_path=str(template_path),
                error=str(e),
                exc_info=True,
            )
            return False

    @staticmethod
    def render(content: str, context: dict) -> str:
        """Render a Jinja2 template string with the given context dict.

        Uses lenient undefined handling — variables not in *context* are
        left visible in the output as ``{{ var }}``.  This is appropriate
        for scaffold templates where partial context is expected.

        Args:
            content: Template string using Jinja2 syntax (``{{ var }}``).
            context: Mapping of variable names to replacement values.

        Returns:
            The rendered string.
        """
        if not content:
            return content
        template = _LENIENT_ENV.from_string(content)
        return template.render(context)

    @staticmethod
    def render_strict(content: str, context: dict) -> str:
        """Render a Jinja2 template string, raising on any undefined variable.

        Use this when all variables must be present — e.g. integration
        endpoint URLs where a missing env var would produce a broken value.

        Args:
            content: Template string using Jinja2 syntax (``{{ var }}``).
            context: Mapping of variable names to replacement values.

        Raises:
            jinja2.UndefinedError: If the template references a variable
                that is not present in *context*.

        Returns:
            The rendered string.
        """
        if not content:
            return content
        template = _STRICT_ENV.from_string(content)
        return template.render(context)
