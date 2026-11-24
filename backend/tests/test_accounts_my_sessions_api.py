import uuid

import pytest
from django.contrib.sessions.models import Session
from django.test import Client

from apps.accounts.models import User, UserSession
from apps.audit.models import ServerAuditEvent
from apps.devices.services import create_pairing_code, pair_device
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json

FIREFOX = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"


def signed_in(user: User, *, agent: str = "", address: str = "127.0.0.1") -> Client:
    client = Client(HTTP_USER_AGENT=agent, REMOTE_ADDR=address)
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert r.status_code == 200
    return client


def row_of(client: Client) -> UserSession:
    return UserSession.objects.get(session_key=client.session.session_key)


def test_list_my_sessions_marks_the_current_one() -> None:
    user = UserFactory.create()
    mine = signed_in(user, agent=FIREFOX, address="192.0.2.7")
    other = signed_in(user)
    stranger = signed_in(UserFactory.create())

    r = mine.get("/api/v1/me/sessions")

    assert r.status_code == 200
    body = r.json()
    assert {s["id"] for s in body} == {str(row_of(mine).id), str(row_of(other).id)}
    assert str(row_of(stranger).id) not in {s["id"] for s in body}
    (current,) = [s for s in body if s["current"]]
    assert current["id"] == str(row_of(mine).id)
    assert current["browser"] == "Firefox op Windows"
    assert current["client_ip"] == "192.0.2.7"
    assert current["method"] == "password"
    assert set(current) == {
        "id",
        "method",
        "started_at",
        "last_used_at",
        "client_ip",
        "browser",
        "current",
        "second_factor",
    }
    assert current["second_factor"] is False


def test_ending_another_session_signs_that_browser_out() -> None:
    user = UserFactory.create()
    mine = signed_in(user)
    other = signed_in(user)
    other_row = row_of(other)

    r = mine.delete(f"/api/v1/me/sessions/{other_row.id}")

    assert r.status_code == 204
    assert other.get("/api/v1/me").status_code == 401
    assert not UserSession.objects.filter(id=other_row.id).exists()
    assert not Session.objects.filter(session_key=other_row.session_key).exists()
    assert mine.get("/api/v1/me").status_code == 200


def test_ending_my_current_session_answers_204_and_signs_me_out() -> None:
    user = UserFactory.create()
    mine = signed_in(user)
    key = mine.session.session_key

    r = mine.delete(f"/api/v1/me/sessions/{row_of(mine).id}")

    assert r.status_code == 204
    assert mine.get("/api/v1/me").status_code == 401
    assert not UserSession.objects.filter(session_key=key).exists()
    assert not Session.objects.filter(session_key=key).exists()


def test_ending_a_session_is_logged() -> None:
    user = UserFactory.create()
    mine = signed_in(user)
    other = signed_in(user)
    other_id = row_of(other).id

    assert mine.delete(f"/api/v1/me/sessions/{other_id}").status_code == 204

    (event,) = ServerAuditEvent.objects.filter(code="session_ended")
    assert (event.actor_kind, event.actor_id) == ("user", user.id)
    assert (event.target_kind, event.target_id, event.target_name) == ("session", other_id, "")
    assert event.values == {"count": 1, "reason": "self"}


def test_ending_somebody_elses_session_is_404() -> None:
    mine = signed_in(UserFactory.create())
    theirs = signed_in(UserFactory.create())
    their_row = row_of(theirs)

    r = mine.delete(f"/api/v1/me/sessions/{their_row.id}")

    assert r.status_code == 404
    assert r.json()["code"] == "session_not_found"
    assert UserSession.objects.filter(id=their_row.id).exists()
    assert theirs.get("/api/v1/me").status_code == 200
    assert not ServerAuditEvent.objects.filter(code="session_ended").exists()


def test_ending_an_unknown_session_is_404() -> None:
    mine = signed_in(UserFactory.create())

    r = mine.delete(f"/api/v1/me/sessions/{uuid.uuid4()}")

    assert r.status_code == 404


def test_my_sessions_refuse_a_bearer_token() -> None:
    user = UserFactory.create()
    _, code = create_pairing_code(user)
    _, token = pair_device(code=code, device_name="iPhone")
    headers = {"Authorization": f"Bearer {token}"}

    r = Client().get("/api/v1/me/sessions", headers=headers)

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


def test_my_sessions_need_a_csrf_token() -> None:
    user = UserFactory.create()
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    r = client.delete(f"/api/v1/me/sessions/{row_of(client).id}")

    assert r.status_code == 403
    assert UserSession.objects.filter(user=user).exists()


@pytest.mark.usefixtures("without_test_session_receiver")
def test_a_new_session_was_not_used_before_it_started() -> None:
    # The finding: last_used_at lay 1-2 ms before started_at. Recorded as in production
    # (one insert): the test run's own receiver would insert first and hide it.
    client = signed_in(UserFactory.create())

    row = row_of(client)
    (listed,) = client.get("/api/v1/me/sessions").json()

    assert row.last_used_at == row.created_at
    assert listed["last_used_at"] == listed["started_at"]
