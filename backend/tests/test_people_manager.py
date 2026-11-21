"""`setPersonManager` ("Beheerd door", Mensen slice C, plan Task 13): a widening with a
confirmation, never to the admin themself on private post, and ended by activation or by
the manager leaving."""

import re
import uuid

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import AccountNotice
from apps.audit.models import WorkspaceAuditEvent
from apps.documents import visibility
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from tests.factories import TEST_PASSWORD
from tests.helpers import post_json
from tests.members_support import as_user, bearer, paired
from tests.visibility_support import Office, as_actor, office, private_letter, readers, sees

LINK = re.compile(r"#(?P<token>[A-Za-z0-9_-]{43})$")


@pytest.fixture(autouse=True)
def _fast_hashing_and_public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def _url(o: Office, person: str) -> str:
    return f"/api/v1/workspaces/{o.workspace.id}/persons/{o.persons[person].id}/manager"


def _put(client: Client, o: Office, person: str, **body: object) -> "tuple[int, dict[str, object]]":
    payload = {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in body.items()}
    r = client.put(_url(o, person), payload, content_type="application/json")
    return r.status_code, r.json()


def _preview(client: Client, o: Office, person: str, user_id: uuid.UUID | None) -> str:
    r = post_json(
        client,
        f"/workspaces/{o.workspace.id}/visibility/preview",
        {
            "member_change": "manage",
            "member_user_id": None if user_id is None else str(user_id),
            "member_person_id": str(o.persons[person].id),
        },
    )
    assert r.status_code == 200, r.json()
    return str(r.json()["preview_token"])


def _manager(o: Office, person: str) -> uuid.UUID | None:
    with as_actor(o.workspace, SYSTEM):
        return Person.objects.get(id=o.persons[person].id).managed_by_id


def _manage(o: Office, person: str, user_id: uuid.UUID) -> None:
    admin = as_user(o.admin)
    token = _preview(admin, o, person, user_id)
    status, body = _put(admin, o, person, user_id=user_id, confirmed=True, preview_token=token)
    assert status == 200, body


def test_set_manager_needs_confirmation_when_gaining() -> None:
    o = office()  # E off: Jan's letter goes to the sorter
    admin = as_user(o.admin)
    status, body = _put(admin, o, "Jan", user_id=o.plain.id)
    assert (status, body["code"]) == (409, "confirmation_required")
    assert _manager(o, "Jan") is None
    token = _preview(admin, o, "Jan", o.plain.id)
    status, body = _put(admin, o, "Jan", user_id=o.plain.id, confirmed=True, preview_token=token)
    assert status == 200, body
    assert _manager(o, "Jan") == o.plain.id
    letter = o.letters["person_without_account"]
    assert sees(o.plain, letter)
    assert not sees(o.sorter, letter)
    assert AccountNotice.objects.filter(user=o.plain, code="sight_widened").exists()
    assert AccountNotice.objects.filter(user=o.sorter, code="sight_narrowed").exists()


def test_clearing_needs_no_preview_and_sends_the_post_back() -> None:
    o = office()
    _manage(o, "Jan", o.plain.id)
    status, body = _put(as_user(o.admin), o, "Jan", user_id=None)
    assert status == 200, body
    assert _manager(o, "Jan") is None
    letter = o.letters["person_without_account"]
    assert not sees(o.plain, letter)
    assert sees(o.sorter, letter)


