"""Sorters, and every member change of sight through the same steps (plan Task 38, S 4.4,
4.7): preview, confirmation, audit, notices."""

import uuid
from typing import TYPE_CHECKING

from django.test import Client

from apps.accounts.models import AccountNotice, User
from apps.audit.models import WorkspaceAuditEvent
from apps.people.models import DepartmentMember, Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.factories import UserFactory
from tests.visibility_support import Office, as_actor, office

if TYPE_CHECKING:
    from tests.helpers import Response


def _as(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


def _ws(o: Office, rest: str) -> str:
    return f"/api/v1/workspaces/{o.workspace.id}{rest}"


def _put(client: Client, url: str, body: dict[str, object]) -> "Response":
    return client.put(url, body, content_type="application/json")


def _post(client: Client, url: str, body: dict[str, object]) -> "Response":
    return client.post(url, body, content_type="application/json")


def _token(client: Client, o: Office, body: dict[str, object]) -> str:
    r = _post(client, _ws(o, "/visibility/preview"), body)
    assert r.status_code == 200, r.json()
    return str(r.json()["preview_token"])


def _notices(user: User) -> list[tuple[str, dict[str, str]]]:
    return [(n.code, n.values) for n in AccountNotice.objects.filter(user=user).order_by("id")]


def _audit(o: Office, code: str) -> list[dict[str, object]]:
    with as_actor(o.workspace, SYSTEM):
        return [
            dict(e.values)
            for e in WorkspaceAuditEvent.objects.filter(workspace_id=o.workspace.id, code=code)
        ]


def _sorts(o: Office, user: User) -> bool:
    return Membership.objects.get(workspace=o.workspace, user=user).sorts_post


def test_flag_a_sorter_with_confirmation(client: Client) -> None:
    o = office()
    _as(client, o.admin)
    url = _ws(o, f"/members/{o.plain.id}/sorts-post")
    r = _put(client, url, {"sorts_post": True})
    assert r.status_code == 409
    assert r.json()["code"] == "confirmation_required"
    assert not _sorts(o, o.plain)

    token = _token(
        client,
        o,
        {
            "member_change": "sorts_post",
            "member_user_id": str(o.plain.id),
            "member_sorts_post": True,
        },
    )
    r = _put(client, url, {"sorts_post": True, "confirmed": True, "preview_token": token})
    assert r.status_code == 200, r.json()
    assert r.json()["sorts_post"] is True
    assert _audit(o, "post_sorter_changed") == [{"member": "Piet", "value": True}]
    assert "unassigned" in o.visible(o.plain.id)
    assert ("sight_widened", {"category": "unassigned"}) in _notices(o.plain)


def test_unflagging_one_of_two_sorters_needs_no_confirmation(client: Client) -> None:
    o = office()
    Membership.objects.filter(workspace=o.workspace, user=o.plain).update(sorts_post=True)
    r = _put(
        _as(client, o.admin), _ws(o, f"/members/{o.sorter.id}/sorts-post"), {"sorts_post": False}
    )
    assert r.status_code == 200, r.json()
    assert not _sorts(o, o.sorter)
    assert "unassigned" not in o.visible(o.sorter.id)
    assert ("sight_narrowed", {"category": "unassigned"}) in _notices(o.sorter)


def test_viewer_cannot_sort(client: Client) -> None:
    o = office()
    r = _put(
        _as(client, o.admin),
        _ws(o, f"/members/{o.viewer.id}/sorts-post"),
        {"sorts_post": True, "confirmed": True},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "viewer_cannot_sort"
    assert not _sorts(o, o.viewer)


def test_making_a_sorter_viewer_clears_the_flag(client: Client) -> None:
    o = office()
    _as(client, o.admin)
    url = _ws(o, f"/members/{o.sorter.id}/role")
    # Anne becomes the fallback sorter and gains: a confirmation (S 4.4).
    assert _put(client, url, {"role": "viewer"}).status_code == 409
    token = _token(
        client,
        o,
        {"member_change": "role", "member_user_id": str(o.sorter.id), "member_role": "viewer"},
    )
    r = _put(client, url, {"role": "viewer", "confirmed": True, "preview_token": token})
    assert r.status_code == 200, r.json()
    assert r.json()["sorts_post"] is False
    assert not _sorts(o, o.sorter)
    assert {"member": "Sandra", "value": False} in _audit(o, "post_sorter_changed")
    assert "unassigned" in o.visible(o.admin.id)


def test_promotion_while_admins_read_all_needs_confirmation(client: Client) -> None:
    o = office()
    o.state(e=False, d=True, a=True)
    _as(client, o.admin)
    url = _ws(o, f"/members/{o.plain.id}/role")
    r = _put(client, url, {"role": "admin"})
    assert r.status_code == 409
    assert r.json()["code"] == "confirmation_required"
    token = _token(
        client,
        o,
        {"member_change": "role", "member_user_id": str(o.plain.id), "member_role": "admin"},
    )
    r = _put(client, url, {"role": "admin", "confirmed": True, "preview_token": token})
    assert r.status_code == 200, r.json()
    for user in o.users.values():
        assert ("admins_read_all_on", {}) in _notices(user), user.name
    assert ("sight_widened", {"category": "all_as_admin"}) in _notices(o.plain)


def test_department_add_needs_confirmation_and_notifies(client: Client) -> None:
    o = office()
    finance = o.persons["Finance"]
    _as(client, o.admin)
    url = _ws(o, f"/persons/{finance.id}/members")
    r = _post(client, url, {"user_id": str(o.plain.id)})
    assert r.status_code == 409
    assert r.json()["code"] == "confirmation_required"
    token = _token(
        client,
        o,
        {
            "member_change": "department_add",
            "member_user_id": str(o.plain.id),
            "member_person_id": str(finance.id),
        },
    )
    r = _post(client, url, {"user_id": str(o.plain.id), "confirmed": True, "preview_token": token})
    assert r.status_code == 200, r.json()
    assert "finance_letter" in o.visible(o.plain.id)
    assert _notices(o.plain) == [
        ("sight_widened", {"category": "department", "department": "Finance"})
    ]
    # Anne added someone else: no self-grant notice for Femke.
    assert _notices(o.finance) == []


def test_last_department_member_removed_sends_post_to_sorters(client: Client) -> None:
    o = office()
    finance = o.persons["Finance"]
    r = _as(client, o.admin).delete(_ws(o, f"/persons/{finance.id}/members/{o.finance.id}"))
    assert r.status_code == 200, r.json()
    assert "finance_letter" not in o.visible(o.finance.id)
    assert "finance_letter" in o.visible(o.sorter.id)
    assert ("sight_widened", {"category": "department", "department": "Finance"}) in _notices(
        o.sorter
    )
    assert ("sight_narrowed", {"category": "department", "department": "Finance"}) in _notices(
        o.finance
    )


def test_unlink_sends_post_to_sorters_without_confirmation(client: Client) -> None:
    o = office()
    piet = o.persons["Piet"]
    r = _put(_as(client, o.plain), _ws(o, f"/persons/{piet.id}/member"), {"user_id": None})
    assert r.status_code == 200, r.json()
    assert "own_piet" not in o.visible(o.plain.id)
    assert "own_piet" in o.visible(o.sorter.id)
    assert ("sight_widened", {"category": "person", "person": "Piet"}) in _notices(o.sorter)
    assert ("sight_narrowed", {"category": "person", "person": "Piet"}) in _notices(o.plain)


def test_link_needs_confirmation(client: Client) -> None:
    o = office()
    newcomer = UserFactory.create(name="Nina")
    Membership.objects.create(user=newcomer, workspace=o.workspace, role="member")
    jan = o.persons["Jan"]
    _as(client, o.admin)
    url = _ws(o, f"/persons/{jan.id}/member")
    r = _put(client, url, {"user_id": str(newcomer.id)})
    assert r.status_code == 409
    token = _token(
        client,
        o,
        {
            "member_change": "link",
            "member_user_id": str(newcomer.id),
            "member_person_id": str(jan.id),
        },
    )
    r = _put(client, url, {"user_id": str(newcomer.id), "confirmed": True, "preview_token": token})
    assert r.status_code == 200, r.json()
    with as_actor(o.workspace, SYSTEM):
        assert Person.objects.get(id=jan.id).user_id == newcomer.id
    # Sandra sorted Jan's post: it is Nina's now.
    assert ("sight_narrowed", {"category": "person", "person": "Jan"}) in _notices(o.sorter)


def test_self_grant_notifies_department_members(client: Client) -> None:
    o = office()
    finance = o.persons["Finance"]
    _as(client, o.admin)
    body: dict[str, object] = {
        "member_change": "department_add",
        "member_user_id": str(o.admin.id),
        "member_person_id": str(finance.id),
    }
    token = _token(client, o, body)
    r = _post(
        client,
        _ws(o, f"/persons/{finance.id}/members"),
        {"user_id": str(o.admin.id), "confirmed": True, "preview_token": token},
    )
    assert r.status_code == 200, r.json()
    assert ("self_grant", {"category": "department", "department": "Finance"}) in _notices(
        o.finance
    )
    assert not [n for n in _notices(o.hr) if n[0] == "self_grant"]


def test_self_flagging_notifies_the_other_admins(client: Client) -> None:
    o = office()
    Membership.objects.filter(workspace=o.workspace, user=o.hr).update(role="admin")
    _as(client, o.admin)
    body: dict[str, object] = {
        "member_change": "sorts_post",
        "member_user_id": str(o.admin.id),
        "member_sorts_post": True,
    }
    token = _token(client, o, body)
    r = _put(
        client,
        _ws(o, f"/members/{o.admin.id}/sorts-post"),
        {"sorts_post": True, "confirmed": True, "preview_token": token},
    )
    assert r.status_code == 200, r.json()
    assert ("self_grant", {"category": "unassigned"}) in _notices(o.hr)
    assert not [n for n in _notices(o.admin) if n[0] == "self_grant"]


def test_role_change_with_stale_preview_token_is_409(client: Client) -> None:
    o = office()
    o.state(e=False, d=True, a=True)
    _as(client, o.admin)
    token = _token(
        client,
        o,
        {"member_change": "role", "member_user_id": str(o.plain.id), "member_role": "admin"},
    )
    # Another admin puts Hans in Finance in between.
    with as_actor(o.workspace, SYSTEM):
        DepartmentMember.objects.create(
            workspace=o.workspace, department=o.persons["Finance"], user=o.hr
        )
    r = _put(
        client,
        _ws(o, f"/members/{o.plain.id}/role"),
        {"role": "admin", "confirmed": True, "preview_token": token},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "preview_outdated"
    assert Membership.objects.get(workspace=o.workspace, user=o.plain).role == "member"
    assert _audit(o, "member_role_changed") == []


def test_preview_of_a_member_change_changes_nothing(client: Client) -> None:
    o = office()
    finance = o.persons["Finance"]
    _as(client, o.admin)
    r = _post(
        client,
        _ws(o, "/visibility/preview"),
        {
            "member_change": "department_add",
            "member_user_id": str(o.plain.id),
            "member_person_id": str(finance.id),
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert [(c["name"], c["gains"], c["losses"]) for c in body["changes"]] == [
        ("Piet", ["post van Finance"], [])
    ]
    assert body["needs_confirmation"] is True
    assert body["letter_count"] == 1
    with as_actor(o.workspace, SYSTEM):
        assert not DepartmentMember.objects.filter(user=o.plain).exists()
    assert _audit(o, "department_member_added") == []
    assert _notices(o.plain) == []
    assert o.visible(o.plain.id) == {"own_piet"}


def test_preview_of_a_removal_changes_nothing(client: Client) -> None:
    o = office()
    _as(client, o.admin)
    r = _post(
        client,
        _ws(o, "/visibility/preview"),
        {"member_change": "remove", "member_user_id": str(o.finance.id)},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    names = {c["name"] for c in body["changes"]}
    assert names == {"Femke", "Sandra"}
    assert body["needs_confirmation"] is False
    assert Membership.objects.filter(workspace=o.workspace, user=o.finance).exists()
    with as_actor(o.workspace, SYSTEM):
        assert Person.objects.get(id=o.persons["Femke"].id).user_id == o.finance.id


def test_preview_refuses_what_the_writer_refuses(client: Client) -> None:
    o = office()
    _as(client, o.admin)
    r = _post(
        client,
        _ws(o, "/visibility/preview"),
        {"member_change": "role", "member_user_id": str(o.admin.id), "member_role": "member"},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "last_admin"
    r = _post(
        client,
        _ws(o, "/visibility/preview"),
        {"member_change": "role", "member_user_id": str(uuid.uuid4()), "member_role": "admin"},
    )
    assert r.status_code == 404
    r = _post(client, _ws(o, "/visibility/preview"), {"member_change": "role"})
    assert r.status_code == 422


def test_member_removal_tells_the_sorters(client: Client) -> None:
    o = office()
    r = _as(client, o.admin).delete(_ws(o, f"/members/{o.finance.id}"))
    assert r.status_code == 204
    assert "finance_letter" in o.visible(o.sorter.id)
    assert [n for n in _notices(o.sorter) if n[0] == "sight_widened"]
    assert not [n for n in _notices(o.finance) if n[0].startswith("sight_")]


def test_member_out_lists_departments_and_flag(client: Client) -> None:
    o = office()
    r = _as(client, o.admin).get(_ws(o, "/members"))
    assert r.status_code == 200
    members = {m["name"]: m for m in r.json()}
    assert members["Femke"]["department_ids"] == [str(o.persons["Finance"].id)]
    assert members["Sandra"]["sorts_post"] is True
    assert members["Piet"]["sorts_post"] is False
    assert members["Piet"]["department_ids"] == []
