"""Appointing or removing a server admin on the server (D 21): `set_server_admin`.

No secret is involved, so the address is a plain argument. The server always keeps an
active server admin; the log records the system as actor and the member gets a notice.
"""

import re
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.account_state import set_server_admin
from apps.accounts.models import AccountNotice, PasswordReset, User
from apps.audit.codes import AuditCode
from apps.audit.services import AuditActor
from apps.workspaces.models import Workspace
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.members_support import admin_of, as_user, join, server_rows


def run(*args: str) -> str:
    out = StringIO()
    call_command("set_server_admin", *args, stdout=out)
    return out.getvalue()


def notices(user: User) -> list[str]:
    return list(AccountNotice.objects.filter(user=user).values_list("code", flat=True))


def test_command_makes_a_member_server_admin() -> None:
    UserFactory.create(is_server_admin=True)
    user = UserFactory.create(email="anne@example.nl")

    output = run("--email", "Anne@Example.nl")

    user.refresh_from_db()
    assert user.is_server_admin
    assert output.strip() == f"{user.name} is nu serverbeheerder."
    rows = server_rows(AuditCode.SERVER_ADMIN_GRANTED)
    assert [(r.actor_kind, r.actor_id, r.target_id) for r in rows] == [("system", None, user.id)]
    assert notices(user) == [AccountNotice.Code.SERVER_ADMIN_GRANTED]


def test_command_removes_the_flag_when_another_admin_exists(ws_a: Workspace) -> None:
    UserFactory.create(is_server_admin=True)
    boss = join(ws_a, user=UserFactory.create(is_server_admin=True))
    target = join(ws_a)
    r = post_json(as_user(boss), f"/users/{target.id}/password-reset")
    assert r.status_code == 201

    output = run("--email", boss.email, "--remove")

    boss.refresh_from_db()
    assert not boss.is_server_admin
    assert boss.is_active
    assert output.strip() == f"{boss.name} is geen serverbeheerder meer."
    rows = server_rows(AuditCode.SERVER_ADMIN_REVOKED)
    assert [(r.actor_kind, r.target_id) for r in rows] == [("system", boss.id)]
    assert PasswordReset.objects.get(created_by=boss).revoked_at is not None
    assert notices(boss) == [AccountNotice.Code.SERVER_ADMIN_REVOKED]


def test_the_last_server_admin_cannot_be_removed() -> None:
    boss = UserFactory.create(is_server_admin=True)
    # An inactive server admin does not count.
    UserFactory.create(is_server_admin=True, is_active=False)

    with pytest.raises(
        CommandError, match=re.escape("Er is altijd minstens één serverbeheerder nodig.")
    ):
        run("--email", boss.email, "--remove")

    boss.refresh_from_db()
    assert boss.is_server_admin
    assert server_rows(AuditCode.SERVER_ADMIN_REVOKED) == []
    assert notices(boss) == []


def test_an_unknown_address_or_inactive_account_is_refused() -> None:
    sleeping = UserFactory.create(is_active=False)

    with pytest.raises(CommandError, match=re.escape("Er is geen account met dit e-mailadres.")):
        run("--email", "niemand@example.nl")
    with pytest.raises(
        CommandError,
        match=re.escape("Dit account is uitgeschakeld. Nodig de persoon eerst opnieuw uit."),
    ):
        run("--email", sleeping.email)

    sleeping.refresh_from_db()
    assert not sleeping.is_server_admin
    assert server_rows(AuditCode.SERVER_ADMIN_GRANTED) == []


def test_granting_twice_writes_one_event() -> None:
    user = UserFactory.create()

    run("--email", user.email)
    output = run("--email", user.email)

    assert server_rows(AuditCode.SERVER_ADMIN_GRANTED) != []
    assert len(server_rows(AuditCode.SERVER_ADMIN_GRANTED)) == 1
    assert len(notices(user)) == 1
    assert output.strip() == f"{user.name} is nu serverbeheerder."


def test_removing_somebody_who_is_none_changes_nothing() -> None:
    UserFactory.create(is_server_admin=True)
    user = UserFactory.create()

    set_server_admin(email=user.email, grant=False, actor=AuditActor.system())

    assert server_rows(AuditCode.SERVER_ADMIN_REVOKED) == []
    assert notices(user) == []


def test_removal_takes_the_server_admin_lock() -> None:
    UserFactory.create(is_server_admin=True)
    boss = UserFactory.create(is_server_admin=True)

    with CaptureQueriesContext(connection) as queries:
        set_server_admin(email=boss.email, grant=False, actor=AuditActor.system())

    locks = [
        q["sql"]
        for q in queries.captured_queries
        if 'FROM "accounts_user"' in q["sql"]
        and "FOR UPDATE" in q["sql"]
        and '"is_server_admin"' in q["sql"]
    ]
    assert len(locks) == 1


def test_a_new_server_admin_may_act_on_any_account(ws_a: Workspace, ws_b: Workspace) -> None:
    user = admin_of(ws_a)
    other = join(ws_b)
    join(ws_a, user=other)

    assert post_json(as_user(user), f"/users/{other.id}/password-reset").status_code == 403
    run("--email", user.email)

    assert post_json(as_user(user), f"/users/{other.id}/password-reset").status_code == 201
