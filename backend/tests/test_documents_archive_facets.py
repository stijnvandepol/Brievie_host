"""The archive's facets (archive plan Task 7, spec S 5, S 8): counted only over what the
member sees, reviewed only, never a trashed or private document they may not read."""

from datetime import date

from django.test import Client

from apps.accounts.models import User
from apps.documents import services
from apps.documents.models import Document
from apps.documents.services import ArchiveFacets, Facet
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.archive_support import membership_of, own_document, private_own_document
from tests.documents_support import trashed
from tests.my_tasks_support import household
from tests.visibility_support import as_actor, letter, office


def _facets(workspace: Workspace, user: User) -> ArchiveFacets:
    member = membership_of(workspace, user)
    with as_actor(workspace, user.id):
        return services.list_archive_facets(member)


def _as_dict(facets: list[Facet]) -> dict[str, int]:
    return {facet.value: facet.count for facet in facets}


def test_facets_count_kinds_types_years_and_senders() -> None:
    h = household()
    post = letter(h.workspace, on=[h.persons["Anne"]])
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=post.id).update(
            sender="Waterbedrijf", document_type="invoice", letter_date=date(2025, 3, 1)
        )
    own_document(h.workspace, scanned_by=h.anne, letter_date=date(2024, 1, 1))
    own_document(
        h.workspace, scanned_by=h.anne, document_type="diploma", letter_date=date(2024, 5, 1)
    )
    letter(h.workspace, on=[], status="needs_review")
    facets = _facets(h.workspace, h.anne)
    assert _as_dict(facets.kinds) == {"letter": 1, "document": 2}
    assert _as_dict(facets.types) == {"invoice": 1, "identity": 1, "diploma": 1}
    # By count, then by value; kinds in the order of `Document.Kind`.
    assert [f.value for f in facets.types] == ["diploma", "identity", "invoice"]
    assert [f.value for f in facets.kinds] == ["letter", "document"]
    assert [f.value for f in facets.years] == ["2025", "2024"]
    assert _as_dict(facets.years) == {"2025": 1, "2024": 2}
    assert _as_dict(facets.senders) == {"Waterbedrijf": 1}


def test_facets_count_only_what_the_member_sees() -> None:
    o = office()
    own_document(o.workspace, on=[o.persons["Finance"]], document_type="contract")
    own_document(o.workspace, on=[o.persons["Piet"]], document_type="diploma")
    for user in o.users.values():
        member = membership_of(o.workspace, user)
        with as_actor(o.workspace, user.id):
            seen = list(
                services.list_documents(member, archive=services.ArchiveFilters(archived=True))
            )
            facets = services.list_archive_facets(member)
        kinds: dict[str, int] = {}
        types: dict[str, int] = {}
        for document in seen:
            kinds[document.kind] = kinds.get(document.kind, 0) + 1
            if document.document_type:
                types[document.document_type] = types.get(document.document_type, 0) + 1
        assert _as_dict(facets.kinds) == kinds, user.name
        assert _as_dict(facets.types) == types, user.name
    plain = _facets(o.workspace, o.plain)
    assert "contract" not in _as_dict(plain.types)
    assert _as_dict(plain.types).get("diploma") == 1


def test_facets_never_include_trashed_or_private_hidden_documents() -> None:
    o = office()
    private_own_document(o.workspace, on=[o.persons["Femke"]], document_type="medical")
    gone = own_document(o.workspace, on=[o.persons["Piet"]], document_type="warranty")
    trashed(gone)
    for user in (o.plain, o.admin, o.sorter, o.hr, o.viewer):
        types = _as_dict(_facets(o.workspace, user).types)
        assert "warranty" not in types, user.name
        assert "medical" not in types, user.name
    assert _as_dict(_facets(o.workspace, o.finance).types).get("medical") == 1


def test_senders_facet_is_capped_and_ordered_by_count() -> None:
    h = household()
    names = {}
    for index in range(services.SENDERS_MAX + 5):
        for _ in range(2 if index == 7 else 1):
            names[letter(h.workspace, on=[h.persons["Anne"]]).id] = f"Afzender {index:02d}"
    with as_actor(h.workspace, SYSTEM):
        for document_id, name in names.items():
            Document.objects.filter(id=document_id).update(sender=name)
    senders = _facets(h.workspace, h.anne).senders
    assert len(senders) == services.SENDERS_MAX
    assert senders[0] == Facet("Afzender 07", 2)
    assert [s.value for s in senders[1:3]] == ["Afzender 00", "Afzender 01"]


def test_the_api_answers_the_facets(client: Client) -> None:
    h = household()
    own_document(h.workspace, scanned_by=h.anne)
    client.force_login(h.piet)
    response = client.get(f"/api/v1/workspaces/{h.workspace.id}/documents/archive-facets")
    assert response.status_code == 200
    body = response.json()
    assert body["kinds"] == [{"value": "document", "count": 1}]
    assert body["types"] == [{"value": "identity", "count": 1}]
    assert body["senders"] == []
