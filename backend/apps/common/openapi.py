"""Post-processing of the exported OpenAPI schema (the contract with Brievie_ios)."""

from typing import cast

_PRIMITIVES = {"string", "integer", "number", "boolean"}
_NULL = {"type": "null"}


def normalize_nullable(schema: dict[str, object]) -> dict[str, object]:
    """A copy of `schema` in which a nullable primitive is a type array.

    Pydantic writes `X | None` as `anyOf: [X, {"type": "null"}]`, and
    swift-openapi-generator skips such properties. `type: [X, "null"]` means the
    same in OpenAPI 3.1 and becomes an optional in the generated client. A nullable
    object or `$ref` cannot be written that way and is refused: design the field
    differently (leave it out, or use an empty list).
    """
    return cast(dict[str, object], _normalize(schema, ""))


def _normalize(node: object, pointer: str) -> object:
    if isinstance(node, list):
        return [_normalize(item, f"{pointer}/{index}") for index, item in enumerate(node)]
    if not isinstance(node, dict):
        return node
    if "anyOf" in node:
        node = _merge_nullable(node, pointer)
    return {key: _normalize(value, f"{pointer}/{key}") for key, value in node.items()}


def _merge_nullable(node: dict[str, object], pointer: str) -> dict[str, object]:
    options = node["anyOf"]
    if not isinstance(options, list) or _NULL not in options:
        return node
    others = [option for option in options if option != _NULL]
    if (
        len(others) != 1
        or not isinstance(others[0], dict)
        or others[0].get("type") not in _PRIMITIVES
    ):
        raise ValueError(f"Only a primitive can be nullable in the contract: {pointer}")
    primitive = dict(others[0])
    primitive["type"] = [primitive["type"], "null"]
    rest = {key: value for key, value in node.items() if key != "anyOf"}
    return {**primitive, **rest}
