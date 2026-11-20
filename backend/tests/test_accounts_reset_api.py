"""Reset links (D 6.3): made by an admin, used once by the member, within 24 hours."""

import hashlib
import io
import logging
import re
import threading
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import AccountNotice, PasswordReset, User, UserSession
from apps.accounts.resets import complete_password_reset, revoke_resets_made_by
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.devices.models import Device
from apps.devices.services import create_pairing_code, pair_device
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json

NEW_PASSWORD = "een-nieuw-lang-wachtwoord-2026"
DEAD_LINK = {
    "code": "reset_link_invalid",
    "message": "Deze herstellink werkt niet meer. Vraag een nieuwe.",
}
LINK = re.compile(r"^https://brievie\.local/wachtwoord-herstellen#(?P<token>[A-Za-z0-9_-]{43})$")


@pytest.fixture(autouse=True)
def _fast_hashing_and_public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def admin_of(ws: Workspace) -> User:
    return Membership.objects.get(workspace=ws, role=Membership.Role.ADMIN).user


def join(ws: Workspace, role: str = Membership.Role.MEMBER, user: User | None = None) -> User:
    user = user or UserFactory.create()
    Membership.objects.create(user=user, workspace=ws, role=role)
    return user


def signed_in(user: User, password: str = TEST_PASSWORD) -> Client:
    client = Client()
    r = post_json(client, "/auth/login", {"email": user.email, "password": password})
    assert r.status_code == 200
    return client


def make_token(admin_client: Client, target: User) -> str:
    r = post_json(admin_client, f"/users/{target.id}/password-reset")
    assert r.status_code == 201, r.json()
    match = LINK.match(r.json()["link"])
    assert match is not None, r.json()["link"]
    return match["token"]


def complete(
    client: Client, token: str, password: str = NEW_PASSWORD
) -> "tuple[int, dict[str, str]]":
    r = post_json(client, "/auth/password-reset", {"token": token, "password": password})
    return r.status_code, r.json()


def fresh(user: User) -> User:
    return User.objects.get(pk=user.pk)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_reset_link_is_a_fragment_link_and_only_the_hash_is_stored(ws_a: Workspace) -> None:
    member = join(ws_a)
    before = timezone.now()

    r = post_json(signed_in(admin_of(ws_a)), f"/users/{member.id}/password-reset")

    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"link", "expires_at"}
    match = LINK.match(body["link"])
    assert match is not None
    token = match["token"]
    (reset,) = PasswordReset.objects.filter(user=member)
    assert reset.token_hash == sha256(token)
    assert before + timedelta(hours=24) <= reset.expires_at <= timezone.now() + timedelta(hours=24)
    assert (reset.used_at, reset.revoked_at) == (None, None)
    assert (reset.created_by, reset.created_by_name) == (admin_of(ws_a), admin_of(ws_a).name)
    row = PasswordReset.objects.filter(pk=reset.pk).values().get()
    assert token not in {str(value) for value in row.values()}


def test_the_link_uses_the_request_host_without_a_public_url(
    ws_a: Workspace, settings: Settings
) -> None:
    settings.BRIEVIE_PUBLIC_URLS = []
    member = join(ws_a)

    r = post_json(signed_in(admin_of(ws_a)), f"/users/{member.id}/password-reset")

    assert re.match(
        r"^http://testserver/wachtwoord-herstellen#[A-Za-z0-9_-]{43}$", r.json()["link"]
    )


def test_a_new_link_stops_the_older_one(ws_a: Workspace) -> None:
    member = join(ws_a)
    admin = signed_in(admin_of(ws_a))
    older = make_token(admin, member)
    newer = make_token(admin, member)

    assert complete(Client(), older) == (400, DEAD_LINK)
    assert complete(Client(), newer)[0] == 200


