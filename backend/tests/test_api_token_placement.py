"""A secret token never travels in a URL path or query string, where proxies log it."""

from collections.abc import Iterator

from brievie.api import api

HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}


def operations() -> Iterator[tuple[str, dict[str, object]]]:
    schema = api.get_openapi_schema()
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield path, operation


def test_no_operation_takes_a_token_in_path_or_query() -> None:
    offenders: list[str] = []
    for path, operation in operations():
        if "token" in path.lower():
            offenders.append(path)
        parameters = operation.get("parameters", [])
        assert isinstance(parameters, list)
        for parameter in parameters:
            if "token" in str(parameter["name"]).lower():
                offenders.append(f"{operation['operationId']}: {parameter['name']}")

    assert offenders == []


def test_completing_a_reset_takes_the_token_in_the_body() -> None:
    schema = api.get_openapi_schema()
    operation = schema["paths"]["/api/v1/auth/password-reset"]["post"]
    ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    body = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]

    assert "token" in body["properties"]
    assert "token" in body["required"]
