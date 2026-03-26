python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]" build


if (-not (Test-Path -Path .\reports)) {
    New-Item -ItemType Directory -Path .\build | Out-Null
}

# Run ruff and capture output to reports/ruff-report.txt (also display)
ruff check ./src 2>&1 | Tee-Object -FilePath .\build\ruff-report.txt

# Run black (check mode) and capture output to reports/black-report.txt (also display)
# Note: black returns non-zero exit code on format problems; the script will show that exit code.
black --check ./src 2>&1 | Tee-Object -FilePath .\build\black-report.txt

black ./src 2>&1 | Tee-Object -FilePath .\build\black-report.txt

python -m build
pytest -q
