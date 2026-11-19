"""An admin over a member's account: their devices and sessions (D 6.3, D 20, D 21).

Web session only. Every refusal is proved through the HTTP endpoints as a real role, and
each proof checks that nothing changed.
"""

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.authority import account_authority
from apps.accounts.models import AccountNotice, User, UserSession
from apps.audit.codes import AuditCode
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.common.errors import ApiError
from apps.devices.models import Device, TokenExpiryStart
from apps.devices.services import create_pairing_code, pair_device
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json


def admin_of(ws: Workspace) -> User:
    return Membership.objects.get(workspace=ws, role=Membership.Role.ADMIN).user


def join(ws: Workspace, role: str = Membership.Role.MEMBER, user: User | None = None) -> User:
    user = user or UserFactory.create()
    Membership.objects.create(user=user, workspace=ws, role=role)
    return user


def signed_in(user: User) -> Client:
    client = Client()
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert r.status_code == 200
    return client


def paired(user: User, name: str = "iPhone") -> tuple[Device, str]:
    _, code = create_pairing_code(user)
    return pair_device(code=code, device_name=name)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sessions_url(target: User) -> str:
    return f"/api/v1/users/{target.id}/sessions"


def devices_url(target: User) -> str:
    return f"/api/v1/users/{target.id}/devices"


def device_url(target: User, device: Device) -> str:
    return f"/api/v1/users/{target.id}/devices/{device.id}"


def server_events(code: AuditCode) -> list[ServerAuditEvent]:
    return list(ServerAuditEvent.objects.filter(code=code.value))


def workspace_events(ws: Workspace, code: AuditCode) -> list[WorkspaceAuditEvent]:
    with workspace_context(ws.id):
        return list(WorkspaceAuditEvent.objects.filter(code=code.value))


def notices_of(user: User) -> list[AccountNotice]:
    return list(AccountNotice.objects.filter(user=user))


class World:
    """A member of ws_a with a signed-in browser and a paired phone."""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self.member = join(ws)
        self.browser = signed_in(self.member)
        self.device, self.token = paired(self.member)

    def alive(self) -> bool:
        return self.browser.get("/api/v1/me").status_code == 200

    def unchanged(self) -> None:
        """Nothing happened to the member's account: browser and phone work, no notice."""
        assert self.alive()
        self.device.refresh_from_db()
        assert self.device.revoked_at is None
        assert Client().get("/api/v1/me", headers=bearer(self.token)).status_code == 200
        assert notices_of(self.member) == []
        assert server_events(AuditCode.SESSION_ENDED) == []
        assert server_events(AuditCode.DEVICE_REVOKED) == []


def attempts(world: World) -> list[Callable[[Client], int]]:
    return [
        lambda c: c.delete(sessions_url(world.member)).status_code,
        lambda c: c.get(devices_url(world.member)).status_code,
        lambda c: c.delete(device_url(world.member, world.device)).status_code,
    ]


def test_workspace_admin_ends_a_members_sessions(ws_a: Workspace) -> None:
    world = World(ws_a)
    admin = admin_of(ws_a)
    admin_browser = signed_in(admin)

    r = admin_browser.delete(sessions_url(world.member))

    assert r.status_code == 204
    assert world.browser.get("/api/v1/me").status_code == 401
    assert not UserSession.objects.filter(user=world.member).exists()
    assert admin_browser.get("/api/v1/me").status_code == 200
    (notice,) = notices_of(world.member)
    assert (notice.code, notice.actor_name) == ("sessions_ended", admin.name)
    (server_row,) = server_events(AuditCode.SESSION_ENDED)
    assert server_row.values == {"count": 1, "reason": "admin"}
    assert server_row.target_id == world.member.id
    assert server_row.actor_id == admin.id
    (ws_row,) = workspace_events(ws_a, AuditCode.SESSION_ENDED)
    assert ws_row.values == {"count": 1, "reason": "admin"}


def test_ending_no_sessions_is_204_and_leaves_no_notice(ws_a: Workspace) -> None:
    member = join(ws_a)

    r = signed_in(admin_of(ws_a)).delete(sessions_url(member))

    assert r.status_code == 204
    assert notices_of(member) == []
    assert server_events(AuditCode.SESSION_ENDED) == []


def test_workspace_admin_lists_and_revokes_a_members_device(ws_a: Workspace) -> None:
    world = World(ws_a)
    admin = admin_of(ws_a)
    admin_browser = signed_in(admin)

    listed = admin_browser.get(devices_url(world.member))
    revoked = admin_browser.delete(device_url(world.member, world.device))

    assert listed.status_code == 200
    assert [d["id"] for d in listed.json()] == [str(world.device.id)]
    assert revoked.status_code == 204
    assert Client().get("/api/v1/me", headers=bearer(world.token)).status_code == 401
    (notice,) = notices_of(world.member)
    assert (notice.code, notice.actor_name) == ("device_revoked", admin.name)
    assert notice.values == {"device": "iPhone"}
    (server_row,) = server_events(AuditCode.DEVICE_REVOKED)
    assert (server_row.target_id, server_row.actor_id) == (world.device.id, admin.id)
    (ws_row,) = workspace_events(ws_a, AuditCode.DEVICE_REVOKED)
    assert ws_row.target_id == world.device.id


