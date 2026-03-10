#!/usr/bin/env python3
"""
===============================================================================
Script Name   : templater.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Template processing functionality for files with placeholders.
===============================================================================
"""

import os
import re
from pathlib import Path

from xyz_platform.logger import get_logger

logger = get_logger(__name__)


class TemplateProcessor:
    """
    Handles processing of template files with variable substitution.

    This class provides functionality to:
    - Process *.template.* files of given path with environment variable substitution
    - Support custom variable substitution
    - Clean up template files after processing

    Template Processing:
    - Automatically processes *.template.* files
    - Substitutes environment variables with pattern like $VAR or ${VAR}
    - Can process templates with custom variables

    Example:
        # main.template.tf contains:
        # organization = "$organization"
        #
        # After processing with organization="my-org":
        # main.tf contains:
        # organization = "my-org"
    """

    def __init__(self, template_dir: Path, cleanup_templates: bool = True):
        """
        Initialize the template processor.

        Args:
            template_dir: Directory containing template files
            cleanup_templates: Whether to remove template files after processing
            use_terraformignore: Whether to manage .terraformignore file
        """
        self.template_dir = template_dir
        self.cleanup_templates = cleanup_templates

    def process_all_templates(self) -> bool:
        """Process all template files in the template directory."""
        try:
            logger.debug(
                "Searching for template files",
                extra={"template_dir": str(self.template_dir)},
            )

            # Look for .template.* files in the terraform directory
            template_files = list(self.template_dir.glob("*.template.*"))

            if not template_files:
                logger.debug(
                    "No template files found",
                    extra={"template_dir": str(self.template_dir)},
                )
                return True

            for template_file in template_files:
                success = self.process_single_template(template_file)
                if not success:
                    logger.warning(
                        "Skipping further processing due to template error",
                        extra={"failed_template": str(template_file)},
                    )
                    return False

            logger.info(
                "Template processing completed",
                extra={
                    "processed_count": len(template_files),
                    "template_dir": str(self.template_dir),
                },
            )
            return True

        except Exception as e:
            logger.error(
                "Template processing failed",
                extra={"template_dir": str(self.template_dir)},
                exc_info=True,
            )
            return False

    def process_single_template(self, template_path: Path) -> bool:
        """Process a single template file."""
        try:
            if not template_path.exists():
                logger.error(
                    "Template file not found",
                    extra={"template_path": str(template_path)},
                )
                return False

            logger.debug(
                "Processing template", extra={"template_file": template_path.name}
            )

            # Read template content
            with open(template_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Replace environment variables
            processed_content = self._substitute_environment_variables(content)

            # Determine output path
            output_filename = template_path.name.replace(".template.", ".")
            output_path = template_path.parent / output_filename

            # Write processed content
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(processed_content)

            # Optionally cleanup template files to avoid confusion
            if self.cleanup_templates:
                self.cleanup_template_file(template_path)

            logger.debug(
                "Generated output from template",
                extra={
                    "template_file": template_path.name,
                    "output_file": output_path.name,
                },
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to process template",
                extra={"template_path": str(template_path)},
                exc_info=True,
            )
            return False

    def cleanup_template_file(self, template_path: Path) -> bool:
        """Remove template files after processing to avoid Terraform conflicts."""
        try:
            logger.debug(
                "Cleaning up template file", extra={"template_file": template_path.name}
            )
            template_path.unlink()
            return True

        except Exception as e:
            logger.error(
                "Failed to cleanup template file",
                extra={"template_path": str(template_path)},
                exc_info=True,
            )
            return False

    def _substitute_environment_variables(self, content: str) -> str:
        """Substitute environment variables in template content."""

        def replace_var(match):
            var_name = match.group(1)
            env_value = os.environ.get(var_name)

            if env_value is None:
                logger.warning(
                    "Environment variable not found, keeping placeholder",
                    extra={"variable_name": var_name},
                )
                return match.group(0)  # Return original placeholder

            logger.debug(
                "Substituted environment variable", extra={"variable_name": var_name}
            )
            return env_value

        # Pattern to match $variablename or ${variablename} - supports all environment variables
        pattern = r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"

        return re.sub(pattern, replace_var, content)
