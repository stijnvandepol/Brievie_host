import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from apps.accounts.models import AccountNotice, User, UserSession
from apps.accounts.notices import can_dismiss, notice_message, notify
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def signed_in(user: User) -> Client:
    client = Client()
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert r.status_code == 200
    return client


def notice_on(user: User, code: str, when: datetime, **values: str) -> AccountNotice:
    notify(user, code, actor_name="Anne", values=values)
    notice = AccountNotice.objects.filter(user=user).latest("created_at")
    AccountNotice.objects.filter(pk=notice.pk).update(created_at=when)
    notice.refresh_from_db()
    return notice


def seen(client: Client, ids: list[str]) -> int:
    return post_json(client, "/me/notices/seen", {"notice_ids": ids}).status_code


def test_notices_are_listed_once_and_marked_seen() -> None:
    user = UserFactory.create()
    client = signed_in(user)
    older = notice_on(user, "sessions_ended", datetime(2026, 5, 2, 12, tzinfo=AMSTERDAM))
    newer = notice_on(
        user, "device_revoked", datetime(2026, 5, 3, 12, tzinfo=AMSTERDAM), device="iPhone"
    )

    body = client.get("/api/v1/me/notices").json()

    assert [n["id"] for n in body] == [str(older.id), str(newer.id)]
    assert body[1] == {
        "id": str(newer.id),
        "code": "device_revoked",
        "message": "Anne heeft op 3 mei je apparaat \u2018iPhone\u2019 ingetrokken.",
        "created_at": body[1]["created_at"],
        "can_dismiss": True,
    }
    assert seen(client, [str(older.id), str(newer.id)]) == 204
    assert client.get("/api/v1/me/notices").json() == []


def test_notice_message_reads_like_the_design() -> None:
    user = UserFactory.create()
    notice = notice_on(user, "password_reset_completed", datetime(2026, 5, 3, 9, tzinfo=AMSTERDAM))

    assert notice_message(notice) == "Je wachtwoord is op 3 mei opnieuw ingesteld door Anne."


def test_the_date_is_the_day_in_amsterdam() -> None:
    user = UserFactory.create()
    # 23:30 UTC on 2 May is 01:30 on 3 May in Amsterdam (summer time).
    when = datetime(2026, 5, 2, 23, 30, tzinfo=ZoneInfo("UTC"))
    notice = notice_on(user, "sessions_ended", when)

    assert notice_message(notice) == "Anne heeft op 3 mei je sessies beëindigd."


@pytest.mark.parametrize(
    ("code", "values", "text"),
    [
        (
            "password_reset_created",
            {},
            "Anne heeft op 3 mei een herstellink voor je wachtwoord gemaakt.",
        ),
        (
            "emergency_access_switched_on",
            {"workspace": "Thuis"},
            "Anne heeft op 3 mei noodtoegang voor de beheerder aangezet in Thuis.",
        ),
        (
            "emergency_access_used",
            {"count": "2", "person": "Bram"},
            "Anne heeft op 3 mei met noodtoegang 2 privé-brieven van Bram geopend. "
            "De reden staat bij elke brief.",
        ),
    ],
)
def test_every_notice_has_a_sentence(code: str, values: dict[str, str], text: str) -> None:
    notice = notice_on(
        UserFactory.create(), code, datetime(2026, 5, 3, 12, tzinfo=AMSTERDAM), **values
    )

    assert notice_message(notice) == text


def test_every_code_has_a_message() -> None:
    user = UserFactory.create()
    for code in AccountNotice.Code.values:
        notify(user, code, actor_name="Anne")
    for notice in AccountNotice.objects.filter(user=user):
        assert notice_message(notice)


def test_notify_refuses_an_unknown_code() -> None:
    with pytest.raises(ValueError, match="notice"):
        notify(UserFactory.create(), "something_else", actor_name="Anne")


def test_marking_another_users_notice_changes_nothing() -> None:
    anne = UserFactory.create()
    bram = UserFactory.create()
    bram_notice = notice_on(bram, "sessions_ended", datetime(2026, 5, 3, tzinfo=AMSTERDAM))
    anne_client = signed_in(anne)

    assert anne_client.get("/api/v1/me/notices").json() == []
    assert seen(anne_client, [str(bram_notice.id)]) == 204

    bram_notice.refresh_from_db()
    assert bram_notice.seen_at is None
    assert [n["id"] for n in signed_in(bram).get("/api/v1/me/notices").json()] == [
        str(bram_notice.id)
    ]


def test_marking_an_unknown_notice_is_harmless() -> None:
    client = signed_in(UserFactory.create())

    assert seen(client, [str(uuid.uuid4())]) == 204


def test_marking_takes_at_most_50_notices() -> None:
    client = signed_in(UserFactory.create())

    assert seen(client, [str(uuid.uuid4()) for _ in range(51)]) == 422


def test_a_session_made_by_a_reset_cannot_dismiss_the_reset_notice() -> None:
    user = UserFactory.create()
    client = signed_in(user)
    UserSession.objects.filter(session_key=client.session.session_key).update(created_via="reset")
    notice = notice_on(user, "password_reset_completed", datetime(2026, 5, 3, tzinfo=AMSTERDAM))
    other = notice_on(user, "sessions_ended", datetime(2026, 5, 4, tzinfo=AMSTERDAM))

    body = client.get("/api/v1/me/notices").json()
    assert [n["can_dismiss"] for n in body] == [False, True]
    assert seen(client, [str(notice.id), str(other.id)]) == 204
    notice.refresh_from_db()
    other.refresh_from_db()
    assert notice.seen_at is None
    assert other.seen_at is not None

    # After a fresh sign-in with the password, the same notice can be dismissed.
    fresh = signed_in(user)
    assert [n["can_dismiss"] for n in fresh.get("/api/v1/me/notices").json()] == [True]
    assert seen(fresh, [str(notice.id)]) == 204
    notice.refresh_from_db()
    assert notice.seen_at is not None


def test_can_dismiss_without_a_session_record() -> None:
    notice = notice_on(
        UserFactory.create(), "password_reset_completed", datetime(2026, 5, 3, tzinfo=AMSTERDAM)
    )

    assert can_dismiss(notice, None) is True


def test_notices_refuse_a_bearer_token() -> None:
    from apps.devices.services import create_pairing_code, pair_device

    _, code = create_pairing_code(UserFactory.create())
    _, token = pair_device(code=code, device_name="iPhone")

    r = Client().get("/api/v1/me/notices", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 401
