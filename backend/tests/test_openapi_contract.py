import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.common.management.commands import export_openapi
from brievie.api import api

ERROR_REF = "#/components/schemas/ErrorOut"
CAMEL_CASE = re.compile(r"^[a-z][a-zA-Z0-9]*$")
# getHealth has no failure mode (ruling); every other operation documents its errors.
NO_ERROR_RESPONSES = {"getHealth"}
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
