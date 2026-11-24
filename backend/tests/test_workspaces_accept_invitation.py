"""Opening and accepting an invitation (D 6.2): once, signing in through `start_session`."""

import hashlib
import logging
import re
import threading

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User, UserSession
from apps.accounts.services import verify_password
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.people.models import Person
from apps.pipeline.settings_service import Actor, configure, configure_autopilot
from apps.workspaces import invitations
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.invitations import accept_invitation
from apps.workspaces.members import remove_member
from apps.workspaces.models import Invitation, Membership, Workspace
from tests.documents_support import admin_of
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json

NEW_PASSWORD = "een-lang-nieuw-wachtwoord-2026"
DEAD = {
    "code": "invitation_invalid",
    "message": "Deze uitnodiging werkt niet meer. Vraag een nieuwe.",
}
LINK = re.compile(r"#(?P<token>[A-Za-z0-9_-]{43})$")


@pytest.fixture(autouse=True)
def _fast_hashing_and_public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def as_admin(ws: Workspace) -> Client:
    client = Client()
    client.force_login(admin_of(ws))
    return client


def invite(ws: Workspace, **body: object) -> str:
    r = post_json(as_admin(ws), f"/workspaces/{ws.id}/invitations", body)
    assert r.status_code == 201, r.json()
    match = LINK.search(r.json()["link"])
    assert match is not None
    return match["token"]


def lookup(client: Client, token: str) -> "tuple[int, dict[str, object]]":
    r = post_json(client, "/invitations/lookup", {"token": token})
    return r.status_code, r.json()


def accept(
    client: Client,
    token: str,
    *,
    account: str = "new",
    email: str = "nieuw@example.nl",
    password: str = NEW_PASSWORD,
    name: str = "Anne de Vries",
    **extra: object,
) -> "tuple[int, dict[str, object]]":
    body = {
        "token": token,
        "account": account,
        "name": name,
        "email": email,
        "password": password,
        **extra,
    }
    r = post_json(client, "/invitations/accept", body)
    return r.status_code, r.json()


def person(ws: Workspace, name: str) -> Person:
    with workspace_context(ws.id, actor=SYSTEM):
        return Person.objects.create(workspace=ws, display_name=name)


def workspace_rows(ws: Workspace, code: str) -> list[WorkspaceAuditEvent]:
    with workspace_context(ws.id, actor=SYSTEM):
        return list(WorkspaceAuditEvent.objects.filter(code=code).order_by("created_at", "id"))


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def unused(token: str) -> bool:
    return Invitation.objects.get(token_hash=sha256(token)).accepted_at is None


def test_lookup_shows_workspace_inviter_and_role(ws_a: Workspace) -> None:
    token = invite(ws_a, role="viewer", note="Anne", email="anne@example.nl")

    status, body = lookup(Client(), token)

    assert status == 200
    assert set(body) == {"workspace_name", "invited_by", "role", "email_fixed", "person_name", "ai"}
    assert (body["workspace_name"], body["invited_by"], body["role"]) == (
        "Thuis",
        admin_of(ws_a).name,
        "viewer",
    )
    # Whether an address is fixed, not the address; the admin's note never.
    assert (body["email_fixed"], body["person_name"]) == (True, "")
    assert "anne@example.nl" not in str(body)
    assert "Anne" not in str(body).replace(admin_of(ws_a).name, "")
    assert lookup(Client(), invite(ws_a))[1]["email_fixed"] is False


def test_lookup_names_the_person_from_inside_the_workspace_context(ws_a: Workspace) -> None:
    papa = person(ws_a, "Papa")
    token = invite(ws_a, person_id=str(papa.id))

    assert lookup(Client(), token)[1]["person_name"] == "Papa"


