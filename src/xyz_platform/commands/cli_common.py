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


# Validation for mutually exclusive options
def validate_mutually_exclusive_options(ctx, param, value, exclusive_params):
    """
    Validate that mutually exclusive options are not used together.

    Args:
        ctx: Click context
        param: Current parameter being validated
        value: Value of the current parameter
        exclusive_params: List of parameter names that are mutually exclusive

    Raises:
        click.UsageError: If mutually exclusive options are used together

    Returns:
        The parameter value if validation passes
    """
    if value:
        # Check if any of the exclusive parameters are also set
        for exclusive_param in exclusive_params:
            if ctx.params.get(exclusive_param):
                raise click.UsageError(
                    f"Illegal usage: --{param.name} and --{exclusive_param} are mutually exclusive."
                )
    return value


# Callbacks for specific mutually exclusive options
def validate_verbose_quiet_exclusive(ctx, param, value):
    """
    Callback to ensure --verbose and --quiet are not used together.

    Args:
        ctx: Click context
        param: Current parameter being validated
        value: Value of the current parameter

    Returns:
        The parameter value if validation passes

    Raises:
        click.UsageError: If both --verbose and --quiet are used
    """
    # Determine which parameter is exclusive based on current param name
    exclusive_param = "quiet" if param.name == "verbose" else "verbose"
    return validate_mutually_exclusive_options(ctx, param, value, [exclusive_param])


def validate_output_quiet_exclusive(ctx, param, value):
    """
    Callback to ensure --output and --quiet are not used together.

    Args:
        ctx: Click context
        param: Current parameter being validated
        value: Value of the current parameter

    Returns:
        The parameter value if validation passes

    Raises:
        click.UsageError: If --output (with non-empty value) and --quiet are used together
    """
    if param.name == "output":
        # For --output: check if quiet is set and output value is non-empty
        if value and ctx.params.get("quiet"):
            raise click.UsageError(
                "Illegal usage: --output and --quiet are mutually exclusive."
            )
    elif param.name == "quiet":
        # For --quiet: check if output is set with non-empty value
        if value and ctx.params.get("output"):
            raise click.UsageError(
                "Illegal usage: --quiet and --output are mutually exclusive."
            )
    return value


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
        callback=validate_output_quiet_exclusive,
        help=f"Output format: {', '.join(formats_list)}",
    )(func)
    return func


# --verbose -> Enable verbose output (show logs in console)
def click_output_verbose(func):
    func = click.option(
        "--verbose",
        is_flag=True,
        callback=validate_verbose_quiet_exclusive,
        help="Enable verbose output (show logs in console)",
    )(func)
    return func


# --quiet -> Disable all output (show nothing in console)
def click_output_quiet(func):
    """
    Quiet mode disables all console output.
    Mutually exclusive with --verbose and --output.
    """

    def combined_callback(ctx, param, value):
        # Check both --verbose and --output exclusivity
        value = validate_verbose_quiet_exclusive(ctx, param, value)
        value = validate_output_quiet_exclusive(ctx, param, value)
        return value

    func = click.option(
        "--quiet",
        is_flag=True,
        callback=combined_callback,
        help="Disable any console output",
    )(func)
    return func
