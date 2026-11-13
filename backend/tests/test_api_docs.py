from django.test import Client

from brievie.api import api, docs_urls


def test_docs_are_not_served_without_debug(client: Client) -> None:
    assert api.docs_url is None
    assert client.get("/api/v1/docs").status_code == 404
    assert client.get("/api/v1/openapi.json").status_code == 404


def test_docs_are_served_only_in_debug() -> None:
    assert docs_urls(debug=True) == {"docs_url": "/docs", "openapi_url": "/openapi.json"}
    assert docs_urls(debug=False) == {"docs_url": None, "openapi_url": None}