def test_lookup_shows_the_real_autopilot_switches(ws_a: Workspace) -> None:
    token = invite(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        configure(
            workspace_id=ws_a.id,
            mode="external",
            base_url="",
            model="",
            api_key="sk-geheim",
            consent=True,
        )
        configure_autopilot(
            workspace_id=ws_a.id,
            organise=True,
            confirm=True,
            acknowledged=True,
            actor=Actor(user_id=None, label="api"),
        )

    ai = lookup(Client(), token)[1]["ai"]

    assert isinstance(ai, dict)
    assert (ai["auto_organise"], ai["auto_confirm"], ai["autopilot_allowed"]) == (True, True, True)


def test_lookup_shows_the_ai_line_when_ai_is_on(ws_a: Workspace) -> None:
    token = invite(ws_a)
    assert lookup(Client(), token)[1]["ai"] == {
        "mode": "off", "supplier": None, "host": None, "since": None, "has_api_key": False,
        "model": None, "auto_organise": False, "auto_confirm": False, "autopilot_allowed": True,
    }  # fmt: skip
    with workspace_context(ws_a.id, actor=SYSTEM):
        configure(
            workspace_id=ws_a.id,
            mode="external",
            base_url="",
            model="",
            api_key="sk-geheim",
            consent=True,
        )

    status, body = lookup(Client(), token)

    assert status == 200
    ai = body["ai"]
    assert isinstance(ai, dict)
    assert (ai["mode"], ai["supplier"], ai["host"], ai["model"]) == (
        "external",
        "mistral",
        "api.mistral.ai",
        None,
    )
    assert ai["since"] is not None
    assert "sk-geheim" not in str(body)


def make_dead(ws: Workspace, token: str, state: str) -> str:
    invitation = Invitation.objects.get(token_hash=sha256(token))
    rows = Invitation.objects.filter(pk=invitation.pk)
    if state == "accepted":
        rows.update(accepted_at=timezone.now())
    elif state == "revoked":
        rows.update(revoked_at=timezone.now())
    elif state == "expired":
        rows.update(expires_at=timezone.now())
    elif state == "creator_demoted":
        Membership.objects.filter(workspace=ws, user=admin_of(ws)).update(role="member")
    elif state == "creator_deactivated":
        User.objects.filter(pk=admin_of(ws).pk).update(is_active=False)
    else:
        return token[::-1]
    return token


DEAD_STATES = [
    "accepted",
    "revoked",
    "expired",
    "unknown",
    "creator_demoted",
    "creator_deactivated",
]


@pytest.mark.parametrize("state", DEAD_STATES)
def test_dead_invitation_answers_one_sentence(ws_a: Workspace, state: str) -> None:
    admin = admin_of(ws_a)
    token = make_dead(ws_a, invite(ws_a), state)
    members_before = Membership.objects.count()

    assert lookup(Client(), token) == (400, DEAD)
    assert accept(Client(), token) == (400, DEAD)
    assert accept(Client(), token, account="existing", email=admin.email) == (400, DEAD)
    assert Membership.objects.count() == members_before
    assert not User.objects.filter(email="nieuw@example.nl").exists()


def test_every_dead_invitation_costs_the_same_queries(ws_a: Workspace) -> None:
    counts: dict[str, tuple[int, int]] = {}
    for number, state in enumerate(DEAD_STATES):
        Membership.objects.filter(workspace=ws_a).update(role="admin")
        User.objects.update(is_active=True)
        token = make_dead(ws_a, invite(ws_a), state)
        with CaptureQueriesContext(connection) as looked_up:
            assert lookup(Client(REMOTE_ADDR=f"192.0.2.{number + 1}"), token)[0] == 400
        with CaptureQueriesContext(connection) as accepted:
            assert accept(Client(REMOTE_ADDR=f"198.51.100.{number + 1}"), token)[0] == 400
        counts[state] = (len(looked_up), len(accepted))

    assert len(set(counts.values())) == 1, counts


def test_accept_with_a_new_account_signs_in_and_links_the_person(ws_a: Workspace) -> None:
    papa = person(ws_a, "Papa")
    token = invite(ws_a, person_id=str(papa.id))
    browser = Client()

    status, body = accept(browser, token, email="  Papa@Example.NL ", name="Kees")

    assert status == 200
    assert set(body) == {"user", "workspace"}
    user = User.objects.get(email="papa@example.nl")
    assert user.check_password(NEW_PASSWORD)
    assert (user.name, user.is_active, user.is_server_admin) == ("Kees", True, False)
    workspace = body["workspace"]
    assert isinstance(workspace, dict)
    assert (workspace["id"], workspace["role"]) == (str(ws_a.id), "member")
    assert Membership.objects.get(workspace=ws_a, user=user).role == "member"
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Person.objects.get(pk=papa.pk).user_id == user.id
        assert Person.objects.filter(workspace=ws_a, user=user).count() == 1
    me = browser.get("/api/v1/me")
    assert (me.status_code, me.json()["email"]) == (200, "papa@example.nl")
    assert UserSession.objects.get(user=user).created_via == "invitation"
    (signed_in,) = ServerAuditEvent.objects.filter(code="sign_in_succeeded", actor_id=user.id)
    assert signed_in.values == {"via": "invitation", "second_factor": "none"}
    (linked,) = workspace_rows(ws_a, "person_linked")
    assert (linked.target_kind, linked.target_id, linked.target_name) == ("person", papa.id, "Papa")
    (accepted,) = workspace_rows(ws_a, "invitation_accepted")
    assert accepted.values == {"role": "member", "new_account": True, "account_reactivated": False}
    assert accepted.actor_id == user.id
    invitation = Invitation.objects.get(token_hash=sha256(token))
    assert (invitation.accepted_by_id, invitation.accepted_at is not None) == (user.id, True)


def test_accept_without_person_creates_one(ws_a: Workspace) -> None:
    token = invite(ws_a)

    assert accept(Client(), token, name="Anne de Vries")[0] == 200

    user = User.objects.get(email="nieuw@example.nl")
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Person.objects.get(workspace=ws_a, user=user).display_name == "Anne de Vries"
    assert not workspace_rows(ws_a, "person_linked")


def test_the_role_is_the_one_on_the_invitation(ws_a: Workspace) -> None:
    token = invite(ws_a, role="viewer")

    status, _ = accept(Client(), token, role="admin")

    assert status == 200
    user = User.objects.get(email="nieuw@example.nl")
    assert Membership.objects.get(workspace=ws_a, user=user).role == "viewer"


def test_on_member_added_gets_the_person_and_the_maker(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []

    def spy(
        workspace_id: object, user_id: object, *, person_id: object, created_by_id: object
    ) -> None:
        calls.append((workspace_id, user_id, person_id, created_by_id))

    monkeypatch.setattr("apps.workspaces.invitations.on_member_added", spy)
    papa = person(ws_a, "Papa")

    accept(Client(), invite(ws_a, person_id=str(papa.id)))

    user = User.objects.get(email="nieuw@example.nl")
    assert calls == [(ws_a.id, user.id, papa.id, admin_of(ws_a).id)]


def test_a_failure_anywhere_leaves_nothing_behind(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("part B failed")

    monkeypatch.setattr("apps.workspaces.invitations.on_member_added", broken)
    papa = person(ws_a, "Papa")
    token = invite(ws_a, person_id=str(papa.id))
    members_before = Membership.objects.count()

    status, body = accept(Client(raise_request_exception=False), token)

    assert (status, body["code"]) == (500, "internal_error")
    assert not User.objects.filter(email="nieuw@example.nl").exists()
    assert Membership.objects.count() == members_before
    assert unused(token)
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Person.objects.get(pk=papa.pk).user_id is None
    assert not workspace_rows(ws_a, "person_linked")
    assert not workspace_rows(ws_a, "invitation_accepted")
    assert not UserSession.objects.filter(created_via="invitation").exists()


def test_an_unknown_account_kind_is_422(ws_a: Workspace) -> None:
    token = invite(ws_a)

    assert accept(Client(), token, account="sso") == (
        422,
        {
            "code": "validation_error",
            "message": "Kies een nieuw account of een bestaand account.",
        },
    )
    assert unused(token)


@pytest.mark.parametrize(
    ("field", "value"), [("password", "kort"), ("name", "  "), ("email", "geen-adres")]
)
def test_a_new_account_follows_the_rules(ws_a: Workspace, field: str, value: str) -> None:
    token = invite(ws_a)

    status, body = accept(Client(), token, **{field: value})

    assert (status, body["code"]) == (422, "validation_error")
    assert unused(token)
    assert User.objects.count() == 1  # the admin


@pytest.mark.parametrize("active", [True, False])
def test_new_account_with_a_taken_address_is_409(ws_a: Workspace, active: bool) -> None:
    UserFactory.create(email="bestaand@example.nl", is_active=active)
    token = invite(ws_a)

    status, body = accept(Client(), token, email="Bestaand@example.nl")

    assert status == 409
    assert body == {
        "code": "account_exists",
        "message": "Er is al een account met dit e-mailadres. Kies ‘Ik heb al een account’.",  # noqa: RUF001
    }
    assert unused(token)
    assert not Membership.objects.filter(user__email="bestaand@example.nl").exists()


def test_a_unique_violation_on_accept_answers_409(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another accept with the same address won the race between the check and the insert.
    UserFactory.create(email="nieuw@example.nl")
    monkeypatch.setattr("apps.workspaces.invitations._account_exists", lambda email: False)
    token = invite(ws_a)

    status, body = accept(Client(), token)

    assert (status, body["code"]) == (409, "account_exists")
    assert unused(token)
    assert not Membership.objects.filter(workspace=ws_a, user__email="nieuw@example.nl").exists()


def test_the_same_user_accepting_twice_at_once_answers_409(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The membership was made by a concurrent accept after the check.
    user = UserFactory.create()
    Membership.objects.create(user=user, workspace=ws_a, role="member")
    monkeypatch.setattr("apps.workspaces.invitations._is_member", lambda user, workspace: False)
    token = invite(ws_a)

    status, body = accept(
        Client(), token, account="existing", email=user.email, password=TEST_PASSWORD
    )

    assert (status, body["code"]) == (409, "already_member")
    assert unused(token)


def test_existing_account_joins_a_second_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    other_admin = admin_of(ws_b)
    token = invite(ws_a, role="viewer")
    browser = Client()

    status, body = accept(
        browser, token, account="existing", email=other_admin.email.upper(), password=TEST_PASSWORD
    )

    assert status == 200
    user = body["user"]
    assert isinstance(user, dict)
    assert user["id"] == str(other_admin.id)
    assert {(m.workspace_id, m.role) for m in Membership.objects.filter(user=other_admin)} == {
        (ws_a.id, "viewer"),
        (ws_b.id, "admin"),
    }
    assert {w["id"] for w in browser.get("/api/v1/workspaces").json()} == {
        str(ws_a.id),
        str(ws_b.id),
    }
    (accepted,) = workspace_rows(ws_a, "invitation_accepted")
    assert accepted.values == {"role": "viewer", "new_account": False, "account_reactivated": False}


def test_accepting_while_signed_in_as_the_same_user_keeps_one_session_row(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    other_admin = admin_of(ws_b)
    browser = Client()
    r = post_json(browser, "/auth/login", {"email": other_admin.email, "password": TEST_PASSWORD})
    assert r.status_code == 200

    status, _ = accept(
        browser, invite(ws_a), account="existing", email=other_admin.email, password=TEST_PASSWORD
    )

    assert status == 200
    (row,) = UserSession.objects.filter(user=other_admin)
    assert row.created_via == "invitation"
    assert browser.get("/api/v1/me").status_code == 200


def test_accepting_in_a_browser_signed_in_as_somebody_else_switches(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    token = invite(ws_a)
    browser = Client()
    browser.force_login(admin_of(ws_a))
    before = UserSession.objects.filter(user=admin_of(ws_a)).count()

    assert accept(browser, token)[0] == 200

    assert browser.get("/api/v1/me").json()["email"] == "nieuw@example.nl"
    assert UserSession.objects.filter(user=admin_of(ws_a)).count() == before - 1


def test_an_existing_account_is_never_found_by_its_address_alone(ws_a: Workspace) -> None:
    victim = UserFactory.create(email="slachtoffer@example.nl")
    token = invite(ws_a)

    status, body = accept(Client(), token, account="existing", email=victim.email, password="raden")

    assert (status, body["code"]) == (401, "invalid_credentials")
    assert unused(token)
    assert not Membership.objects.filter(user=victim).exists()
    assert accept(Client(), token, account="existing", email="niemand@example.nl")[0] == 401


def test_wrong_password_counts_towards_the_lockout(ws_a: Workspace) -> None:
    user = UserFactory.create()
    token = invite(ws_a)
    browser = Client()

    statuses = [
        accept(browser, token, account="existing", email=user.email, password="fout")[0]
        for _ in range(5)
    ]
    locked = accept(browser, token, account="existing", email=user.email, password=TEST_PASSWORD)

    assert statuses == [401] * 5
    assert (locked[0], locked[1]["code"]) == (429, "too_many_attempts")
    assert unused(token)
    assert ServerAuditEvent.objects.filter(code="sign_in_failed", target_id=user.id).count() == 5
    (lockout,) = ServerAuditEvent.objects.filter(code="sign_in_locked_out")
    assert lockout.values == {"via": "invitation"}
    # The same lockout holds for the sign-in page.
    r = post_json(browser, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert r.status_code == 429


def test_deactivated_account_comes_back_only_with_its_password(ws_a: Workspace) -> None:
    user = UserFactory.create(is_active=False)
    r = post_json(Client(), "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert r.status_code == 401
    token = invite(ws_a)

    wrong = accept(Client(), token, account="existing", email=user.email, password="fout")
    assert wrong[0] == 401
    user.refresh_from_db()
    assert not user.is_active
    browser = Client()
    status, _ = accept(browser, token, account="existing", email=user.email, password=TEST_PASSWORD)

    assert status == 200
    user.refresh_from_db()
    assert user.is_active
    assert Membership.objects.get(workspace=ws_a, user=user).role == "member"
    assert browser.get("/api/v1/me").status_code == 200
    (accepted,) = workspace_rows(ws_a, "invitation_accepted")
    assert accepted.values == {"role": "member", "new_account": False, "account_reactivated": True}


@pytest.mark.parametrize("account", ["new", "existing"])
def test_email_bound_invitation_refuses_another_address(ws_a: Workspace, account: str) -> None:
    user = UserFactory.create(email="ander@example.nl")
    token = invite(ws_a, email="bedoeld@example.nl")
    email = "ander@example.nl" if account == "existing" else "nog-een@example.nl"

    status, body = accept(Client(), token, account=account, email=email, password=TEST_PASSWORD)

    assert (status, body["code"]) == (403, "invitation_email_mismatch")
    assert unused(token)
    assert not Membership.objects.filter(user=user).exists()


def test_email_bound_invitation_accepts_that_address(ws_a: Workspace) -> None:
    token = invite(ws_a, email="bedoeld@example.nl")

    assert accept(Client(), token, email="Bedoeld@Example.nl")[0] == 200


def test_already_member_is_409_and_keeps_the_invitation(ws_a: Workspace) -> None:
    member = UserFactory.create()
    Membership.objects.create(user=member, workspace=ws_a, role="viewer")
    token = invite(ws_a, role="admin")

    status, body = accept(
        Client(), token, account="existing", email=member.email, password=TEST_PASSWORD
    )

    assert (status, body["code"]) == (409, "already_member")
    assert unused(token)
    assert Membership.objects.get(workspace=ws_a, user=member).role == "viewer"


def test_person_linked_meanwhile_is_409(ws_a: Workspace) -> None:
    papa = person(ws_a, "Papa")
    token = invite(ws_a, person_id=str(papa.id))
    other = UserFactory.create()
    Membership.objects.create(user=other, workspace=ws_a, role="member")
    with workspace_context(ws_a.id, actor=SYSTEM):
        Person.objects.filter(pk=papa.pk).update(user=other)

    status, body = accept(Client(), token)

    assert (status, body["code"]) == (409, "invitation_person_taken")
    assert unused(token)
    assert not User.objects.filter(email="nieuw@example.nl").exists()


def test_person_archived_meanwhile_is_409(ws_a: Workspace) -> None:
    papa = person(ws_a, "Papa")
    token = invite(ws_a, person_id=str(papa.id))
    with workspace_context(ws_a.id, actor=SYSTEM):
        Person.objects.filter(pk=papa.pk).update(archived_at=timezone.now())

    status, body = accept(Client(), token)

    assert (status, body["code"]) == (409, "invitation_person_taken")
    assert unused(token)
    assert not User.objects.filter(email="nieuw@example.nl").exists()
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Person.objects.get(pk=papa.pk).user_id is None


def test_email_bound_invitation_answers_the_mismatch_before_account_exists(
    ws_a: Workspace,
) -> None:
    UserFactory.create(email="ander@example.nl")
    token = invite(ws_a, email="bedoeld@example.nl")

    status, body = accept(Client(), token, account="new", email="Ander@example.nl")

    assert (status, body["code"]) == (403, "invitation_email_mismatch")
    assert unused(token)


def test_a_person_link_left_from_before_answers_409_and_keeps_nothing(ws_a: Workspace) -> None:
    # An account that is no member but still linked to a person here: a state the
    # unique index refuses to repeat for a second person (built directly in the table).
    former = UserFactory.create()
    old = person(ws_a, "Oud")
    papa = person(ws_a, "Papa")
    with workspace_context(ws_a.id, actor=SYSTEM):
        Person.objects.filter(pk=old.pk).update(user=former)
    token = invite(ws_a, person_id=str(papa.id))

    status, body = accept(
        Client(), token, account="existing", email=former.email, password=TEST_PASSWORD
    )

    assert (status, body["code"]) == (409, "invitation_person_taken")
    assert unused(token)
    assert not Membership.objects.filter(user=former).exists()
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Person.objects.get(pk=papa.pk).user_id is None
    assert workspace_rows(ws_a, "invitation_accepted") == []


def test_a_removal_between_password_check_and_join_still_ends_active(
    ws_a: Workspace, ws_b: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The account's last membership (in ws_b) goes after its password was checked and
    # before the join: the join must act on the account as it is then, under its lock.
    user = UserFactory.create()
    Membership.objects.create(user=user, workspace=ws_b, role="member")
    token = invite(ws_a)
    original = verify_password

    def check_then_lose_the_last_membership(
        request: HttpRequest, *, email: str, password: str
    ) -> User:
        checked = original(request, email=email, password=password)
        remove_member(
            request=browser_request(),
            workspace=ws_b,
            user_id=user.id,
            actor=AuditActor.for_user(admin_of(ws_b)),
            left=False,
        )
        assert not User.objects.get(pk=user.pk).is_active
        return checked

    monkeypatch.setattr(invitations, "verify_password", check_then_lose_the_last_membership)

    status, _ = accept(
        Client(), token, account="existing", email=user.email, password=TEST_PASSWORD
    )

    assert status == 200
    user.refresh_from_db()
    assert user.is_active
    assert Membership.objects.filter(user=user, workspace=ws_a).exists()
    (accepted,) = workspace_rows(ws_a, "invitation_accepted")
    assert accepted.values == {"role": "member", "new_account": False, "account_reactivated": True}


def test_joining_stops_other_invitations_bound_to_that_address(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    bound = invite(ws_a, role="admin", email="nieuw@example.nl")
    elsewhere = invite(ws_b, email="nieuw@example.nl")
    plain = invite(ws_a)

    assert accept(Client(), plain)[0] == 200

    assert Invitation.objects.get(token_hash=sha256(bound)).revoked_at is not None
    assert Invitation.objects.get(token_hash=sha256(elsewhere)).revoked_at is None
    assert lookup(Client(), bound) == (400, DEAD)
    revoked = workspace_rows(ws_a, "invitation_revoked")
    assert [r.values for r in revoked] == [{"reason": "superseded"}]


def browser_request() -> HttpRequest:
    request = RequestFactory().post("/api/v1/invitations/accept", REMOTE_ADDR="192.0.2.50")
    SessionMiddleware(lambda r: HttpResponse()).process_request(request)
    request.user = AnonymousUser()
    return request


def test_accept_locks_the_invitation_row(ws_a: Workspace) -> None:
    token = invite(ws_a)

    with CaptureQueriesContext(connection) as queries:
        accept_invitation(
            browser_request(),
            token=token,
            account="new",
            name="Anne",
            email="nieuw@example.nl",
            password=NEW_PASSWORD,
        )

    locked = [
        q["sql"]
        for q in queries.captured_queries
        if 'FROM "workspaces_invitation"' in q["sql"] and "FOR UPDATE" in q["sql"]
    ]
    # One lock by the token decides the race; the other links bound to the address are
    # revoked without waiting for a link another request is accepting.
    by_token = [sql for sql in locked if '"token_hash"' in sql.split("WHERE", 1)[1]]
    assert len(by_token) == 1
    assert all("SKIP LOCKED" in sql for sql in locked if sql not in by_token)


@pytest.mark.django_db(transaction=True)
def test_concurrent_accepts_make_one_membership(ws_a: Workspace) -> None:
    token = invite(ws_a)
    members_before = Membership.objects.filter(workspace=ws_a).count()
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def run(number: int) -> None:
        request = browser_request()
        try:
            barrier.wait(timeout=10)
            accept_invitation(
                request,
                token=token,
                account="new",
                name=f"Persoon {number}",
                email=f"tegelijk{number}@example.nl",
                password=NEW_PASSWORD,
            )
            outcomes.append("ok")
        except ApiError as exc:
            outcomes.append(exc.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(outcomes) == ["invitation_invalid", "ok"]
    assert Membership.objects.filter(workspace=ws_a).count() == members_before + 1
    assert User.objects.filter(email__startswith="tegelijk").count() == 1
    assert len(workspace_rows(ws_a, "invitation_accepted")) == 1


def test_invitation_token_never_logged(ws_a: Workspace, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    papa = person(ws_a, "Papa")
    admin = as_admin(ws_a)
    r = post_json(admin, f"/workspaces/{ws_a.id}/invitations", {"person_id": str(papa.id)})
    token = LINK.search(r.json()["link"])["token"]  # type: ignore[index]
    renewed = post_json(
        admin, f"/workspaces/{ws_a.id}/invitations/{Invitation.objects.get().id}/renew"
    )
    old_token, token = token, LINK.search(renewed.json()["link"])["token"]  # type: ignore[index]
    answers = [
        lookup(Client(), token),
        lookup(Client(), old_token),
        accept(Client(), token, password="kort"),
        accept(Client(), token, account="existing", email=admin_of(ws_a).email, password="fout"),
        accept(Client(), token),
        accept(Client(), token),
        lookup(Client(), token),
    ]

    assert [status for status, _ in answers] == [200, 400, 422, 401, 200, 400, 400]
    listed = admin.get(f"/api/v1/workspaces/{ws_a.id}/invitations").content.decode()
    for secret in (token, old_token, sha256(token), sha256(old_token)):
        assert secret not in caplog.text
        assert all(secret not in str(body) for _, body in answers)
        assert secret not in listed
        assert all(secret not in str(row.values) for row in ServerAuditEvent.objects.all())
        assert all(secret not in row.target_name for row in ServerAuditEvent.objects.all())
        with workspace_context(ws_a.id, actor=SYSTEM):
            rows = list(WorkspaceAuditEvent.objects.all())
        assert all(secret not in str(row.values) + row.target_name for row in rows)


def test_twenty_tries_per_address() -> None:
    client = Client(REMOTE_ADDR="192.0.2.9")
    statuses = [lookup(client, f"onbekend-{i}")[0] for i in range(10)]
    statuses += [accept(client, f"onbekend-{i}")[0] for i in range(11)]

    assert statuses[:20] == [400] * 20
    assert statuses[20] == 429
    assert lookup(Client(REMOTE_ADDR="192.0.2.10"), "onbekend")[0] == 400


def test_the_token_goes_in_the_body_only() -> None:
    assert post_json(Client(), "/invitations/lookup", {}).status_code == 422
    assert post_json(Client(), "/invitations/lookup", {"token": "x" * 101}).status_code == 422
