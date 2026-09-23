#!/usr/bin/env python3
"""Command layer — the CLI entry point and its subcommands.

Top of the import hierarchy (ADR-0003): commands may use controllers,
services, models and utils; nothing may import a command. That keeps every
capability callable without a terminal, which is what makes the library
testable and reusable from CI or a future server.
"""