def test_stale_preview_token_is_409() -> None:
    o = office()
    admin = as_user(o.admin)
    token = _preview(admin, o, "Jan", o.plain.id)
    # Something of the rule changes in between: Hans now sorts post too.
    r = admin.put(
        f"/api/v1/workspaces/{o.workspace.id}/members/{o.hr.id}/sorts-post",
        {"sorts_post": True, "confirmed": True, "preview_token": _sorts_token(admin, o)},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    status, body = _put(admin, o, "Jan", user_id=o.plain.id, confirmed=True, preview_token=token)
    assert (status, body["code"]) == (409, "preview_outdated")


def _sorts_token(admin: Client, o: Office) -> str:
    r = post_json(
        admin,
        f"/workspaces/{o.workspace.id}/visibility/preview",
        {"member_change": "sorts_post", "member_user_id": str(o.hr.id), "member_sorts_post": True},
    )
    assert r.status_code == 200, r.json()
    return str(r.json()["preview_token"])


def test_cannot_manage_own_private_post_person() -> None:
    o = office()
    private_letter(o.workspace, addressees=[o.persons["Jan"]], scanned_by=o.sorter)
    admin = as_user(o.admin)
    status, body = _put(admin, o, "Jan", user_id=o.admin.id, confirmed=True, preview_token="x")
    assert (status, body["code"]) == (409, "cannot_grant_self")
    assert _manager(o, "Jan") is None


def test_member_gets_403() -> None:
    o = office()
    status, body = _put(as_user(o.plain), o, "Jan", user_id=o.plain.id)
    assert (status, body["code"]) == (403, "forbidden")


def test_device_token_gets_401() -> None:
    o = office()
    _device, token = paired(o.admin)
    r = Client().put(
        _url(o, "Jan"),
        {"user_id": str(o.plain.id)},
        content_type="application/json",
        headers=bearer(token),
    )
    assert r.status_code == 401


def test_department_and_person_with_account_are_refused() -> None:
    o = office()
    admin = as_user(o.admin)
    assert _put(admin, o, "Finance", user_id=o.plain.id)[1]["code"] == "is_a_department"
    assert _put(admin, o, "Femke", user_id=o.plain.id)[1]["code"] == "person_has_account"
    stranger = uuid.uuid4()
    assert _put(admin, o, "Jan", user_id=stranger)[1]["code"] == "member_not_found"


def test_activation_ends_management_and_tells_manager() -> None:
    o = office()
    _manage(o, "Jan", o.plain.id)
    admin = as_user(o.admin)
    r = post_json(
        admin,
        f"/workspaces/{o.workspace.id}/visibility/preview",
        {
            "member_change": "invite",
            "member_role": "member",
            "member_person_id": str(o.persons["Jan"].id),
        },
    )
    assert r.status_code == 200, r.json()
    r = post_json(
        admin,
        f"/workspaces/{o.workspace.id}/invitations",
        {
            "person_id": str(o.persons["Jan"].id),
            "confirmed": True,
            "preview_token": r.json()["preview_token"],
        },
    )
    assert r.status_code == 201, r.json()
    match = LINK.search(r.json()["link"])
    assert match is not None
    r = post_json(
        Client(),
        "/invitations/accept",
        {
            "token": match["token"],
            "account": "new",
            "name": "Jan",
            "email": "jan@example.nl",
            "password": TEST_PASSWORD,
        },
    )
    assert r.status_code == 200, r.json()
    assert _manager(o, "Jan") is None
    assert not sees(o.plain, o.letters["person_without_account"])
    assert AccountNotice.objects.filter(user=o.plain, code="sight_narrowed").exists()
    with as_actor(o.workspace, SYSTEM):
        assert WorkspaceAuditEvent.objects.filter(code="person_manager_cleared").exists()


def test_removing_manager_sends_post_to_sorters() -> None:
    o = office()
    _manage(o, "Jan", o.plain.id)
    r = as_user(o.admin).delete(f"/api/v1/workspaces/{o.workspace.id}/members/{o.plain.id}")
    assert r.status_code == 204, r.content
    assert _manager(o, "Jan") is None
    assert sees(o.sorter, o.letters["person_without_account"])
    with as_actor(o.workspace, SYSTEM):
        (row,) = WorkspaceAuditEvent.objects.filter(code="person_manager_cleared")
    assert row.values == {"member": "Piet"}


def test_switching_off_with_manager_keeps_private_post_readable() -> None:
    """Femke is switched off; her private letter would have no reader. Hans manages her
    post afterwards and reads it (the dialog's "Laat de post beheren door ...")."""
    o = office()
    private = o.letters["private_femke"]
    r = as_user(o.admin).delete(f"/api/v1/workspaces/{o.workspace.id}/members/{o.finance.id}")
    assert r.status_code == 204, r.content
    assert not sees(o.hr, private)
    _manage(o, "Femke", o.hr.id)
    assert ("Hans", "managed") in readers(private)
    assert sees(o.hr, private)
    with as_actor(o.workspace, SYSTEM):
        assert o.hr.id in visibility.viewers([private.id])[private.id]


def test_audit_rows_name_the_manager() -> None:
    o = office()
    _manage(o, "Jan", o.plain.id)
    _put(as_user(o.admin), o, "Jan", user_id=None)
    with as_actor(o.workspace, SYSTEM):
        rows = list(
            WorkspaceAuditEvent.objects.filter(
                code__in=["person_manager_set", "person_manager_cleared"]
            ).order_by("created_at", "id")
        )
    assert [(r.code, r.target_name, r.values) for r in rows] == [
        ("person_manager_set", "Jan", {"member": "Piet"}),
        ("person_manager_cleared", "Jan", {"member": "Piet"}),
    ]


def test_the_list_says_who_manages() -> None:
    o = office()
    _manage(o, "Jan", o.plain.id)
    rows = as_user(o.admin).get(f"/api/v1/workspaces/{o.workspace.id}/people").json()
    jan = next(r for r in rows if r["display_name"] == "Jan")
    assert jan["managed_by_user_id"] == str(o.plain.id)
    rows = as_user(o.plain).get(f"/api/v1/workspaces/{o.workspace.id}/people").json()
    assert next(r for r in rows if r["display_name"] == "Jan")["managed_by_user_id"] is None
