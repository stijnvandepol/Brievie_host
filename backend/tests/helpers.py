"""Typed helpers for API tests."""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from django.test import Client

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse

    # The response type of Django's test client (only known to the type checker).
    type Response = _MonkeyPatchedWSGIResponse

SETUP_TOKEN = "test-installatiecode"


def post_json(
    client: Client,
    path: str,
    body: Mapping[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> "Response":
    """POST `body` as JSON to /api/v1<path>; no body at all when `body` is None."""
    if body is None:
        return client.post(f"/api/v1{path}", content_type="application/json", headers=headers)
    return client.post(f"/api/v1{path}", body, content_type="application/json", headers=headers)
