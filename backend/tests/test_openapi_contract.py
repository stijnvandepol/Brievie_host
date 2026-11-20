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


Contract = dict[str, dict[str, dict[str, object]]]

FIXTURES = Path(__file__).parent / "fixtures"
# What each released app was built against: an app of 1b and one of 1c must both keep
# working against this server.
RELEASED = {"1b": FIXTURES / "openapi_1b.json", "1c": FIXTURES / "openapi_1c.json"}


def read_contract(path: Path) -> Contract:
    contract: Contract = json.loads(path.read_text(encoding="utf-8"))
    return contract


@pytest.fixture(params=list(RELEASED))
def released_contract(request: pytest.FixtureRequest) -> Contract:
    version: str = request.param
    return read_contract(RELEASED[version])


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


def test_released_schemas_still_exist(released_contract: Contract) -> None:
    released = released_contract["components"]["schemas"]
    current = current_contract()["components"]["schemas"]
    missing = sorted(set(released) - set(current))
    assert missing == [], f"schemas of the released contract are gone: {missing}"


def test_released_operations_still_exist_with_the_same_operation_id(
    released_contract: Contract,
) -> None:
    released = contract_operations(released_contract)
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


def success_codes(operation: dict[str, object]) -> set[str]:
    responses = operation["responses"]
    assert isinstance(responses, dict)
    return {code for code in responses if code.startswith("2")}


def test_released_operations_keep_their_auth_and_success_codes(
    released_contract: Contract,
) -> None:
    """An app in the field calls these with the auth it was built for: narrowing one to
    the web session only, or dropping a success status, breaks it while CI stays green."""
    current = contract_operations(current_contract())
    for key, operation in contract_operations(released_contract).items():
        now = current[key]
        operation_id = operation["operationId"]
        assert now.get("security") == operation.get("security"), operation_id
        assert success_codes(operation) <= success_codes(now), operation_id


def test_released_operations_gain_no_required_parameters(released_contract: Contract) -> None:
    released = contract_operations(released_contract)
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


# Keys that document a property but do not change what goes over the wire.
DOCUMENTING_KEYS = frozenset({"title", "description", "default"})

# Request bodies whose schema was renamed on purpose, keeping the same wire shape:
# operationId -> (released schema, current schema). The generated iOS type name changes
# at the next contract sync. (`updateLabel` was released in 1c with `LabelUpdateIn`
# already, so it needs no entry.)
RENAMED_REQUEST_BODIES: dict[str, tuple[str, str]] = {}


def wire_shape(definition: object) -> object:
    """`definition` without the keys that only document it, at every depth."""
    if isinstance(definition, dict):
        return {
            key: wire_shape(value)
            for key, value in definition.items()
            if key not in DOCUMENTING_KEYS
        }
    if isinstance(definition, list):
        return [wire_shape(value) for value in definition]
    return definition


def request_body_refs(operation: dict[str, object]) -> dict[str, object]:
    """Content type -> the body schema's `$ref` (or its inline wire shape)."""
    body = operation.get("requestBody", {})
    assert isinstance(body, dict)
    content = body.get("content", {})
    assert isinstance(content, dict)
    refs: dict[str, object] = {}
    for content_type, media in content.items():
        schema = media.get("schema", {})
        refs[content_type] = schema["$ref"] if "$ref" in schema else wire_shape(schema)
    return refs


def test_released_schemas_lose_no_properties(released_contract: Contract) -> None:
    released = released_contract["components"]["schemas"]
    current = current_contract()["components"]["schemas"]
    lost = {
        name: sorted(set(schema_properties(old)) - set(schema_properties(current.get(name))))
        for name, old in released.items()
        if name in current
    }
    lost = {name: props for name, props in lost.items() if props}
    assert lost == {}, f"released schemas lost properties: {lost}"


def test_released_schema_properties_keep_their_definition(released_contract: Contract) -> None:
    """Type, format, $ref, items, enum, ...: all but title, description and default."""
    released = released_contract["components"]["schemas"]
    current = current_contract()["components"]["schemas"]
    changed: dict[str, tuple[object, object]] = {}
    for name, old in released.items():
        new_properties = schema_properties(current.get(name))
        for prop, old_prop in schema_properties(old).items():
            new_prop = new_properties.get(prop)
            if new_prop is not None and wire_shape(old_prop) != wire_shape(new_prop):
                changed[f"{name}.{prop}"] = (wire_shape(old_prop), wire_shape(new_prop))
    assert changed == {}, f"released properties changed their definition (old, new): {changed}"


def test_released_request_bodies_do_not_become_required(released_contract: Contract) -> None:
    released = contract_operations(released_contract)
    current = contract_operations(current_contract())
    became = sorted(
        str(operation["operationId"])
        for key, operation in released.items()
        if key in current and not _body_required(operation) and _body_required(current[key])
    )
    assert became == [], f"released operations' request bodies became required: {became}"


def _body_required(operation: dict[str, object]) -> bool:
    body = operation.get("requestBody")
    return isinstance(body, dict) and bool(body.get("required"))


def test_released_request_bodies_keep_their_schema(released_contract: Contract) -> None:
    released = contract_operations(released_contract)
    current = contract_operations(current_contract())
    changed: dict[str, tuple[object, object]] = {}
    for key, operation in released.items():
        if key not in current:
            continue
        operation_id = str(operation["operationId"])
        new_refs = request_body_refs(current[key])
        for content_type, old_ref in request_body_refs(operation).items():
            new_ref = new_refs.get(content_type)
            if new_ref == old_ref or RENAMED_REQUEST_BODIES.get(operation_id) == (old_ref, new_ref):
                continue
            changed[f"{operation_id} {content_type}"] = (old_ref, new_ref)
    assert changed == {}, f"released request bodies changed their schema (old, new): {changed}"


def test_released_schemas_gain_no_required_properties(released_contract: Contract) -> None:
    """A new app must read an old server: nothing that was optional becomes required."""
    released = released_contract["components"]["schemas"]
    current = current_contract()["components"]["schemas"]
    gained = {
        name: sorted(schema_required(current[name]) - schema_required(old))
        for name, old in released.items()
        if name in current
    }
    assert {name: props for name, props in gained.items() if props} == {}


def test_list_row_proposals_are_optional_primitives() -> None:
    # Additive: an app built before them reads the row as before, and the iOS generator
    # gets plain optionals (no anyOf, no object).
    schemas = current_contract()["components"]["schemas"]
    for name in ("DocumentSummaryOut", "DocumentOut"):
        properties = schema_properties(schemas[name])
        required = schema_required(schemas[name])
        assert properties["proposed_sender"]["type"] == "string"
        assert properties["proposed_document_type"]["type"] == "string"
        assert properties["proposed_letter_date"]["type"] == ["string", "null"]
        assert properties["proposed_letter_date"]["format"] == "date"
        assert not {"proposed_sender", "proposed_document_type", "proposed_letter_date"} & required
