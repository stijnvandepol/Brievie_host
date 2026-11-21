"""Each person's account state, derived in one place (spec 3.1, 3.4; plan Task 3)."""

import itertools
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django import Settings

from apps.common.errors import ApiError
from apps.devices.models import Device
from apps.people.accounts import (
    AccountState,
    PersonAccount,
    account_state,
    list_people,
    person_has_private_post,
)
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Invitation, Membership, Workspace
from tests.factories import TEST_PASSWORD
from tests.helpers import post_json
from tests.members_support import as_user, paired
from tests.visibility_support import Home, as_actor, home, office, private_letter


@pytest.fixture(autouse=True)
def _fast_hashing_and_public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def _membership(workspace: Workspace, user_id: uuid.UUID) -> Membership:
    return Membership.objects.select_related("user").get(workspace=workspace, user_id=user_id)


def _people(h: Home, viewer: uuid.UUID | None = None, state: str = "") -> dict[str, PersonAccount]:
    membership = _membership(h.workspace, viewer or h.anne.id)
    with as_actor(h.workspace, membership.user_id):
        return {
            a.person.display_name: a
            for a in list_people(h.workspace, viewer=membership, state=state)
        }


def _invite(h: Home, person: Person) -> Invitation:
    r = post_json(
        as_user(h.anne), f"/workspaces/{h.workspace.id}/invitations", {"person_id": str(person.id)}
    )
    assert r.status_code == 201, r.json()
    return Invitation.objects.get(id=r.json()["invitation"]["id"])


def _remove(h: Home, user: uuid.UUID) -> None:
    r = as_user(h.anne).delete(f"/api/v1/workspaces/{h.workspace.id}/members/{user}")
    assert r.status_code == 204, r.content


def _add_person(h: Home, name: str) -> Person:
    with as_actor(h.workspace, SYSTEM):
        return Person.objects.create(workspace=h.workspace, display_name=name)


@pytest.mark.parametrize(
    ("linked", "deactivated", "invited"), list(itertools.product((False, True), repeat=3))
)
def test_account_state_precedence(linked: bool, deactivated: bool, invited: bool) -> None:
    state = account_state(
        user_id=uuid.uuid4() if linked else None,
        deactivated_at=timezone.now() if deactivated else None,
        has_open_invitation=invited,
    )
    if linked:
        assert state == AccountState.ACTIVE
    elif invited:
        assert state == AccountState.INVITED
    elif deactivated:
        assert state == AccountState.DEACTIVATED
    else:
        assert state == AccountState.INACTIVE


def test_list_people_states() -> None:
    h = home()
    _remove(h, h.bob.id)
    opa = _add_person(h, "Opa")
    _invite(h, opa)
    tante = _add_person(h, "Tante")
    expired = _invite(h, tante)
    Invitation.objects.filter(id=expired.id).update(expires_at=timezone.now() - timedelta(days=1))

    people = _people(h)
    states = {name: a.state for name, a in people.items()}
    assert states == {
        "Anne": "active",
        "Bob": "deactivated",
        "Carla": "active",
        "Oma": "inactive",
        "Opa": "invited",
        "Tante": "invited",
    }
    assert people["Tante"].invitation_expired is True
    assert people["Opa"].invitation_expired is False
    assert people["Opa"].invitation_id is not None
    assert people["Anne"].role == "admin"


def test_switched_off_person_with_open_invitation_is_invited() -> None:
    h = home()
    _remove(h, h.bob.id)
    _invite(h, h.persons["Bob"])
    assert _people(h)["Bob"].state == "invited"


def test_list_people_leaves_out_departments() -> None:
    o = office()
    membership = _membership(o.workspace, o.admin.id)
    with as_actor(o.workspace, o.admin.id):
        names = {a.person.display_name for a in list_people(o.workspace, viewer=membership)}
    assert not names & {"Finance", "HR", "Directie"}
    assert {"Anne", "Jan", "Piet"} <= names


