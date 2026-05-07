import os

import nox

PYPROJECT = nox.project.load_toml("pyproject.toml")
PYTHON_VERSIONS = nox.project.python_versions(PYPROJECT)
nox.options.default_venv_backend = "uv"

FIX = os.getenv("FIX", "0") == "1"


def __ruff_format_command() -> list[str]:
    ruff = ["ruff", "format", "."]
    if not FIX:
        ruff.append("--check")
    return ruff


def __ruff_check_command() -> list[str]:
    ruff = ["ruff", "check", "."]
    if FIX:
        ruff.append("--fix")
    return ruff


def __mypy_command() -> list[str]:
    mypy = ["mypy", ".", "--strict"]
    return mypy


def __yamllint_command() -> list[str]:
    yamllint = ["yamllint", "."]
    return yamllint


def install_requirements(session: nox.Session) -> None:
    session.run("uv", "sync", "--group", "dev", "--active")


@nox.session(python=PYTHON_VERSIONS)
def lint(session: nox.Session) -> None:
    install_requirements(session)
    session.run(*__ruff_format_command())
    session.run(*__ruff_check_command())
    session.run(*__yamllint_command())
    session.run(*__mypy_command())


@nox.session(python=PYTHON_VERSIONS)
def test(session: nox.Session) -> None:
    install_requirements(session)
    # We allow no tests to pass as successful
    session.run("pytest", success_codes=[0, 5])


@nox.session
def lock_check(session: nox.Session) -> None:
    session.run("uv", "lock", "--check")


@nox.session
def build(session: nox.Session) -> None:
    session.run("uv", "build")


@nox.session
def check_python_version(session: nox.Session) -> None:
    session.run("uvx", "check-python-version")