def test_an_inactive_account_gets_no_link(ws_a: Workspace) -> None:
    member = join(ws_a, user=UserFactory.create(is_active=False))

    r = post_json(signed_in(admin_of(ws_a)), f"/users/{member.id}/password-reset")

    assert r.status_code == 409
    assert r.json()["code"] == "account_inactive"
    assert not PasswordReset.objects.exists()


def test_completing_sets_the_password_ends_sessions_and_signs_in(ws_a: Workspace) -> None:
    member = join(ws_a)
    old_session = signed_in(member)
    token = make_token(signed_in(admin_of(ws_a)), member)
    browser = Client()

    status, body = complete(browser, token)

    assert status == 200
    assert (body["id"], body["email"]) == (str(member.id), member.email)
    assert old_session.get("/api/v1/me").status_code == 401
    assert browser.get("/api/v1/me").status_code == 200
    (session,) = UserSession.objects.filter(user=member)
    assert session.created_via == UserSession.Via.RESET
    assert session.session_key == browser.session.session_key
    assert fresh(member).check_password(NEW_PASSWORD)
    signed_in(member, NEW_PASSWORD)
    assert PasswordReset.objects.get(user=member).used_at is not None


def test_a_token_with_spaces_around_it_still_works(ws_a: Workspace) -> None:
    member = join(ws_a)
    token = make_token(signed_in(admin_of(ws_a)), member)

    assert complete(Client(), f"  {token} \n")[0] == 200


@pytest.mark.parametrize("state", ["used", "expired", "revoked", "unknown", "inactive_account"])
def test_every_dead_link_answers_the_same_sentence(ws_a: Workspace, state: str) -> None:
    member = join(ws_a)
    token = make_token(signed_in(admin_of(ws_a)), member)
    reset = PasswordReset.objects.get(user=member)
    if state == "used":
        assert complete(Client(), token)[0] == 200
        member = fresh(member)
    elif state == "expired":
        PasswordReset.objects.filter(pk=reset.pk).update(expires_at=timezone.now())
    elif state == "revoked":
        PasswordReset.objects.filter(pk=reset.pk).update(revoked_at=timezone.now())
    elif state == "unknown":
        token = token[::-1]
    else:
        User.objects.filter(pk=member.pk).update(is_active=False)
    password_before = fresh(member).password

    assert complete(Client(), token, "nog-een-ander-wachtwoord-77") == (400, DEAD_LINK)
    assert fresh(member).password == password_before


def test_a_link_works_once(ws_a: Workspace) -> None:
    member = join(ws_a)
    token = make_token(signed_in(admin_of(ws_a)), member)

    assert complete(Client(), token)[0] == 200
    assert complete(Client(), token, "nog-een-ander-wachtwoord-77") == (400, DEAD_LINK)
    assert fresh(member).check_password(NEW_PASSWORD)


