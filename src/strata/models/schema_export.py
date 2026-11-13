#!/usr/bin/env python3
"""JSON Schema export for `.strata/schemas/` (docs/design/schema-export.md).

The schemas serve the YAML editor and an AI asked "how do I model this?". They are generated from the
models, so this module only shapes Pydantic's output; it never holds field knowledge of its own:

- **Reference targets** come from `References`/`RemoteReference`, which add `x-strata-references` and
  `x-strata-remote` themselves (`reference_fields.py`).
- **Cross-field rules** come from a model's `schema_rules` class attribute and are appended to its
  description under "Rules:". A schema cannot express most of them, and an editor ignores unknown keywords.
  A field that references the model and has its own description repeats the rules there, because the
  YAML editor shows the field's description on hover and never the model's.
- **Developer rationale** stays in code: a description paragraph that starts `Design notes:` is dropped.
- **Examples** come from `schema_examples.EXAMPLES`, as the root `examples`.
- **Size and noise**: generated titles (property names and model class names) and `default: null` carry
  no information and are dropped; the editor would head every hover with a class name.
"""

import copy
import re
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema
from pydantic_core import core_schema as cs

from strata.models.common_models import PlatformKind
from strata.models.schema_examples import EXAMPLES

#: Everything from this paragraph on is for maintainers, not for the schema's readers.
DESIGN_NOTES_MARKER = "Design notes:"

_DESIGN_NOTES = re.compile(r"\n*" + re.escape(DESIGN_NOTES_MARKER) + r".*\Z", re.S)


def strip_design_notes(text: str) -> str:
    """`text` without its trailing `Design notes:` paragraph (and anything after it)."""
    return _DESIGN_NOTES.sub("", text).rstrip()


_LIST_ITEM = re.compile(r"(?:[-*] |\d+\. |\s)")


def reflow(text: str) -> str:
    """Join the hard-wrapped lines of each paragraph.

    List items and indented blocks stay on their own lines, but an indented line that continues a list
    item is joined to it.
    """
    paragraphs = []
    for paragraph in text.split("\n\n"):
        lines = paragraph.split("\n")
        joined = [lines[0]]
        for line in lines[1:]:
            continues_item = (
                joined[-1].startswith("- ") and line.startswith("  ") and not line.lstrip().startswith("- ")
            )
            if continues_item or not (_LIST_ITEM.match(line) or joined[-1].endswith("::")):
                joined[-1] += " " + line.strip()
            else:
                joined.append(line)
        paragraphs.append("\n".join(joined))
    return "\n\n".join(paragraphs)


class StrataJsonSchema(GenerateJsonSchema):
    """Pydantic's generator, minus auto-generated field titles, plus each model's `schema_rules`."""

    def field_title_should_be_set(self, schema: Any) -> bool:
        return False

    def model_schema(self, schema: cs.ModelSchema) -> dict[str, Any]:
        json_schema = super().model_schema(schema)
        description = json_schema.get("description", "")
        if not description:
            # A scalar-shorthand model is wrapped in `anyOf`, which leaves its docstring on the object branch.
            for branch in json_schema.get("anyOf", ()):
                if isinstance(branch, dict) and "description" in branch:
                    description = branch.pop("description")
                    break
        description = strip_design_notes(description)
        rules = getattr(schema["cls"], "schema_rules", ())
        if rules:
            listed = "\n".join(f"- {rule}" for rule in rules)
            description = f"{description}\n\nRules:\n{listed}" if description else f"Rules:\n{listed}"
        if description:
            json_schema["description"] = description
        return json_schema


def _clean(node: Any) -> None:
    """Drop maintainer notes, titles and `default: null` everywhere in a generated schema, in place."""
    if isinstance(node, dict):
        description = node.get("description")
        if isinstance(description, str):
            node["description"] = reflow(strip_design_notes(description))
        if isinstance(node.get("title"), str):
            del node["title"]
        if "default" in node and node["default"] is None:
            del node["default"]
        for value in node.values():
            _clean(value)
    elif isinstance(node, list):
        for item in node:
            _clean(item)


_RULES_SECTION = re.compile(r"(?:^|\n\n)(Rules:\n.*)\Z", re.S)


def _referenced_model(node: dict[str, Any]) -> str | None:
    """Name of the model `node` points at, directly, through a nullable `anyOf`, or as list items."""
    ref = node.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    for branch in node.get("anyOf", ()):
        if isinstance(branch, dict) and (found := _referenced_model(branch)):
            return found
    items = node.get("items")
    return _referenced_model(items) if isinstance(items, dict) else None


def _repeat_rules_at_references(schema: dict[str, Any]) -> None:
    """Append a model's `Rules:` to every described field that references it, in place."""
    definitions = schema.get("$defs", {})

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            description = node.get("description")
            target = _referenced_model(node) if isinstance(description, str) else None
            if target and isinstance(description, str) and "Rules:" not in description:
                rules = _RULES_SECTION.search(definitions.get(target, {}).get("description", ""))
                if rules:
                    node["description"] = f"{description}\n\n{rules.group(1)}"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk({key: value for key, value in schema.items() if key != "$defs"})
    walk(definitions)


