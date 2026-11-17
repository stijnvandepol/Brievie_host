import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.common.management.commands import export_openapi
from apps.common.openapi import normalize_nullable
from brievie.api import api

ERROR_REF = "#/components/schemas/ErrorOut"
CAMEL_CASE = re.compile(r"^[a-z][a-zA-Z0-9]*$")
# No failure mode besides a server error (ruling); every other operation documents its errors.
NO_ERROR_RESPONSES = {"getHealth", "getSetupStatus"}
BODY_ERRORS = {"400", "413", "415", "422"}
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}


def operations() -> Iterator[dict[str, object]]:
    schema = api.get_openapi_schema()
    for path_item in schema["paths"].values():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield operation


def test_committed_schema_equals_the_generated_one() -> None:
    committed = export_openapi.OUTPUT.read_text(encoding="utf-8")
    assert committed == export_openapi.render_schema(), "run `make openapi`"


def test_every_operation_has_a_camel_case_operation_id() -> None:
    ids = [op.get("operationId") for op in operations()]
    assert ids
    for operation_id in ids:
        assert isinstance(operation_id, str)
        assert CAMEL_CASE.match(operation_id), operation_id
    assert len(ids) == len(set(ids))


def test_every_operation_documents_an_error_response() -> None:
    for op in operations():
        if op["operationId"] in NO_ERROR_RESPONSES:
            continue
        responses = op["responses"]
        assert isinstance(responses, dict)
        error_refs = [
            body["content"]["application/json"]["schema"].get("$ref")
            for status, body in responses.items()
            if int(status) >= 400
        ]
        assert error_refs, f"{op['operationId']} documents no error response"
        assert all(ref == ERROR_REF for ref in error_refs), op["operationId"]


def test_every_operation_with_a_body_documents_body_errors() -> None:
    with_body = [op for op in operations() if "requestBody" in op]
    assert with_body
    for op in with_body:
        responses = op["responses"]
        assert isinstance(responses, dict)
        assert {str(status) for status in responses} >= BODY_ERRORS, op["operationId"]


def test_no_operation_documents_a_500() -> None:
    for op in operations():
        responses = op["responses"]
        assert isinstance(responses, dict)
        assert "500" not in {str(status) for status in responses}, op["operationId"]


def test_version_is_1_0_0() -> None:
    assert api.get_openapi_schema()["info"]["version"] == "1.0.0"


def test_rendered_schema_has_sorted_keys_and_two_space_indent() -> None:
    rendered = export_openapi.render_schema()
    data = json.loads(rendered)
    assert rendered == json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_check_fails_when_the_file_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr(export_openapi, "OUTPUT", target)

    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CommandError) as excinfo:
        call_command("export_openapi", "--check")
    assert excinfo.value.returncode == 1

    call_command("export_openapi")
    assert target.read_text(encoding="utf-8") == export_openapi.render_schema()
    call_command("export_openapi", "--check")


def test_check_fails_when_the_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_openapi, "OUTPUT", tmp_path / "missing.json")

    with pytest.raises(CommandError):
        call_command("export_openapi", "--check")


def test_contract_has_no_anyof() -> None:
    # swift-openapi-generator skips `anyOf: [X, null]` properties.
    assert '"anyOf"' not in export_openapi.render_schema()


def test_nullable_primitive_becomes_type_array() -> None:
    out = normalize_nullable(
        {
            "properties": {
                "p": {
                    "anyOf": [{"type": "string", "format": "uuid"}, {"type": "null"}],
                    "title": "P",
                }
            }
        }
    )
    assert out == {
        "properties": {"p": {"type": ["string", "null"], "format": "uuid", "title": "P"}}
    }


def test_normalize_nullable_does_not_change_its_input() -> None:
    schema: dict[str, object] = {"p": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}
    normalize_nullable(schema)
    assert schema == {"p": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}


def test_nullable_ref_is_refused() -> None:
    schema: dict[str, object] = {
        "properties": {"p": {"anyOf": [{"$ref": "#/components/schemas/X"}, {"type": "null"}]}}
    }
    with pytest.raises(ValueError, match="/properties/p"):
        normalize_nullable(schema)


