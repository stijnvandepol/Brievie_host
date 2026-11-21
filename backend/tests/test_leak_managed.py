"""Managed post leaks nowhere (Mensen slice C, plan Task 14).

In `office()` (own post, E off) Piet manages Jan's post. Hans, another member, must not
learn of Jan's letters through any path; Piet sees them; the moment management ends (in
the same transaction as the change) Piet sees none of them any more.
"""

import uuid

from django.db import transaction
from django.test import Client

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.documents.models import Document, DocumentAddressee
from apps.people import services as people
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from tests.documents_support import documents_url
from tests.visibility_support import Office, as_actor, office, sees

WORD = "zeldzaamwoord"


def _managed_office() -> tuple[Office, Document]:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        Person.objects.filter(id=o.persons["Jan"].id).update(managed_by_id=o.plain.id)
        searchable = Document.objects.create(
            workspace=o.workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            status="done",
            ocr_text=f"Brief voor Jan over {WORD}.",
            addressee_ids=[o.persons["Jan"].id],
        )
        DocumentAddressee.objects.create(
            workspace=o.workspace, document=searchable, person=o.persons["Jan"]
        )
    return o, searchable


def _as(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


def _listed(o: Office, client: Client, query: str = "") -> set[uuid.UUID]:
    response = client.get(f"{documents_url(o.workspace.id)}{query}")
    assert response.status_code == 200
    return {uuid.UUID(item["id"]) for item in response.json()}


def _found(o: Office, client: Client) -> set[uuid.UUID]:
    response = client.post(
        f"{documents_url(o.workspace.id)}/search", {"q": WORD}, content_type="application/json"
    )
    assert response.status_code == 200
    return {uuid.UUID(item["id"]) for item in response.json()["items"]}


def test_another_member_learns_of_no_managed_letter(client: Client) -> None:
    o, searchable = _managed_office()
    jan = {o.letters["person_without_account"].id, searchable.id}
    hans = _as(client, o.hr)
    assert not jan & _listed(o, hans)
    assert not jan & _listed(o, hans, f"?person_id={o.persons['Jan'].id}")
    assert not jan & _found(o, hans)
    for letter_id in jan:
        r = hans.get(f"{documents_url(o.workspace.id)}/{letter_id}")
        assert (r.status_code, r.json()["code"]) == (404, "document_not_found")
    counts = hans.get(f"/api/v1/workspaces/{o.workspace.id}/persons").json()
    assert {c["id"]: c["document_count"] for c in counts}[str(o.persons["Jan"].id)] == 0
    tasks = hans.get(f"/api/v1/workspaces/{o.workspace.id}/tasks?status=all").json()
    assert not {t["document_id"] for t in tasks} & {str(i) for i in jan}


def test_the_sorter_no_longer_gets_it_but_the_manager_does(client: Client) -> None:
    o, searchable = _managed_office()
    jan = {o.letters["person_without_account"].id, searchable.id}
    assert not jan & _listed(o, _as(client, o.sorter))
    piet = _as(client, o.plain)
    assert jan <= _listed(o, piet)
    assert searchable.id in _found(o, piet)
    counts = piet.get(f"/api/v1/workspaces/{o.workspace.id}/persons").json()
    assert {c["id"]: c["document_count"] for c in counts}[str(o.persons["Jan"].id)] == 2


def test_the_manager_loses_all_the_moment_management_ends() -> None:
    o, searchable = _managed_office()
    letters = [o.letters["person_without_account"], searchable]
    assert all(sees(o.plain, letter) for letter in letters)
    with transaction.atomic():
        with as_actor(o.workspace, o.admin.id):
            people.set_manager(
                workspace=o.workspace,
                person_id=o.persons["Jan"].id,
                user_id=None,
                actor=AuditActor.for_user(o.admin),
            )
        # Inside the same transaction already.
        assert not any(sees(o.plain, letter) for letter in letters)
    assert all(sees(o.sorter, letter) for letter in letters)
