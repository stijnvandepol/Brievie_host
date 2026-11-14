"""A body without Content-Length (chunked) reaches Django empty; the API says so with 411."""

from django.test import Client

from apps.accounts.models import User


def test_chunked_body_is_411() -> None:
    r = Client().generic(
        "POST",
        "/api/v1/auth/login",
        b'{"email": "jan@example.nl", "password": "x"}',
        content_type="application/json",
        HTTP_TRANSFER_ENCODING="chunked",
        CONTENT_LENGTH="",
    )

    assert r.status_code == 411
    assert r.json()["code"] == "length_required"
    assert set(r.json()) == {"code", "message"}


def test_chunked_body_outside_the_api_is_not_touched() -> None:
    r = Client().generic(
        "POST", "/koppel-app", b"x", HTTP_TRANSFER_ENCODING="chunked", CONTENT_LENGTH=""
    )

    assert r.status_code != 411


def test_body_with_a_length_still_reaches_the_view() -> None:
    r = Client().post(
        "/api/v1/auth/login",
        {"email": "jan@example.nl", "password": "x"},
        content_type="application/json",
    )

    assert r.status_code == 401
    assert not User.objects.exists()