def test_folder_parent_id_is_nullable_uuid() -> None:
    schemas = json.loads(export_openapi.render_schema())["components"]["schemas"]
    parent_id = schemas["FolderOut"]["properties"]["parent_id"]
    assert parent_id["type"] == ["string", "null"]
    assert parent_id["format"] == "uuid"


RELEASED = Path(__file__).parent / "fixtures" / "openapi_1b.json"


def released_contract() -> dict[str, dict[str, dict[str, object]]]:
    contract: dict[str, dict[str, dict[str, object]]] = json.loads(
        RELEASED.read_text(encoding="utf-8")
    )
    return contract


def current_contract() -> dict[str, dict[str, dict[str, object]]]:
    contract: dict[str, dict[str, dict[str, object]]] = json.loads(export_openapi.render_schema())
    return contract


def contract_operations(
    contract: dict[str, dict[str, dict[str, object]]],
) -> dict[tuple[str, str], dict[str, object]]:
    """(path, method) -> operation."""
    found: dict[tuple[str, str], dict[str, object]] = {}
    for path, item in contract["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS and isinstance(operation, dict):
                found[(path, method)] = operation
    return found


def required_parameters(operation: dict[str, object]) -> set[tuple[str, str]]:
    """(in, name) of the operation's required parameters."""
    parameters = operation.get("parameters", [])
    assert isinstance(parameters, list)
    return {(p["in"], p["name"]) for p in parameters if p.get("required")}


def schema_properties(schema: object) -> dict[str, dict[str, object]]:
    assert schema is None or isinstance(schema, dict)
    properties = (schema or {}).get("properties", {})
    assert isinstance(properties, dict)
    return properties


def schema_required(schema: object) -> set[str]:
    assert isinstance(schema, dict)
    required = schema.get("required", [])
    assert isinstance(required, list)
    return set(required)


def test_released_schemas_still_exist() -> None:
    released = released_contract()["components"]["schemas"]
    current = current_contract()["components"]["schemas"]
    missing = sorted(set(released) - set(current))
    assert missing == [], f"schemas of the released contract are gone: {missing}"


def test_released_operations_still_exist_with_the_same_operation_id() -> None:
    released = contract_operations(released_contract())
    current = contract_operations(current_contract())
    missing = sorted(f"{method.upper()} {path}" for path, method in set(released) - set(current))
    assert missing == [], f"operations of the released contract are gone: {missing}"
    renamed = {
        f"{method.upper()} {path}": (
            operation["operationId"],
            current[(path, method)]["operationId"],
        )
        for (path, method), operation in released.items()
        if operation["operationId"] != current[(path, method)]["operationId"]
    }
    assert renamed == {}, f"released operations changed their operationId (old, new): {renamed}"


def test_released_operations_gain_no_required_parameters() -> None:
    released = contract_operations(released_contract())
    current = contract_operations(current_contract())
    gained = {
        str(operation["operationId"]): sorted(
            required_parameters(current[key]) - required_parameters(operation)
        )
        for key, operation in released.items()
        if key in current
    }
    gained = {name: params for name, params in gained.items() if params}
    assert gained == {}, f"released operations gained required parameters (in, name): {gained}"


def test_released_schema_properties_keep_their_type() -> None:
    released = released_contract()["components"]["schemas"]
    current = current_contract()["components"]["schemas"]
    changed: dict[str, tuple[object, object]] = {}
    for name, old in released.items():
        new_properties = schema_properties(current.get(name))
        for prop, old_prop in schema_properties(old).items():
            new_prop = new_properties.get(prop)
            if new_prop is not None and old_prop.get("type") != new_prop.get("type"):
                changed[f"{name}.{prop}"] = (old_prop.get("type"), new_prop.get("type"))
    assert changed == {}, f"released properties changed their type (old, new): {changed}"


def test_released_schemas_gain_no_required_properties() -> None:
    """A new app must read an old server: nothing that was optional becomes required."""
    released = released_contract()["components"]["schemas"]
    current = current_contract()["components"]["schemas"]
    gained = {
        name: sorted(schema_required(current[name]) - schema_required(old))
        for name, old in released.items()
        if name in current
    }
    assert {name: props for name, props in gained.items() if props} == {}
