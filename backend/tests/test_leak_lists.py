"""Lists and counts show no colleague's post (S 4.10, plan Task 12).

In `office()`, own post (E off, D on, A off, Sandra sorts): Hans (HR) and Piet must not
learn of Finance's letter through the post list, the Te doen lists, the badges, a
filter or a count; Femke (Finance) still sees it, and Sandra gets the post nobody with
an account reads (P4: the `unassigned` state keeps its meaning "no addressee at all").
"""

import uuid

from django.test import Client

from apps.accounts.models import User
from apps.documents.models import Document, DocumentLabel
from apps.workspaces.context import SYSTEM
from tests.documents_support import documents_url
from tests.visibility_support import Office, as_actor, office


def _names(o: Office, client: Client, query: str = "") -> set[str]:
    response = client.get(f"{documents_url(o.workspace.id)}{query}")
    assert response.status_code == 200
    return {o.name_of(uuid.UUID(item["id"])) for item in response.json()}


def _as(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


def _set_status(o: Office, name: str, status: str) -> None:
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=o.letters[name].id).update(status=status)


def _label_id(o: Office) -> uuid.UUID:
    with as_actor(o.workspace, SYSTEM):
        return DocumentLabel.objects.get(document=o.letters["finance_letter"]).label_id


def test_post_list_omits_finance_post(client: Client) -> None:
    o = office()
    for user in (o.hr, o.plain, o.viewer, o.admin, o.sorter):
        names = _names(o, _as(client, user))
        assert "finance_letter" not in names, user.name
        assert "private_femke" not in names, user.name
        assert names == o.visible(user.id), user.name


def test_te_doen_lists_omit_it(client: Client) -> None:
    o = office()
    _as(client, o.hr)
    _set_status(o, "finance_letter", Document.Status.NEEDS_REVIEW)
    assert "finance_letter" not in _names(o, client, "?inbox_state=review")
    _set_status(o, "finance_letter", Document.Status.FAILED)
    assert "finance_letter" not in _names(o, client, "?inbox_state=review")
    _set_status(o, "finance_letter", Document.Status.PROCESSING)
    assert _names(o, client, "?inbox_state=new") == set()
    _set_status(o, "finance_letter", Document.Status.DONE)
    assert _names(o, client, "?inbox_state=unassigned") == set()

    _set_status(o, "finance_letter", Document.Status.NEEDS_REVIEW)
    assert "finance_letter" in _names(o, _as(client, o.finance), "?inbox_state=review")


def test_inbox_counts_omit_it(client: Client) -> None:
    o = office()
    _set_status(o, "finance_letter", Document.Status.NEEDS_REVIEW)
    url = f"/api/v1/workspaces/{o.workspace.id}/inbox/counts"

    hans = _as(client, o.hr).get(url).json()
    assert (hans["new"], hans["review"], hans["unassigned"], hans["automatic"]) == (0, 0, 0, 0)

    femke = _as(client, o.finance).get(url).json()
    # Finance's letter and the one Piet scanned for Femke.
    assert femke["review"] == 2


def test_person_filter_on_femke_shows_nothing(client: Client) -> None:
    o = office()
    query = f"?person_id={o.persons['Femke'].id}"
    for user in (o.hr, o.viewer, o.admin, o.sorter):
        assert _names(o, _as(client, user), query) == set(), user.name
    # Piet scanned a letter on Femke's name that is still in review: rule 5.
    assert _names(o, _as(client, o.plain), query) == {"scanned_in_review"}
    assert _names(o, _as(client, o.finance), query) == {"scanned_in_review", "private_femke"}


def test_department_filter_on_finance_shows_nothing(client: Client) -> None:
    o = office()
    query = f"?person_id={o.persons['Finance'].id}"
    for user in (o.hr, o.plain, o.viewer, o.admin, o.sorter):
        assert _names(o, _as(client, user), query) == set(), user.name
    assert _names(o, _as(client, o.finance), query) == {"finance_letter"}


def test_label_filter_omits_it(client: Client) -> None:
    o = office()
    query = f"?label_id={_label_id(o)}"
    assert _names(o, _as(client, o.hr), query) == {"hr_letter"}
    assert "finance_letter" in _names(o, _as(client, o.finance), query)


def test_persons_document_count_counts_only_visible(client: Client) -> None:
    o = office()
    response = _as(client, o.hr).get(f"/api/v1/workspaces/{o.workspace.id}/persons")
    assert response.status_code == 200
    counts = {item["id"]: item["document_count"] for item in response.json()}
    assert counts[str(o.persons["HR"].id)] == 1
    for name in ("Finance", "Femke", "Piet", "Jan", "Directie"):
        assert counts[str(o.persons[name].id)] == 0, name

    response = _as(client, o.finance).get(f"/api/v1/workspaces/{o.workspace.id}/persons")
    counts = {item["id"]: item["document_count"] for item in response.json()}
    assert counts[str(o.persons["Finance"].id)] == 1
    assert counts[str(o.persons["Femke"].id)] == 2  # her private letter and Piet's scan

    response = _as(client, o.plain).get(f"/api/v1/workspaces/{o.workspace.id}/persons")
    counts = {item["id"]: item["document_count"] for item in response.json()}
    assert counts[str(o.persons["Femke"].id)] == 1  # only the letter Piet scanned


def test_labels_document_count_counts_only_visible(client: Client) -> None:
    o = office()
    label_id = str(_label_id(o))
    for user in (o.hr, o.plain, o.finance, o.sorter):
        response = _as(client, user).get(f"/api/v1/workspaces/{o.workspace.id}/labels")
        assert response.status_code == 200
        counts = {item["id"]: item["document_count"] for item in response.json()}
        assert counts[label_id] == len(o.visible(user.id)), user.name


def test_sorter_sees_unassigned_in_te_doen(client: Client) -> None:
    o = office()
    _as(client, o.sorter)
    assert _names(o, client) == {"unassigned", "only_directie", "person_without_account"}
    # P4: the chip filters letters without any addressee; the others show with their name.
    assert _names(o, client, "?inbox_state=unassigned") == {"unassigned"}
    counts = client.get(f"/api/v1/workspaces/{o.workspace.id}/inbox/counts").json()
    assert counts["unassigned"] == 1


def test_the_owner_still_sees_it(client: Client) -> None:
    o = office()
    names = _names(o, _as(client, o.finance))
    assert {"finance_letter", "private_femke"} <= names
    response = client.get(f"{documents_url(o.workspace.id)}/{o.letters['finance_letter'].id}")
    assert response.status_code == 200