def test_workspace_admin_cannot_touch_a_member_of_two_workspaces(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    world = World(ws_a)
    join(ws_b, user=world.member)
    admin_browser = signed_in(admin_of(ws_a))

    for attempt in attempts(world):
        r = attempt(admin_browser)
        assert r == 403
    body = admin_browser.get(devices_url(world.member)).json()
    assert body["code"] == "server_admin_only"
    world.unchanged()


def test_workspace_admin_cannot_touch_a_server_admin(ws_a: Workspace) -> None:
    world = World(ws_a)
    User.objects.filter(pk=world.member.pk).update(is_server_admin=True)
    admin_browser = signed_in(admin_of(ws_a))

    assert [attempt(admin_browser) for attempt in attempts(world)] == [403, 403, 403]
    world.unchanged()


def test_server_admin_lists_and_revokes_anyones_device(ws_a: Workspace, ws_b: Workspace) -> None:
    world = World(ws_a)
    join(ws_b, user=world.member)
    boss = UserFactory.create(is_server_admin=True)
    boss_browser = signed_in(boss)

    listed = boss_browser.get(devices_url(world.member))
    revoked = boss_browser.delete(device_url(world.member, world.device))

    assert [d["id"] for d in listed.json()] == [str(world.device.id)]
    assert revoked.status_code == 204
    assert Client().get("/api/v1/me", headers=bearer(world.token)).status_code == 401
    assert len(notices_of(world.member)) == 1
    # One row for the revocation (revoke_device writes it), no workspace row: the account
    # belongs to two workspaces.
    (row,) = server_events(AuditCode.DEVICE_REVOKED)
    assert row.actor_id == boss.id
    assert not workspace_events(ws_a, AuditCode.DEVICE_REVOKED)
    assert not workspace_events(ws_b, AuditCode.DEVICE_REVOKED)


def test_server_admin_ends_sessions_of_anyone(ws_a: Workspace, ws_b: Workspace) -> None:
    world = World(ws_a)
    join(ws_b, user=world.member)
    boss_browser = signed_in(UserFactory.create(is_server_admin=True))

    assert boss_browser.delete(sessions_url(world.member)).status_code == 204

    assert world.browser.get("/api/v1/me").status_code == 401
    assert boss_browser.get("/api/v1/me").status_code == 200
    assert len(server_events(AuditCode.SESSION_ENDED)) == 1


def test_nobody_acts_on_their_own_account_through_these_endpoints(ws_a: Workspace) -> None:
    # A server admin ending their own sessions here would sign themselves out; their own
    # sessions and devices have pages of their own (account_authority: own_account).
    boss = UserFactory.create(is_server_admin=True)
    boss_browser = signed_in(boss)
    device, token = paired(boss)

    r = boss_browser.delete(sessions_url(boss))
    listed = boss_browser.get(devices_url(boss))
    revoked = boss_browser.delete(device_url(boss, device))

    assert [r.status_code, listed.status_code, revoked.status_code] == [403, 403, 403]
    assert r.json()["code"] == "own_account"
    assert boss_browser.get("/api/v1/me").status_code == 200
    assert Client().get("/api/v1/me", headers=bearer(token)).status_code == 200
    assert notices_of(boss) == []
    assert server_events(AuditCode.DEVICE_REVOKED) == []


def test_revoking_another_users_device_id_is_404(ws_a: Workspace) -> None:
    world = World(ws_a)
    other = join(ws_a)
    other_device, other_token = paired(other)
    admin_browser = signed_in(admin_of(ws_a))

    r = admin_browser.delete(device_url(world.member, other_device))

    assert r.status_code == 404
    assert r.json()["code"] == "device_not_found"
    other_device.refresh_from_db()
    assert other_device.revoked_at is None
    assert Client().get("/api/v1/me", headers=bearer(other_token)).status_code == 200
    world.unchanged()
    assert notices_of(other) == []


def test_an_already_revoked_device_is_404_and_leaves_no_notice(ws_a: Workspace) -> None:
    world = World(ws_a)
    Device.objects.filter(pk=world.device.pk).update(revoked_at=timezone.now())

    r = signed_in(admin_of(ws_a)).delete(device_url(world.member, world.device))

    assert (r.status_code, r.json()["code"]) == (404, "device_not_found")
    assert notices_of(world.member) == []


@pytest.mark.parametrize("role", [Membership.Role.MEMBER, Membership.Role.VIEWER])
def test_a_member_or_viewer_gets_404_and_nothing_changes(ws_a: Workspace, role: str) -> None:
    world = World(ws_a)
    caller = signed_in(join(ws_a, role=role))

    results = [attempt(caller) for attempt in attempts(world)]

    assert results == [404, 404, 404]
    world.unchanged()


def test_an_admin_of_another_workspace_gets_404_and_nothing_changes(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    world = World(ws_a)
    caller = signed_in(admin_of(ws_b))

    assert [attempt(caller) for attempt in attempts(world)] == [404, 404, 404]
    world.unchanged()


def test_an_unknown_id_and_a_strangers_id_are_indistinguishable(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    stranger = join(ws_b)
    ghost = UserFactory.build()  # never saved: an id nobody has
    admin_browser = signed_in(admin_of(ws_a))
    device, _token = paired(stranger)

    def answers(target: User) -> list[tuple[int, bytes]]:
        responses = [
            admin_browser.delete(sessions_url(target)),
            admin_browser.get(devices_url(target)),
            admin_browser.delete(device_url(target, device)),
        ]
        return [(r.status_code, r.content) for r in responses]

    assert answers(stranger) == answers(ghost)
    assert {status for status, _ in answers(ghost)} == {404}
    device.refresh_from_db()
    assert device.revoked_at is None
    assert notices_of(stranger) == []


def test_a_device_token_gets_401_on_all_three(ws_a: Workspace) -> None:
    world = World(ws_a)
    admin = admin_of(ws_a)
    _, admin_token = paired(admin)
    headers = bearer(admin_token)
    csrf_free = Client()

    results = [
        csrf_free.delete(sessions_url(world.member), headers=headers).status_code,
        csrf_free.get(devices_url(world.member), headers=headers).status_code,
        csrf_free.delete(device_url(world.member, world.device), headers=headers).status_code,
    ]

    assert results == [401, 401, 401]
    world.unchanged()


def test_without_signing_in_it_is_401(ws_a: Workspace) -> None:
    world = World(ws_a)

    assert [attempt(Client()) for attempt in attempts(world)] == [401, 401, 401]
    world.unchanged()


def test_writes_need_a_csrf_token(ws_a: Workspace) -> None:
    world = World(ws_a)
    strict = Client(enforce_csrf_checks=True)
    post_json(strict, "/auth/login", {"email": admin_of(ws_a).email, "password": TEST_PASSWORD})
    # The login itself is exempt only without a session; sign the session in directly.
    strict.force_login(admin_of(ws_a))

    assert strict.delete(sessions_url(world.member)).status_code == 403
    assert strict.delete(device_url(world.member, world.device)).status_code == 403
    world.unchanged()


def test_revoking_ones_own_device_leaves_no_notice(ws_a: Workspace) -> None:
    member = join(ws_a)
    device, _token = paired(member)

    r = signed_in(member).delete(f"/api/v1/devices/{device.id}")

    assert r.status_code == 204
    assert notices_of(member) == []
    assert len(server_events(AuditCode.DEVICE_REVOKED)) == 1


def test_expired_devices_are_not_listed(ws_a: Workspace) -> None:
    member = join(ws_a)
    live, _ = paired(member, "Nieuw")
    old, _ = paired(member, "Oud")
    now = timezone.now()
    Device.objects.filter(pk=old.pk).update(
        last_seen_at=now - timedelta(days=400), created_at=now - timedelta(days=500)
    )
    TokenExpiryStart.objects.all().update(started_at=now - timedelta(days=400))

    r = signed_in(admin_of(ws_a)).get(devices_url(member))

    assert [d["id"] for d in r.json()] == [str(live.id)]


def test_list_members_says_which_accounts_the_caller_may_manage(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    admin = admin_of(ws_a)
    plain = join(ws_a)
    shared = join(ws_a)
    join(ws_b, user=shared)
    boss_member = join(ws_a, user=UserFactory.create(is_server_admin=True))
    viewer = join(ws_a, role=Membership.Role.VIEWER)
    colleague = join(ws_a, role=Membership.Role.MEMBER)
    url = f"/api/v1/workspaces/{ws_a.id}/members"
    everyone = [admin, plain, shared, boss_member, viewer, colleague]

    def flags(caller: User) -> dict[str, bool]:
        client = signed_in(caller)
        rows = client.get(url).json()
        return {row["user_id"]: row["can_manage_account"] for row in rows}

    def expected_by_authority(caller: User) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for user in everyone:
            try:
                account_authority(caller, user.id)
                result[str(user.id)] = True
            except ApiError:
                result[str(user.id)] = False
        return result

    as_admin = flags(admin)
    assert as_admin[str(plain.id)] is True
    assert as_admin[str(shared.id)] is False  # also a member of another workspace
    assert as_admin[str(boss_member.id)] is False  # a server admin
    assert as_admin[str(admin.id)] is False  # not on oneself
    assert as_admin == expected_by_authority(admin)

    boss = UserFactory.create(is_server_admin=True)
    join(ws_a, user=boss)
    everyone.append(boss)
    as_boss = flags(boss)
    assert as_boss[str(shared.id)] is True
    assert as_boss[str(boss_member.id)] is True
    assert as_boss[str(boss.id)] is False
    assert as_boss == expected_by_authority(boss)

    for caller in (plain, viewer):
        assert not any(flags(caller).values())
        assert flags(caller) == expected_by_authority(caller)
