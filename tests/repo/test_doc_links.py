#!/usr/bin/env python3
"""Links between docs (docs/work/README.md).

Two rules over every relative Markdown link in a Markdown file of the repository (`docs/`, the root `README.md`,
`.github/`, ...):

- It resolves: a doc moved between `docs/work/`, `docs/design/` and `docs/archive/` leaves no link behind.
- Only a work doc links to a work doc. A work doc graduates to `docs/design/`, moves to `docs/archive/` or is deleted,
  so a finished doc names a work doc without a link: `` `audit-trail.md` (work doc) ``.

Fenced code blocks and inline code spans are not links.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"

_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+?\.md)(?:#[^)\s]*)?\)")
_FENCE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN = re.compile(r"`[^`\n]*`")


def doc_links(text: str) -> list[tuple[int, str]]:
    """`(line number, href)` for each relative link to a `.md` file in `text`, outside code."""
    links: list[tuple[int, str]] = []
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        spans = [match.span() for match in _CODE_SPAN.finditer(line)]
        for match in _LINK.finditer(line):
            if "://" in match.group(1) or any(start <= match.start() < end for start, end in spans):
                continue
            links.append((number, match.group(1)))
    return links


def _is_work_doc(path: Path) -> bool:
    return path.is_relative_to(DOCS / "work")


def _docs() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    # `--cached` still lists a file deleted or moved in the working tree until the change is staged.
    return [REPO_ROOT / name for name in listed if name.endswith(".md") and (REPO_ROOT / name).is_file()]


def _exists_as_written(path: Path) -> bool:
    """`path` exists with every part spelled as on disk, so a link that resolves only on a case-insensitive file
    system (`./contributing.md` for `CONTRIBUTING.md`) fails on Windows as it does on Linux and GitHub."""
    try:
        parts = path.relative_to(REPO_ROOT).parts
    except ValueError:
        return path.exists()
    here = REPO_ROOT
    for part in parts:
        if not here.is_dir() or part not in os.listdir(here):
            return False
        here = here / part
    return True


def _problems() -> tuple[list[str], list[str]]:
    by_name: dict[str, list[str]] = {}
    for doc in DOCS.rglob("*.md"):
        by_name.setdefault(doc.name, []).append(doc.relative_to(REPO_ROOT).as_posix())
    broken: list[str] = []
    into_work: list[str] = []
    for doc in _docs():
        where = doc.relative_to(REPO_ROOT).as_posix()
        for number, href in doc_links(doc.read_text(encoding="utf-8")):
            target = (doc.parent / href).resolve()
            if not _exists_as_written(Path(os.path.normpath(doc.parent / href))):
                now = ", ".join(sorted(by_name.get(Path(href).name, []))) or "no doc of that name"
                broken.append(f"{where}:{number}: {href} (now: {now})")
            elif _is_work_doc(target) and not _is_work_doc(doc.resolve()):
                into_work.append(f"{where}:{number}: {href} -> write `{target.name}` (work doc)")
    return broken, into_work


@pytest.mark.slow
def test_every_link_between_docs_resolves():
    broken, _ = _problems()
    assert broken == [], "Links to a doc that does not exist; point each at the doc's current place:\n" + "\n".join(
        broken
    )


@pytest.mark.slow
def test_only_a_work_doc_links_to_a_work_doc():
    _, into_work = _problems()
    assert into_work == [], "A finished doc links to a work doc, which will move; name it without a link:\n" + (
        "\n".join(into_work)
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("See [audit](../work/audit-trail.md#layers).", [(1, "../work/audit-trail.md")]),
        ("one\n[x](x.md) and [y](./y.md)", [(2, "x.md"), (2, "./y.md")]),
        ("`[x](../design/x.md)` is an example", []),
        ("```\n[x](x.md)\n```\n[y](y.md)", [(4, "y.md")]),
        ("[site](https://example.com/a.md) and [img](a.png)", []),
    ],
)
def test_doc_links_finds_links_outside_code(text, expected):
    assert doc_links(text) == expected
