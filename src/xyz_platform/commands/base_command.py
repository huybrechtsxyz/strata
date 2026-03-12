#!/usr/bin/env python3
"""
===============================================================================
Script Name   : base_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Base command class for the XYZ Platform.
===============================================================================
"""

from datetime import datetime
import os
import sys

import click

from xyz_platform.utils import system


class BaseCommand:
    """Base command class for the XYZ Platform. All CLI commands should inherit from this class to ensure consistent behavior and shared functionality."""

    def ShowConsoleHeader(self, work_path: str = None):
        click.echo("=" * 80)
        click.echo(f"🚀 XYZ PLATFORM — CLI (v{system.get_cli_version()})")
        click.echo("=" * 80)
        click.echo("Automates workspace preparation, configuration, and deployment.")
        click.echo(
            f"⏱️   Timestamp       : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        click.echo(f"📜  Entry point     : {' '.join(sys.argv)}")
        click.echo(f"📂  Current dir     : {os.getcwd()}")
        if work_path:
            click.echo(f"📁  Work path       : {self._work_path}")

    def ShowConsoleFooter(self):
        click.echo("=" * 80)
        click.echo("✨ Thank you for using XYZ Platform CLI!")
        click.echo("📘 Documentation: https://docs.xyzplatform.com")
        click.echo("💬 Support: https://support.xyzplatform.com")
        click.echo("=" * 80)
