# XYZ Platform

The XYZ Platform is a comprehensive solution for managing and deploying cloud infrastructure and applications. It leverages Infrastructure as Code (IaC) principles to provide a structured and automated approach to provisioning resources across various cloud providers.

## Installation
### Linux
```console
uv sync
source .venv/bin/activate
```

### Windows
```console
uv sync
source .venv/Scripts/activate
```

## Versioning
[Python versioning schema](https://peps.python.org/pep-0440/) is used in combination with [SemVer](https://semver.org/) for `Major.Minor.Patch`. For more details on this check [this subsection](https://peps.python.org/pep-0440/#semantic-versioning).
In the root directory of the repository the `VERSION.txt` file is used as the single source of truth for the version.

## Software dependencies
## Latest releases

## API references

# Build and Test
## Developer installation
### Linux
```console
uv sync --group dev
. .venv/bin/activate
```

### Windows
```console
uv sync --group dev
source .venv/Scripts/activate
```

## Run checks on the code

### ruff 
[`ruff`](https://docs.astral.sh/ruff/) is an extermely fast Python linter and code formatter. It checks the style and quality of Python code. To run `ruff`, run:
```console
ruff check .
```
To fix errors automatically run:
```console
ruff check . --fix
ruff format .
```

### mypy
[`mypy`](https://mypy-lang.org/) is an optional static type checker for Python that aims to combine the benefits of dynamic (or "duck") typing and static typing. To run `mypy`, run:
```console
mypy .
```

### yamllint
[`yamllint`](https://yamllint.readthedocs.io/en/stable/quickstart.html) is a linter for yaml files. To run it, run:
```console
yamllint .
```

### pytest
[`pytest`](https://docs.pytest.org/) is a testing framework that allows users to write test codes using Python programming language. To tun your tests (that are placed in the tests module), run:
```console
pytest 
```

### nox
[`nox`](https://nox.thea.codes/en/stable/) is a command-line tool that automates testing in multiple Python environments. `nox` uses a standard Python file for configuration (`noxfile.py`).

In this package, `nox` is configured to run all the above tools - `ruff`, `yamllint`, `mypy` and `pytest` and more. By default, `nox` creates a separate Python 3.13 environment. To run all the tools above with this default, run:

```console
nox
```

To run only the `ruff`, `yamllint` and `mypy` (the linters and the static typing check):

```console
nox -s lint
```

If you want the style problems to be fixed (by `ruff`), run:
```console
FIX=1 nox -s lint
```

To run the unit tests, run:

```console
nox -s tests
```

To build your package, run:

```console
nox -s build
```

### Lockfiles
Lockfiles make sure that the virtual environment of the developer is exactly the same as the environment which is used in CI or for other developers. Where packages or services typically define dependencies as ranges, lockfiles are pinned to exact versions. These exact versions are the result of resolving the dependencies to a satisfying set of package versions. Whenever updating a dependency, the `uv.lock` file might become out-of-date. Running `uv lock` or `uv run` will update the lockfile to be a satisfying version set again. There is also a `nox` session that checks whether the lockfile is up-to-date.

```console
nox -s lock_check
```

Whenever updating your local git checkout, a new lockfile version might have been pulled. To sync with your virtual environment, you can run `uv sync`.
# Generation of documentation

In order to generate the documentation, run 
```bash
make
```

This will create the documentation and will place it in a new directory `html_docs`. 
Surf to `html_docs/index.html` to see the documentation.

Note that you can install in VS Code the extension [Open HTML In Browser](https://marketplace.visualstudio.com/items?itemName=peakchen90.open-html-in-browser) which will allow you to right click on the `html_docs/index.html` and select "Open in Default Browser".

In order to clean the documentation, run
```bash
make clean
```