def render_schema(model_cls: type[BaseModel]) -> dict[str, Any]:
    """`model_cls`'s JSON Schema as strata exports it, without the root examples."""
    schema = model_cls.model_json_schema(schema_generator=StrataJsonSchema)
    _clean(schema)
    _repeat_rules_at_references(schema)
    return schema


#: Kinds whose spec may be a template (`base-parameters-v2.md` (work doc), ADR-0061): the spec model's `$defs` name,
#: and the spec fields that are never templated (rule 5). Mirrors `TEMPLATE_SERVICE_BY_KIND` in the controller layer,
#: which this layer cannot import.
_TEMPLATE_SPECS: dict[PlatformKind, tuple[str, frozenset[str]]] = {
    PlatformKind.DEPLOYMENT: ("DeploymentSpecModel", frozenset({"partial", "extends"})),
    PlatformKind.ENVIRONMENT: ("EnvironmentSpecModel", frozenset({"partial", "extends"})),
    PlatformKind.TENANT: ("TenantSpecModel", frozenset({"partial", "extends"})),
}

#: What a templated value may also be: a string holding a `${param:}` token (`hub-${param:spoke.hub}-env`), or one
#: that is only a token, which then stands for a value of any type (a number, a list, a map).
_PARAM_TOKEN_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": r"\$\{param:[A-Za-z0-9_.-]+\}",
    "description": "Filled from the document's parameter sets when the solution loads (`${param:<role>.<key>}`).",
}

#: Where the token schema sits in an exported schema, and the branch that points at it: defined once, not inlined
#: at every field.
_PARAM_TOKEN_DEF = "ParamToken"
_PARAM_TOKEN_REF: dict[str, Any] = {"$ref": f"#/$defs/{_PARAM_TOKEN_DEF}"}

#: Keys that carry no constraint: a schema node with only these accepts any string already.
_UNCONSTRAINED_STRING_KEYS = frozenset({"type", "description", "default", "examples", "title"})


def _with_param_token(node: dict[str, Any]) -> dict[str, Any]:
    """`node`, also accepting a `${param:}` token: `node` itself when it already accepts any string."""
    if node.get("type") == "string" and set(node) <= _UNCONSTRAINED_STRING_KEYS:
        return node
    # The description and default stay on the outer node, where the editor shows them on hover.
    annotations: dict[str, Any] = {key: node[key] for key in ("description", "default") if key in node}
    rest = {key: value for key, value in node.items() if key not in annotations}
    branches = list(rest["anyOf"]) if set(rest) == {"anyOf"} else [rest]
    if _PARAM_TOKEN_REF not in branches:
        branches.append(dict(_PARAM_TOKEN_REF))
    return {"anyOf": branches, **annotations}


def _accept_param_tokens(node: dict[str, Any], skip: frozenset[str] = frozenset()) -> None:
    """Let every value position under `node` also take a `${param:}` token, in place (but not the `skip` fields).

    A value position is a property, an array's items or a map's values; the branches of an `anyOf` are walked, not
    wrapped, so a nullable field gets one token branch, not one per branch. A `$ref` is left alone: the definition it
    names is rewritten itself.
    """
    for container in ("properties", "patternProperties"):
        for key, child in list(node.get(container, {}).items()):
            if isinstance(child, dict) and key not in skip:
                _accept_param_tokens(child)
                node[container][key] = _with_param_token(child)
    for key in ("items", "additionalProperties"):
        child = node.get(key)
        if isinstance(child, dict):
            _accept_param_tokens(child)
            node[key] = _with_param_token(child)
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in node.get(key, ()):
            if isinstance(branch, dict):
                _accept_param_tokens(branch)


def _accept_templates(schema: dict[str, Any], kind: PlatformKind) -> None:
    """For a kind whose spec may be a template, every field of the spec, at any depth, also takes `${param:}`.

    The YAML editor then accepts what `strata validate` accepts: `tenant: ${param:spoke.tenant}` in a deployment.
    `meta` and the never-templated spec fields (`extends`, `partial`) keep their schema, so a token there is still
    underlined, as `strata validate` rejects it.
    """
    template = _TEMPLATE_SPECS.get(kind)
    if template is None:
        return
    spec_name, never = template
    definitions = schema.setdefault("$defs", {})
    for name, definition in list(definitions.items()):
        if name.endswith("MetaModel") or not isinstance(definition, dict):
            continue
        _accept_param_tokens(definition, never if name == spec_name else frozenset())
    definitions[_PARAM_TOKEN_DEF] = copy.deepcopy(_PARAM_TOKEN_SCHEMA)


def export_schema(model_cls: type[BaseModel], kind: PlatformKind) -> dict[str, Any]:
    """The schema written to `.strata/schemas/<kind>.json`."""
    schema = render_schema(model_cls)
    _accept_templates(schema, kind)
    examples = EXAMPLES.get(kind)
    if examples:
        schema["examples"] = copy.deepcopy(examples)
    return schema
