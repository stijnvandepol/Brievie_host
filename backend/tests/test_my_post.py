"""Mijn post and unread letters in the lists (navigation plan Task 4, spec S 5, S 9)."""

import uuid

from django.test import Client

from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.documents_support import bearer_for, documents_url
from tests.my_tasks_support import household
from tests.visibility_support import as_actor, letter, office

JSON = "application/json"


def _ids(client: Client, workspace: Workspace, query: str = "", **headers: str) -> list[str]:
    r = client.get(f"{documents_url(workspace.id)}{query}", headers=headers or None)
    assert r.status_code == 200, r.content
    return [row["id"] for row in r.json()]


def _as(user: object) -> Client:
    client = Client()
    client.force_login(user)  # type: ignore[arg-type]
    return client


def test_addressed_to_me_lists_only_own_person_letters() -> None:
    h = household()
    piet = letter(h.workspace, on=[h.persons["Piet"]])
    both = letter(h.workspace, on=[h.persons["Piet"], h.persons["Anne"]])
    letter(h.workspace, on=[h.persons["Anne"]])
    letter(h.workspace, on=[])
    assert set(_ids(_as(h.piet), h.workspace, "?addressed_to_me=true")) == {
        str(piet.id),
        str(both.id),
    }


def test_addressed_to_me_includes_managed_persons() -> None:
    h = household()
    with as_actor(h.workspace, SYSTEM):
        Person.objects.filter(id=h.persons["Jan"].id).update(managed_by=h.piet)
    jan = letter(h.workspace, on=[h.persons["Jan"]])
    assert _ids(_as(h.piet), h.workspace, "?addressed_to_me=true") == [str(jan.id)]


def test_addressed_to_me_leaves_out_department_letters() -> None:
    o = office()
    own = o.letters["hr_letter"]  # on the HR department; Hans is in HR
    assert str(own.id) not in _ids(_as(o.hr), o.workspace, "?addressed_to_me=true")
    # "Ook mijn afdeling" (B2) brings them in.
    assert str(own.id) in _ids(
        _as(o.hr), o.workspace, "?addressed_to_me=true&include_departments=true"
    )


def test_member_without_person_gets_empty_my_post() -> None:
    h = household()
    letter(h.workspace, on=[h.persons["Piet"]])
    with as_actor(h.workspace, SYSTEM):
        Person.objects.filter(id=h.persons["Piet"].id).update(user=None)
    assert _ids(_as(h.piet), h.workspace, "?addressed_to_me=true") == []


def test_unread_only_after_mark_read() -> None:
    h = household()
    first = letter(h.workspace, on=[h.persons["Piet"]])
    second = letter(h.workspace, on=[h.persons["Piet"]])
    client = _as(h.piet)
    assert set(_ids(client, h.workspace, "?unread_only=true")) == {str(first.id), str(second.id)}
    r = client.post(f"{documents_url(h.workspace.id)}/{first.id}/read", content_type=JSON)
    assert r.status_code == 204
    assert _ids(client, h.workspace, "?unread_only=true") == [str(second.id)]
    # Anne's state is her own.
    assert len(_ids(_as(h.anne), h.workspace, "?unread_only=true")) == 2
    r = client.delete(f"{documents_url(h.workspace.id)}/{first.id}/read")
    assert r.status_code == 204
    assert len(_ids(client, h.workspace, "?unread_only=true")) == 2


def test_mark_many_read_endpoint() -> None:
    h = household()
    first = letter(h.workspace, on=[h.persons["Piet"]])
    second = letter(h.workspace, on=[h.persons["Piet"]])
    client = _as(h.piet)
    r = client.post(
        f"{documents_url(h.workspace.id)}/read",
        {"document_ids": [str(first.id), str(second.id), str(uuid.uuid4())]},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json() == {"marked": 2}
    assert _ids(client, h.workspace, "?unread_only=true") == []
    r = client.post(
        f"{documents_url(h.workspace.id)}/read",
        {"document_ids": [str(uuid.uuid4()) for _ in range(201)]},
        content_type="application/json",
    )
    assert r.status_code == 422


def test_marking_works_with_the_app() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    headers = bearer_for(h.piet)
    client = Client()
    r = client.post(
        f"{documents_url(h.workspace.id)}/{document.id}/read", content_type=JSON, headers=headers
    )
    assert r.status_code == 204
    assert _ids(client, h.workspace, "?unread_only=true", **headers) == []


def test_mark_read_of_an_unknown_letter_is_404() -> None:
    h = household()
    r = _as(h.piet).post(f"{documents_url(h.workspace.id)}/{uuid.uuid4()}/read", content_type=JSON)
    assert r.status_code == 404
    assert r.json()["code"] == "document_not_found"


def test_summary_and_detail_carry_unread() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    client = _as(h.piet)
    [row] = client.get(documents_url(h.workspace.id)).json()
    assert row["unread"] is True
    detail = client.get(f"{documents_url(h.workspace.id)}/{document.id}").json()
    assert detail["unread"] is True
    client.post(f"{documents_url(h.workspace.id)}/{document.id}/read", content_type=JSON)
    [row] = client.get(documents_url(h.workspace.id)).json()
    assert row["unread"] is False
    detail = client.get(f"{documents_url(h.workspace.id)}/{document.id}").json()
    assert detail["unread"] is False


def test_private_letter_of_other_never_in_my_post() -> None:
    o = office()
    private = o.letters["private_femke"]
    for user in (o.admin, o.sorter, o.plain, o.hr):
        ids = _ids(_as(user), o.workspace, "?addressed_to_me=true&include_departments=true")
        assert str(private.id) not in ids
        assert str(private.id) not in _ids(_as(user), o.workspace, "?unread_only=true")


def test_list_without_new_params_unchanged() -> None:
    o = office()
    for user in (o.admin, o.sorter, o.plain, o.finance):
        client = _as(user)
        plain = _ids(client, o.workspace)
        assert plain == _ids(
            client,
            o.workspace,
            "?addressed_to_me=false&unread_only=false&include_departments=false",
        )
