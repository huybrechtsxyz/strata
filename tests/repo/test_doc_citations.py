#!/usr/bin/env python3
"""Every doc path (`docs/<dir>/<name>.md`) cited in a file under `src/` or `tests/` (tracked or new) exists (docs/work/README.md).

A doc moves between `docs/work/`, `docs/design/` and `docs/archive/` as it matures, and nothing else updates the
docstrings, comments and schema descriptions that cite it. This test is that update's reminder.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: A line break inside a citation, after a `/`: docstring or comment indentation and an optional `#`.
_WRAP = r"(?:[ \t]*\r?\n[ \t]*#?[ \t]*)?"

#: `docs/<dirs>/<name>.md`, not followed by more of a file name (`docs/handover.md.j2` is a template path).
_CITATION = re.compile(
    r"docs/((?:[A-Za-z0-9_.-]+/" + _WRAP + r")*?[A-Za-z0-9_.-]+\.md)(?![A-Za-z0-9_-]|\.[A-Za-z0-9_])"
)

_SUFFIXES = (".py", ".j2", ".md", ".yaml", ".yml", ".json", ".txt", ".toml")


def cited_docs(text: str) -> list[str]:
    """Every doc path cited in `text`, relative to `docs/`, with any line wrap removed."""
    return [re.sub(_WRAP, "", match.group(1)) for match in _CITATION.finditer(text)]


def _tracked_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "src", "tests"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    # `--cached` still lists a file deleted or moved in the working tree until the change is staged.
    return [REPO_ROOT / name for name in listed if name.endswith(_SUFFIXES) and (REPO_ROOT / name).is_file()]


@pytest.mark.slow
def test_every_cited_doc_exists():
    docs = REPO_ROOT / "docs"
    by_name: dict[str, list[str]] = {}
    for doc in docs.rglob("*.md"):
        by_name.setdefault(doc.name, []).append(doc.relative_to(REPO_ROOT).as_posix())

    stale: list[str] = []
    for path in _tracked_files():
        for cited in cited_docs(path.read_text(encoding="utf-8", errors="replace")):
            if not (docs / cited).exists():
                now = ", ".join(sorted(by_name.get(Path(cited).name, []))) or "no doc of that name"
                stale.append(f"{path.relative_to(REPO_ROOT).as_posix()}: docs/{cited} (now: {now})")

    assert stale == [], "Cited docs that do not exist; point each at the doc's current place:\n" + "\n".join(stale)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("see docs/work/audit-trail.md's 'Path resolution'", ["work/audit-trail.md"]),
        ("(docs/work/remotes.md#fetch-modes).", ["work/remotes.md"]),
        ("Ends a sentence: docs/README.md.", ["README.md"]),
        ('"""Helpers (docs/design/\nsbom-generation.md Phase 1)."""', ["design/sbom-generation.md"]),
        ("# placement (docs/archive/\n# checkov-integration.md Phase 1).", ["archive/checkov-integration.md"]),
        ("    template: docs/handover.md.j2", []),
        ("docs/design/x.mdx", []),
    ],
)
def test_cited_docs_finds_a_citation_however_it_is_written(text, expected):
    assert cited_docs(text) == expected
