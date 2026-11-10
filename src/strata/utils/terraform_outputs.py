#!/usr/bin/env python3
"""Read the `output` blocks a Terraform or OpenTofu root module declares: each output's `description` and
`sensitive` flag (docs/design/deployment-documentation.md, section 3).

`terraform output -json` returns `value`, `type` and `sensitive`, but not the description, so it is read from the
root module's own `.tf` files with `python-hcl2`, the parser the SBOM Terraform collector already uses. Only the
top-level `*.tf` and `*.tf.json` files of the root module: outputs of child modules are not the step's outputs.

Best effort: a file that does not parse is skipped, and a description that is not a plain string (an expression)
is left out. A description is source code, not a secret, so it is kept for a sensitive output too.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hcl2


@dataclass(frozen=True)
class DeclaredOutput:
    """One `output` block of a root module."""

    name: str
    description: str | None
    sensitive: bool


def read_declared_outputs(root: Path) -> dict[str, DeclaredOutput]:
    """Return the outputs the root module in `root` declares, by name. Empty when `root` has no Terraform files."""
    return {
        name: DeclaredOutput(
            name=name,
            description=_text(body.get("description"), from_hcl) or None,
            sensitive=body.get("sensitive") in (True, "true"),
        )
        for name, body, from_hcl in _blocks(root, "output")
    }


def read_declared_variables(root: Path) -> set[str]:
    """The names of the `variable` blocks the root module in `root` declares.

    Terraform reads a `TF_VAR_<name>` environment variable only for a declared variable, and `deploy run` sets one
    for every resolved value, so these names are the secrets a Terraform step can actually read that way.
    """
    return {name for name, _, _ in _blocks(root, "variable")}


def _blocks(root: Path, kind: str) -> Iterator[tuple[str, dict[str, Any], bool]]:
    """`(label, body, from_hcl)` for every `kind` block in the root module's top-level `*.tf` and `*.tf.json`
    files, in file-name order. A file that does not parse is skipped."""
    if not root.is_dir():
        return
    for path in sorted([*root.glob("*.tf"), *root.glob("*.tf.json")]):
        from_hcl = path.suffix == ".tf"
        data = _load_hcl(path) if from_hcl else _load_json(path)
        blocks = (data or {}).get(kind) or []
        # HCL (python-hcl2) and JSON syntax both allow a list of `{label: body}` objects; JSON also one object.
        for block in [blocks] if isinstance(blocks, dict) else blocks:
            if not isinstance(block, dict):
                continue
            for label, body in block.items():
                if label == "__is_block__":
                    continue
                if isinstance(body, list) and body and isinstance(body[0], dict):
                    body = body[0]  # JSON syntax may wrap one block body in a list
                name = _text(label, from_hcl)
                if name and isinstance(body, dict):
                    yield name, body, from_hcl


def _load_hcl(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = hcl2.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _text(value: Any, from_hcl: bool) -> str | None:
    """A string from either syntax: HCL keeps the quotes (`_hcl_string()`), JSON does not."""
    if from_hcl:
        return _hcl_string(value)
    return value if isinstance(value, str) else None


def _hcl_string(value: Any) -> str | None:
    """A quoted HCL string literal as `python-hcl2` returns it (`'"text"'`, escapes kept), unquoted.

    Anything else (an expression such as `"${var.x}"` stays a string and is kept as written; a number, a list)
    returns `None`.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = text[1:-1]
        return decoded if isinstance(decoded, str) else None
    return text
