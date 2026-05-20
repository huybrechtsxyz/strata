"""Common Click decorators, option callbacks, and exit-code helpers for Strata CLI commands."""

import click

OUTPUT_FORMATS = ["console", "text", "json", "ndjson"]


# Exit code handler for commands with validation
def handle_command_exit(command, success: bool) -> None:
    """
    Handle command exit codes based on success status and validation errors.

    Exit codes:
        0 - Success
        1 - System/execution failure
        3 - Validation failure (file invalid)

    Args:
        command: Command instance with has_validation_errors() method
        success: Initial success status from command.execute()

    Raises:
        click.exceptions.Exit: With appropriate exit code
    """
    # Mark as failure if there are validation errors
    if success and hasattr(command, "has_validation_errors") and command.has_validation_errors():
        success = False

    if not success:
        # Check if failure was due to validation errors vs system errors
        if hasattr(command, "has_validation_errors") and command.has_validation_errors():
            # File didn't validate - exit code 3
            raise click.exceptions.Exit(3)
        else:
            # System/execution error - exit code 1
            raise click.exceptions.Exit(1)

    # Success - exit code 0 (implicit)


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
                raise click.UsageError(f"Illegal usage: --{param.name} and --{exclusive_param} are mutually exclusive.")
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


# Validation to ensure --output and --quiet are not used together
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
            raise click.UsageError("Illegal usage: --output and --quiet are mutually exclusive.")
    elif param.name == "quiet":
        # For --quiet: check if output is set with non-empty value
        if value and ctx.params.get("output"):
            raise click.UsageError("Illegal usage: --quiet and --output are mutually exclusive.")
    return value


# --output json -> Returns the output in JSON format
def click_output_format(func):
    """
    Standard output format option for CLI commands.

    Available formats: json, text
    Default: text (human-readable, command-specific formatting)
    """
    formats_list = [f for f in OUTPUT_FORMATS if f]  # Exclude blank
    func = click.option(
        "--output",
        type=click.Choice(OUTPUT_FORMATS, case_sensitive=False),
        default=None,
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


# --work-path -> The path to the root of the workspace if not in pwd
def click_work_path(func):
    func = click.option(
        "--work-path",
        default=None,
        type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=str),
        help="Optional root path of the workspace, if different then PWD (must exist)",
    )(func)
    return func


# --file / -f -> Path to the deployment YAML file (supports STRATA_FILE env var)
def click_file(func):
    func = click.option(
        "--file",
        "-f",
        default=None,
        envvar="STRATA_FILE",
        metavar="PATH",
        help="Path to the deployment YAML file. [env: STRATA_FILE]",
    )(func)
    return func


# --profile -> The active profile to use (defaults to the currently active profile)
def click_profile(func):
    func = click.option(
        "--profile",
        default=None,
        help="Profile name. Defaults to the currently active profile.",
    )(func)
    return func