@pytest.mark.django_db(transaction=True)
def test_two_connections_completing_at_the_same_moment_get_one_success(ws_a: Workspace) -> None:
    member = join(ws_a)
    token = make_token(signed_in(admin_of(ws_a)), member)
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def run(password: str) -> None:
        try:
            barrier.wait(timeout=10)
            statuses.append(complete(Client(), token, password)[0])
        finally:
            connection.close()

    threads = [
        threading.Thread(target=run, args=(p,))
        for p in ("eerste-lang-wachtwoord-11", "tweede-lang-wachtwoord-22")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(statuses) == [200, 400]
    assert UserSession.objects.filter(user=member, created_via="reset").count() == 1
    assert ServerAuditEvent.objects.filter(code="password_changed").count() == 1


def test_completion_rechecks_the_authority(ws_a: Workspace, ws_b: Workspace) -> None:
    joined_elsewhere = join(ws_a)
    token = make_token(signed_in(admin_of(ws_a)), joined_elsewhere)
    join(ws_b, user=joined_elsewhere)

    assert complete(Client(), token) == (400, DEAD_LINK)
    assert fresh(joined_elsewhere).check_password(TEST_PASSWORD)


def test_completion_fails_when_the_creator_was_demoted(ws_a: Workspace) -> None:
    second_admin = join(ws_a, Membership.Role.ADMIN)
    member = join(ws_a)
    token = make_token(signed_in(second_admin), member)
    Membership.objects.filter(user=second_admin, workspace=ws_a).update(role="member")

    assert complete(Client(), token) == (400, DEAD_LINK)
    assert fresh(member).check_password(TEST_PASSWORD)


def test_completion_fails_when_the_creator_is_gone(ws_a: Workspace) -> None:
    creator = join(ws_a, Membership.Role.ADMIN)
    member = join(ws_a)
    token = make_token(signed_in(creator), member)
    creator.delete()

    assert complete(Client(), token) == (400, DEAD_LINK)
    assert fresh(member).check_password(TEST_PASSWORD)


def test_completion_fails_when_the_target_became_a_server_admin(ws_a: Workspace) -> None:
    member = join(ws_a)
    token = make_token(signed_in(admin_of(ws_a)), member)
    User.objects.filter(pk=member.pk).update(is_server_admin=True)

    assert complete(Client(), token) == (400, DEAD_LINK)
    assert fresh(member).check_password(TEST_PASSWORD)


def test_a_server_admins_link_survives_a_second_membership(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    member = join(ws_a)
    server_admin = UserFactory.create(is_server_admin=True)
    token = make_token(signed_in(server_admin), member)
    join(ws_b, user=member)

    assert complete(Client(), token)[0] == 200


def test_a_weak_password_keeps_the_link_usable(ws_a: Workspace) -> None:
    member = join(ws_a)
    token = make_token(signed_in(admin_of(ws_a)), member)

    status, body = complete(Client(), token, "kort")

    assert (status, body["code"]) == (422, "validation_error")
    assert PasswordReset.objects.get(user=member).used_at is None
    assert fresh(member).check_password(TEST_PASSWORD)
    assert complete(Client(), token)[0] == 200


def test_reset_writes_notices_and_audit_rows(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    member = join(ws_a)
    signed_in(member)
    token = make_token(signed_in(admin), member)
    browser = Client(REMOTE_ADDR="203.0.113.5")

    assert complete(browser, token)[0] == 200

    created, completed = AccountNotice.objects.filter(user=member).order_by("created_at")
    assert (created.code, created.actor_name) == ("password_reset_created", admin.name)
    assert (completed.code, completed.actor_name) == ("password_reset_completed", admin.name)
    notices = browser.get("/api/v1/me/notices").json()
    by_code = {n["code"]: n for n in notices}
    assert by_code["password_reset_completed"]["can_dismiss"] is False
    assert by_code["password_reset_completed"]["message"].endswith(f"door {admin.name}.")
    # The session the reset started cannot hide that it happened.
    ids = [n["id"] for n in notices]
    assert post_json(browser, "/me/notices/seen", {"notice_ids": ids}).status_code == 204
    assert AccountNotice.objects.get(pk=completed.pk).seen_at is None

    (made,) = ServerAuditEvent.objects.filter(code="password_reset_created")
    assert (made.actor_id, made.target_kind, made.target_id) == (admin.id, "user", member.id)
    assert made.values == {}
    # Whoever held the link did it, which nobody proved to be the member: no name, no id,
    # the address only; the maker's id ties this row to the one above.
    (changed,) = ServerAuditEvent.objects.filter(code="password_changed")
    assert (changed.actor_kind, changed.actor_id, changed.actor_name) == ("user", None, "")
    assert changed.client_ip == "203.0.113.5"
    assert (changed.target_id, changed.values) == (
        member.id,
        {"via": "reset", "reset_by": str(admin.id)},
    )
    (ended,) = ServerAuditEvent.objects.filter(code="session_ended")
    assert (ended.actor_id, ended.target_id) == (None, member.id)
    assert ended.values == {"count": 1, "reason": "reset"}
    signed = ServerAuditEvent.objects.filter(code="sign_in_succeeded", target_id=member.id)
    assert {"via": "reset"} in [row.values for row in signed]
    with workspace_context(ws_a.id, actor=SYSTEM):
        (in_workspace,) = WorkspaceAuditEvent.objects.filter(code="password_reset_created")
    assert (in_workspace.actor_id, in_workspace.target_id) == (admin.id, member.id)


def test_a_link_for_a_member_of_two_workspaces_is_in_the_server_log_only(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    member = join(ws_b, user=join(ws_a))
    make_token(signed_in(UserFactory.create(is_server_admin=True)), member)

    assert ServerAuditEvent.objects.filter(code="password_reset_created").count() == 1
    for ws in (ws_a, ws_b):
        with workspace_context(ws.id, actor=SYSTEM):
            assert not WorkspaceAuditEvent.objects.filter(code="password_reset_created").exists()


def test_reset_token_never_logged(ws_a: Workspace, caplog: pytest.LogCaptureFixture) -> None:
    member = join(ws_a)
    caplog.set_level(logging.DEBUG)
    admin = signed_in(admin_of(ws_a))
    token = make_token(admin, member)
    answers = [
        complete(Client(), token, "kort"),
        complete(Client(), token),
        complete(Client(), token),
        complete(Client(), token[::-1]),
    ]

    assert [status for status, _ in answers] == [422, 200, 400, 400]
    for secret in (token, sha256(token)):
        assert secret not in caplog.text
        assert all(secret not in str(body) for _, body in answers)
        assert all(secret not in str(row.values) for row in ServerAuditEvent.objects.all())
        with workspace_context(ws_a.id, actor=SYSTEM):
            assert all(secret not in str(row.values) for row in WorkspaceAuditEvent.objects.all())
        assert all(secret not in str(n.values) for n in AccountNotice.objects.all())


def test_completing_is_rate_limited_per_client_address() -> None:
    statuses = [complete(Client(REMOTE_ADDR="192.0.2.9"), f"onbekend-{i}")[0] for i in range(11)]

    assert statuses[:10] == [400] * 10
    assert statuses[10] == 429
    assert complete(Client(REMOTE_ADDR="192.0.2.10"), "onbekend")[0] == 400


def test_completing_takes_the_token_in_the_body_only() -> None:
    r = post_json(Client(), "/auth/password-reset", {"password": NEW_PASSWORD})

    assert r.status_code == 422
    assert (
        post_json(
            Client(), "/auth/password-reset", {"token": "x" * 101, "password": "y"}
        ).status_code
        == 422
    )


def test_revoke_resets_made_by_stops_only_that_creators_links(ws_a: Workspace) -> None:
    first_admin = admin_of(ws_a)
    second_admin = join(ws_a, Membership.Role.ADMIN)
    first, second, used_member = join(ws_a), join(ws_a), join(ws_a)
    by_second = make_token(signed_in(second_admin), first)
    by_first = make_token(signed_in(first_admin), second)
    used = make_token(signed_in(second_admin), used_member)
    assert complete(Client(), used)[0] == 200

    assert revoke_resets_made_by(second_admin.id) == 1

    assert complete(Client(), by_second) == (400, DEAD_LINK)
    assert complete(Client(), by_first)[0] == 200
    used_row = PasswordReset.objects.get(user=used_member)
    assert used_row.revoked_at is None


def test_reset_password_command_sets_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = UserFactory.create(is_server_admin=True)
    browser = signed_in(admin)
    answers = iter([NEW_PASSWORD, NEW_PASSWORD])
    monkeypatch.setattr(
        "apps.accounts.management.commands.reset_password.getpass", lambda prompt="": next(answers)
    )
    out = io.StringIO()

    call_command("reset_password", "--email", admin.email.upper(), stdout=out)

    assert fresh(admin).check_password(NEW_PASSWORD)
    assert browser.get("/api/v1/me").status_code == 401
    (changed,) = ServerAuditEvent.objects.filter(code="password_changed")
    assert (changed.actor_kind, changed.target_id, changed.values) == (
        "system",
        admin.id,
        {"via": "command"},
    )
    assert NEW_PASSWORD not in out.getvalue()
    (notice,) = AccountNotice.objects.filter(user=admin)
    assert notice.code == "password_reset_by_command"
    message = signed_in(admin, NEW_PASSWORD).get("/api/v1/me/notices").json()[0]["message"]
    assert re.fullmatch(
        r"Je wachtwoord is op \d{1,2} \w+ opnieuw ingesteld op de server\.", message
    )


def test_reset_password_command_refuses_different_or_weak_passwords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = UserFactory.create(is_server_admin=True)
    for typed in ([NEW_PASSWORD, NEW_PASSWORD + "x"], ["kort", "kort"]):
        answers = iter(typed)
        monkeypatch.setattr(
            "apps.accounts.management.commands.reset_password.getpass",
            lambda prompt="", answers=answers: next(answers),
        )
        with pytest.raises(CommandError):
            call_command("reset_password", "--email", admin.email, stdout=io.StringIO())
    assert fresh(admin).check_password(TEST_PASSWORD)
    with pytest.raises(CommandError):
        call_command("reset_password", "--email", "niemand@example.nl", stdout=io.StringIO())


def test_reset_password_command_takes_no_password_argument() -> None:
    with pytest.raises(CommandError):
        call_command("reset_password", "--email", "a@example.nl", "--password", NEW_PASSWORD)


# --- Fix round: web session only, devices revoked, no-store. ---


def paired(user: User, name: str = "iPhone") -> tuple[Device, str]:
    _, code = create_pairing_code(user)
    return pair_device(code=code, device_name=name)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def devices_work(*tokens: str) -> list[bool]:
    """Whether each device token still signs a call in (only flags in a failure report)."""
    return [Client().get("/api/v1/me", headers=bearer(t)).status_code == 200 for t in tokens]


def test_a_device_token_cannot_make_a_link(ws_a: Workspace) -> None:
    member = join(ws_a)
    _, admin_phone = paired(admin_of(ws_a))
    phone = Client()
    assert devices_work(admin_phone) == [True]

    r = post_json(phone, f"/users/{member.id}/password-reset", headers=bearer(admin_phone))

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"
    assert not PasswordReset.objects.exists()
    assert not AccountNotice.objects.exists()
    assert not ServerAuditEvent.objects.filter(code="password_reset_created").exists()


def test_completing_a_reset_ignores_a_device_token(ws_a: Workspace) -> None:
    # auth=None: the endpoint never acts as the phone's owner.
    member = join(ws_a)
    token = make_token(signed_in(admin_of(ws_a)), member)
    _, admin_phone = paired(admin_of(ws_a))

    r = post_json(
        Client(),
        "/auth/password-reset",
        {"token": token, "password": NEW_PASSWORD},
        headers=bearer(admin_phone),
    )

    assert (r.status_code, r.json()["id"]) == (200, str(member.id))


def test_completion_revokes_the_accounts_devices_only(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    member = join(ws_a)
    first, member_phone = paired(member, "iPhone")
    second, member_tablet = paired(member, "iPad")
    _, admin_phone = paired(admin)
    token = make_token(signed_in(admin), member)
    assert devices_work(member_phone, member_tablet) == [True, True]

    assert complete(Client(REMOTE_ADDR="203.0.113.5"), token)[0] == 200

    assert devices_work(member_phone, member_tablet, admin_phone) == [False, False, True]
    revoked = ServerAuditEvent.objects.filter(code="device_revoked")
    assert {(row.target_kind, row.target_id) for row in revoked} == {
        ("device", first.id),
        ("device", second.id),
    }
    assert {(row.actor_id, row.client_ip) for row in revoked} == {(None, "203.0.113.5")}


def test_completion_revokes_the_accounts_other_open_links(ws_a: Workspace) -> None:
    member = join(ws_a)
    token = make_token(signed_in(admin_of(ws_a)), member)
    other = PasswordReset.objects.create(
        user=member,
        token_hash=sha256("een-andere-link"),
        expires_at=timezone.now() + timedelta(hours=1),
        created_by=admin_of(ws_a),
        created_by_name="Anne",
    )

    assert complete(Client(), token)[0] == 200

    other.refresh_from_db()
    assert other.revoked_at is not None


def test_reset_password_command_revokes_devices_and_open_links(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = join(ws_a)
    _, member_phone = paired(member)
    _, admin_phone = paired(admin_of(ws_a))
    token = make_token(signed_in(admin_of(ws_a)), member)
    answers = iter([NEW_PASSWORD, NEW_PASSWORD])
    monkeypatch.setattr(
        "apps.accounts.management.commands.reset_password.getpass", lambda prompt="": next(answers)
    )

    call_command("reset_password", "--email", member.email, stdout=io.StringIO())

    assert devices_work(member_phone, admin_phone) == [False, True]
    assert complete(Client(), token) == (400, DEAD_LINK)
    (revoked,) = ServerAuditEvent.objects.filter(code="device_revoked")
    assert revoked.actor_kind == "system"


def test_change_password_keeps_the_devices() -> None:
    user = UserFactory.create()
    _, phone = paired(user)
    body = {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD}

    assert post_json(signed_in(user), "/me/password", body).status_code == 204
    assert devices_work(phone) == [True]


def test_responses_with_or_after_the_link_are_not_stored(ws_a: Workspace) -> None:
    member = join(ws_a)
    r = post_json(signed_in(admin_of(ws_a)), f"/users/{member.id}/password-reset")
    assert r.status_code == 201
    assert r["Cache-Control"] == "no-store"
    token = LINK.match(r.json()["link"])["token"]  # type: ignore[index]

    done = post_json(Client(), "/auth/password-reset", {"token": token, "password": NEW_PASSWORD})

    assert done.status_code == 200
    assert done["Cache-Control"] == "no-store"


def test_completion_locks_the_user_before_the_link(ws_a: Workspace) -> None:
    # The lock order of a removal: the user's row, then that user's reset rows. Taking
    # them the other way round here could deadlock with a removal at the same moment.
    member = join(ws_a)
    token = make_token(signed_in(admin_of(ws_a)), member)
    request = RequestFactory().post("/api/v1/auth/password-reset", REMOTE_ADDR="192.0.2.9")
    SessionMiddleware(lambda r: HttpResponse()).process_request(request)
    request.user = AnonymousUser()

    with CaptureQueriesContext(connection) as queries:
        complete_password_reset(request, token=token, password=NEW_PASSWORD)

    sqls = [q["sql"] for q in queries.captured_queries]
    user_lock = next(
        i for i, q in enumerate(sqls) if 'FROM "accounts_user"' in q and "FOR NO KEY UPDATE" in q
    )
    link_lock = next(
        i for i, q in enumerate(sqls) if 'FROM "accounts_passwordreset"' in q and "FOR UPDATE" in q
    )
    assert user_lock < link_lock


def test_making_links_is_rate_limited_per_caller(ws_a: Workspace) -> None:
    admin_client = signed_in(admin_of(ws_a))
    targets = [join(ws_a) for _ in range(2)]
    for n in range(30):
        r = post_json(admin_client, f"/users/{targets[n % 2].id}/password-reset")
        assert r.status_code == 201, n

    r = post_json(admin_client, f"/users/{targets[0].id}/password-reset")

    assert r.status_code == 429
    assert r.json()["code"] == "too_many_attempts"
    assert PasswordReset.objects.filter(revoked_at=None).count() == 2
    # Another admin is not affected.
    other = join(ws_a, Membership.Role.ADMIN)
    assert post_json(signed_in(other), f"/users/{targets[0].id}/password-reset").status_code == 201
