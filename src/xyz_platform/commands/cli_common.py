"""
===============================================================================
Script Name   : cli_common.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Common decorators and utilities for XYZ Platform CLI.
===============================================================================
"""

import click

OUTPUT_FORMATS = ["", "text", "json"]


# --config-path -> The path to the configuration files
def click_config_path(func):
    func = click.option(
        "--config-path",
        default=None,
        type=click.Path(exists=True, file_okay=False, dir_okay=True),
        help="Optional configuration directory path (must exist)",
    )(func)
    return func


# --config-file -> The path to a specific configuration file
def click_config_file(func):
    func = click.option(
        "--config-file",
        default=None,
        type=click.Path(exists=True, file_okay=True, dir_okay=False),
        help="Optional path to a specific configuration file (must exist)",
    )(func)
    return func


# --output json -> Returns the output in JSON format
def click_output_format(func):
    """
    Standard output format option for CLI commands.

    Available formats: json, yaml, table, text
    Default: text (human-readable, command-specific formatting)
    """
    formats_list = [f for f in OUTPUT_FORMATS if f]  # Exclude blank
    func = click.option(
        "--output",
        type=click.Choice(OUTPUT_FORMATS, case_sensitive=False),
        default="",
        help=f"Output format: {', '.join(formats_list)}",
    )(func)
    return func


# --verbose -> Enable verbose output (show logs in console)
def click_verbose(func):
    func = click.option(
        "--verbose",
        is_flag=True,
        help="Enable verbose output (show logs in console)",
    )(func)
    return func


# --quit -> Disable all output (show nothing in console)
def click_quit(func):
    func = click.option(
        "--quit",
        is_flag=True,
        help="Disable any console output",
    )(func)
    return func
