"""Post-processing of the exported OpenAPI schema (the contract with Brievie_ios)."""

from typing import cast

from pydantic import BaseModel

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


def add_component_schemas(
    schema: dict[str, object], models: list[type[BaseModel]]
) -> dict[str, object]:
    """A copy of `schema` with `models` (and what they refer to) under components.schemas.

    For types the contract needs but no operation names: the JSON inside a multipart
    field. Clients generate them like any other schema.
    """
    result = dict(schema)
    components = dict(cast(dict[str, object], result.get("components", {})))
    schemas = dict(cast(dict[str, object], components.get("schemas", {})))
    for model in models:
        definition = model.model_json_schema(ref_template="#/components/schemas/{model}")
        nested = cast(dict[str, object], definition.pop("$defs", {}))
        schemas.update(nested)
        schemas[model.__name__] = definition
    components["schemas"] = schemas
    result["components"] = components
    return result
