"""A signed-in request without a valid CSRF token gets its own code (no database)."""

import pytest
from django.test import RequestFactory

from apps.accounts.auth import session_auth
from apps.accounts.models import User
from apps.common.errors import ApiError


def test_a_missing_csrf_token_is_csrf_failed() -> None:
    request = RequestFactory().post("/api/v1/auth/logout")
    request.user = User(email="a@example.nl", name="A")
    request._dont_enforce_csrf_checks = False  # type: ignore[attr-defined]

    with pytest.raises(ApiError) as excinfo:
        session_auth(request)

    assert (excinfo.value.status, excinfo.value.code) == (403, "csrf_failed")