def test_last_sign_in_from_last_login(client: Client) -> None:
    h = home()
    r = post_json(client, "/auth/login", {"email": h.bob.email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.json()
    bob = _people(h)["Bob"]
    signed_in = bob.last_sign_in_at
    assert signed_in is not None
    assert timezone.now() - signed_in < timedelta(minutes=5)
    assert bob.email == h.bob.email


def test_never_signed_in_is_null() -> None:
    h = home()
    people = _people(h)
    assert people["Carla"].last_sign_in_at is None
    assert people["Carla"].last_app_use_at is None
    assert people["Oma"].last_sign_in_at is None


def test_last_app_use_ignores_revoked_devices() -> None:
    h = home()
    seen = timezone.now() - timedelta(hours=2)
    later = timezone.now() - timedelta(minutes=5)
    kept, _token = paired(h.bob, "iPhone")
    gone, _other = paired(h.bob, "iPad")
    Device.objects.filter(id=kept.id).update(last_seen_at=seen)
    Device.objects.filter(id=gone.id).update(last_seen_at=later, revoked_at=timezone.now())
    assert _people(h)["Bob"].last_app_use_at == seen


def test_switched_off_shows_former_account(client: Client) -> None:
    h = home()
    r = post_json(client, "/auth/login", {"email": h.bob.email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.json()
    _remove(h, h.bob.id)
    bob = _people(h)["Bob"]
    assert bob.state == "deactivated"
    assert bob.email == h.bob.email
    assert bob.last_sign_in_at is not None
    assert bob.deactivated_at is not None
    assert bob.role == ""


def test_non_admin_gets_no_account_facts(client: Client) -> None:
    h = home()
    r = post_json(client, "/auth/login", {"email": h.anne.email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.json()
    _invite(h, h.persons["Oma"])
    for account in _people(h, viewer=h.bob.id).values():
        assert account.email == ""
        assert account.last_sign_in_at is None
        assert account.last_app_use_at is None
        assert account.deactivated_at is None
        assert account.invitation_id is None
        assert account.invitation_expires_at is None
        assert account.can_manage_account is False
        assert account.role == ""


def test_non_admin_sees_only_active_or_inactive() -> None:
    h = home()
    _remove(h, h.carla.id)
    _invite(h, h.persons["Oma"])
    states = {n: a.state for n, a in _people(h, viewer=h.bob.id).items()}
    assert states == {"Anne": "active", "Bob": "active", "Carla": "inactive", "Oma": "inactive"}
    assert set(_people(h, viewer=h.bob.id, state="inactive")) == {"Carla", "Oma"}
    with pytest.raises(ApiError) as caught:
        _people(h, viewer=h.bob.id, state="invited")
    assert caught.value.code == "validation_error"


def test_person_has_private_post_is_boolean_only() -> None:
    o = office()  # E off: Anne (admin, A off) cannot read Hans's private letter
    private_letter(o.workspace, addressees=[o.persons["Hans"]], scanned_by=o.sorter)
    with as_actor(o.workspace, o.admin.id):
        assert person_has_private_post(o.workspace, o.persons["Hans"].id) is True
        assert person_has_private_post(o.workspace, o.persons["Piet"].id) is False
        with pytest.raises(ApiError) as caught:
            person_has_private_post(o.workspace, o.persons["Finance"].id)
    assert caught.value.code == "person_not_found"


def test_list_people_query_count() -> None:
    """Constant in the number of people: no query per person."""
    h = home()
    _remove(h, h.bob.id)
    _invite(h, h.persons["Oma"])
    membership = _membership(h.workspace, h.anne.id)

    def count() -> int:
        with as_actor(h.workspace, h.anne.id), CaptureQueriesContext(connection) as queries:
            list_people(h.workspace, viewer=membership)
        return len(queries)

    few = count()
    for number in range(8):
        person = _add_person(h, f"Neef {number}")
        if number % 2:
            _invite(h, person)
    assert count() == few
    # Measured 24 for an admin (the people, their accounts, devices and invitations, and
    # listMembers' rule for `can_manage_account`); what counts is that it stays flat.
    assert few <= 26
