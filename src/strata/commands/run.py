#!/usr/bin/env python3
"""The command lifecycle — everything true of *every* command.

Sets up logging, binds a correlation id, builds the reporter, renders whatever
failed, writes the footer, and exits with the right code. A command body is
left with only its own work.

**Why a context manager and not a base class.** v1 had the same idea and made
it `BaseCommand`, which grew to 955 lines: a five-phase template method that
also loaded deployments (`_load_deployment_service_with_extends`), resolved
build paths, emitted audit events and ran policy checks. Inheritance gave
every command everything, so anything vaguely shared landed there — `strata
version` inherited deployment loading. A context manager has no subclasses to
serve, so it cannot accumulate that way, and the command body stays an
ordinary function rather than an `_execute()` override.

**The rule that keeps it small:** this may only own what is true of *every*
command. Logging, run id, reporter, error-to-exit mapping, footer. Anything
true of only some commands belongs in those commands. That is the discipline
v1 had no way to enforce.

Note it deliberately does **not** open the solution: `validate` needs the
non-raising form while `build` and `deploy` need `require_valid()`, and that
distinction is the point of `strata.controllers.solution_context`.
"""

import logging
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import click

from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_VALIDATION, exit_code_for
from strata.commands.options import make_reporter
from strata.commands.output import Reporter
from strata.logging.config import configure_logging
from strata.logging.context import bind_run
from strata.utils.argv import command_line
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import StrataError


class CommandRun:
    """One command execution, and the reporting around it.

    The header is emitted lazily, on the first `step`/`report`/footer. That
    lets a command call `describe()` *after* doing the work needed to know
    what to describe — `validate` cannot name the solution until it has
    loaded it — without the header ever appearing out of order.
    """

    def __init__(self, reporter: Reporter, run_id: str, output: str) -> None:
        self.reporter = reporter
        self.run_id = run_id
        self.output = output

        #: Set by the command. False makes the run fail with `failure_code`.
        self.ok = True

        #: Exit code used when `ok` is False. Validation is the common case;
        #: a command with a different failure mode overrides it.
        self.failure_code = EXIT_VALIDATION

        self._facts: dict[str, str] = {}
        self._summary = ""
        self._header_written = False

    # -- description ------------------------------------------------------

    def describe(self, **facts: object) -> None:
        """Add facts to the header — solution, root, anything command-specific.

        `invocation` and `started` are added automatically, since they are
        identical for every command.
        """
        self._facts.update({key: str(value) for key, value in facts.items()})

    def _ensure_header(self) -> None:
        """Emit the header once, on first use."""
        if self._header_written:
            return
        self._header_written = True
        facts: dict[str, str] = {**self._facts, "invocation": command_line()}
        if self.output == "json":
            # Correlates the result with the log stream. Kept out of the
            # console header, where it is noise on a run that passed.
            facts["run_id"] = self.run_id
        self.reporter.header(facts)

    # -- progress and findings --------------------------------------------

    def step(self, message: str) -> None:
        """Report progress while the command runs."""
        self._ensure_header()
        self.reporter.step(message)

    def report(
        self,
        diagnostics: Diagnostics,
        *,
        root: Path | None = None,
        document_count: int | None = None,
    ) -> None:
        """Report findings. Does not decide the outcome — the command does."""
        self._ensure_header()
        self._summary = self.reporter.diagnostics(
            diagnostics, root=root, document_count=document_count
        )

    # -- completion -------------------------------------------------------

    def _finish(self) -> int:
        """Write the footer and return the exit code."""
        self._ensure_header()
        self.reporter.footer(self.ok, self._summary)
        return EXIT_SUCCESS if self.ok else self.failure_code

    def _fail(self, error: StrataError) -> int:
        """Render a failure that stopped the command, and return its code.

        JSON must still produce its one document, because pipelines parse
        stdout *after* a non-zero exit. Console prints the message alone,
        without a report around it — "you are in the wrong directory" does not
        need a header and a summary.
        """
        if self.output == "json":
            diagnostics = Diagnostics()
            diagnostics.error(str(error), code=type(error).__name__)
            self._ensure_header()
            self.reporter.diagnostics(diagnostics)
            self.reporter.footer(False, "")
        else:
            click.echo(click.style(f"error: {error}", fg="red"), err=True)
        return exit_code_for(error)


@contextmanager
def command_run(
    name: str,
    *,
    output: str,
    quiet: bool = False,
    verbose: bool = False,
    context: Mapping[str, str] | None = None,
) -> Iterator[CommandRun]:
    """Run `name`, reporting and exiting consistently.

    Always raises `click.exceptions.Exit` — success included — so a command
    body never has to remember to set an exit code.

    Args:
        name: Command name, for the header and the JSON envelope.
        output: Rendering format, from `--output`.
        quiet: Suppress decoration but never findings.
        verbose: Raise the log level from WARNING to INFO.
        context: Facts for the header known before the command runs.

    Yields:
        The run, for describing, stepping and reporting.

    Raises:
        click.exceptions.Exit: Always.
    """
    # Logs go to stderr by default, so stdout stays parseable in JSON mode.
    configure_logging(level=logging.INFO if verbose else logging.WARNING)

    run_id = uuid.uuid4().hex
    bind_run(run_id)

    run = CommandRun(make_reporter(name, output, quiet=quiet), run_id, output)
    if context:
        run.describe(**context)

    try:
        yield run
    except StrataError as error:
        raise click.exceptions.Exit(run._fail(error)) from error

    raise click.exceptions.Exit(run._finish())
