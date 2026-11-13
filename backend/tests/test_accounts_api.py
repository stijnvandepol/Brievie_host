import threading
from typing import Any

import pytest
from django.db import connection
from django.test import Client

from apps.accounts.models import User
from apps.workspaces.models import Membership, Workspace
from tests.factories import TEST_PASSWORD, UserFactory

SETUP_BODY = {
    "email": "Beheer@Example.nl",
    "password": "een-lang-wachtwoord-2026",
    "name": "Anna de Vries",
    "workspace_name": "Thuis",
    "workspace_kind": "home",
}


def post(client: Client, path: str, body: dict[str, Any]) -> Any:  # noqa: ANN401
    return client.post(f"/api/v1{path}", body, content_type="application/json")


def test_setup_status_before_and_after_setup(client: Client) -> None:
    assert client.get("/api/v1/setup").json() == {"needs_setup": True}
    UserFactory.create()
    assert client.get("/api/v1/setup").json() == {"needs_setup": False}


def test_setup_creates_admin_workspace_and_logs_in(client: Client) -> None:
    r = post(client, "/setup", SETUP_BODY)

    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "beheer@example.nl"
    assert body["user"]["name"] == "Anna de Vries"
    assert body["workspace"]["name"] == "Thuis"
    assert body["workspace"]["kind"] == "home"
    assert body["workspace"]["role"] == "admin"
    user = User.objects.get()
    workspace = Workspace.objects.get()
    assert Membership.objects.get(user=user, workspace=workspace).role == "admin"
    assert user.check_password(SETUP_BODY["password"])
    # The setup response carries a session: the next call is authenticated.
    assert client.get("/api/v1/me").json()["email"] == "beheer@example.nl"


def test_second_setup_is_refused_and_creates_nothing(client: Client) -> None:
    assert post(client, "/setup", SETUP_BODY).status_code == 201

    r = post(Client(), "/setup", {**SETUP_BODY, "email": "ander@example.nl"})

    assert r.status_code == 409
    assert r.json()["code"] == "already_set_up"
    assert User.objects.count() == 1
    assert Workspace.objects.count() == 1


def test_setup_rejects_a_short_password(client: Client) -> None:
    r = post(client, "/setup", {**SETUP_BODY, "password": "kort-123"})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert User.objects.count() == 0


def test_setup_rejects_an_invalid_email(client: Client) -> None:
    r = post(client, "/setup", {**SETUP_BODY, "email": "geen-adres"})

    assert r.status_code == 422
    assert User.objects.count() == 0


def test_setup_rejects_an_unknown_workspace_kind(client: Client) -> None:
    r = post(client, "/setup", {**SETUP_BODY, "workspace_kind": "club"})

    assert r.status_code == 422
    assert User.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_concurrent_setup_creates_exactly_one_user() -> None:  # Review Focus 6
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def run(email: str) -> None:
        try:
            barrier.wait(timeout=10)
            r = post(Client(), "/setup", {**SETUP_BODY, "email": email})
            statuses.append(r.status_code)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=run, args=(email,)) for email in ("a@example.nl", "b@example.nl")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(statuses) == [201, 409]
    assert User.objects.count() == 1
    assert Workspace.objects.count() == 1


def test_login_success_returns_the_user_and_a_session(client: Client) -> None:
    user = UserFactory.create(email="jan@example.nl")

    r = post(client, "/auth/login", {"email": "Jan@Example.nl", "password": TEST_PASSWORD})

    assert r.status_code == 200
    assert r.json() == {
        "id": str(user.id),
        "email": "jan@example.nl",
        "name": user.name,
        "language": "nl",
        "explanation_language": "nl",
    }
    assert client.get("/api/v1/me").status_code == 200


def test_login_with_a_wrong_password_is_401(client: Client) -> None:
    UserFactory.create(email="jan@example.nl")

    r = post(client, "/auth/login", {"email": "jan@example.nl", "password": "fout-wachtwoord"})

    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"
    assert client.get("/api/v1/me").status_code == 401


def test_login_with_an_unknown_email_is_401(client: Client) -> None:
    r = post(client, "/auth/login", {"email": "niemand@example.nl", "password": TEST_PASSWORD})

    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


def test_sixth_wrong_attempt_is_locked_out(client: Client) -> None:
    UserFactory.create(email="jan@example.nl")
    wrong = {"email": "jan@example.nl", "password": "fout-wachtwoord"}

    for _ in range(5):
        assert post(client, "/auth/login", wrong).status_code == 401
    r = post(client, "/auth/login", wrong)

    assert r.status_code == 429
    assert r.json()["code"] == "too_many_attempts"
    assert r["Retry-After"] == "900"
    # Locked out: the right password does not get in either.
    right = {"email": "jan@example.nl", "password": TEST_PASSWORD}
    assert post(client, "/auth/login", right).status_code == 429


def test_lockout_ignores_the_case_of_the_email(client: Client) -> None:
    UserFactory.create(email="jan@example.nl")
    for email in (
        "jan@example.nl",
        "JAN@example.nl",
        "Jan@Example.NL",
        "jaN@example.nl",
        "JAN@EXAMPLE.NL",
    ):
        assert post(client, "/auth/login", {"email": email, "password": "fout"}).status_code == 401

    r = post(client, "/auth/login", {"email": "jan@example.nl", "password": TEST_PASSWORD})

    assert r.status_code == 429


def test_lockout_is_per_email_and_ip(client: Client) -> None:
    UserFactory.create(email="jan@example.nl")
    other = UserFactory.create(email="piet@example.nl")
    for _ in range(5):
        post(client, "/auth/login", {"email": "jan@example.nl", "password": "fout"})

    # Same IP, other email: not locked.
    r = post(client, "/auth/login", {"email": other.email, "password": TEST_PASSWORD})
    assert r.status_code == 200
    # Same email, other IP: not locked.
    r = Client(REMOTE_ADDR="192.0.2.7").post(
        "/api/v1/auth/login",
        {"email": "jan@example.nl", "password": TEST_PASSWORD},
        content_type="application/json",
    )
    assert r.status_code == 200


def test_logout_ends_the_session(client: Client) -> None:
    client.force_login(UserFactory.create())

    r = client.post("/api/v1/auth/logout")

    assert r.status_code == 204
    assert client.get("/api/v1/me").status_code == 401


def test_me_returns_the_caller(client: Client) -> None:
    user = UserFactory.create()
    client.force_login(user)

    r = client.get("/api/v1/me")

    assert r.status_code == 200
    assert r.json()["id"] == str(user.id)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/me"),
        ("post", "/auth/logout"),
        ("get", "/workspaces"),
        ("get", "/workspaces/00000000-0000-0000-0000-000000000000/folders"),
        ("post", "/workspaces/00000000-0000-0000-0000-000000000000/folders"),
    ],
)
def test_unauthenticated_calls_are_401(client: Client, method: str, path: str) -> None:
    if method == "get":
        r = client.get(f"/api/v1{path}")
    else:
        r = client.post(f"/api/v1{path}", {"name": "X"}, content_type="application/json")

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


def test_session_calls_require_csrf() -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(UserFactory.create())

    r = client.post("/api/v1/auth/logout")

    assert r.status_code == 403
    assert set(r.json()) == {"code", "message"}
