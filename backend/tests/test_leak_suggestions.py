"""Suggestions and history follow their letter (S 4.10, plan Task 17).

A proposal or a history line of a letter the member cannot see is invisible to them,
and acting on it answers like an unknown letter. Archiving a person still closes the
person's open proposals on letters the admin cannot see (they could never be accepted).
"""

import uuid

from django.test import Client

from apps.documents.models import DocumentEvent, DocumentSuggestion
from apps.workspaces.context import SYSTEM
from tests.documents_support import documents_url
from tests.visibility_support import Office, as_actor, office

NOT_FOUND = {"code": "document_not_found", "message": "We kunnen dit document niet vinden."}


def _letter_url(o: Office, name: str) -> str:
    return f"{documents_url(o.workspace.id)}/{o.letters[name].id}"


def _suggestion(o: Office, name: str) -> DocumentSuggestion:
    with as_actor(o.workspace, SYSTEM):
        return DocumentSuggestion.objects.get(document=o.letters[name])


def _states(o: Office) -> list[tuple[uuid.UUID, str]]:
    with as_actor(o.workspace, SYSTEM):
        return sorted(DocumentSuggestion.objects.values_list("id", "state"))


def test_suggestion_rows_of_a_hidden_letter_are_invisible() -> None:
    o = office()
    for user in o.users.values():
        sees = o.visible(user.id)
        with as_actor(o.workspace, user.id):
            rows = set(DocumentSuggestion.objects.values_list("document_id", flat=True))
            events = set(DocumentEvent.objects.values_list("document_id", flat=True))
        assert {o.name_of(d) for d in rows} == sees, user.name
        assert {o.name_of(d) for d in events} == sees, user.name
    with as_actor(o.workspace, o.hr.id):
        assert not DocumentSuggestion.objects.filter(document=o.letters["finance_letter"]).exists()


def test_accept_and_dismiss_of_a_hidden_suggestion_are_404(client: Client) -> None:
    o = office()
    suggestion = _suggestion(o, "finance_letter")
    before = _states(o)
    client.force_login(o.hr)
    for action in ("accept", "dismiss"):
        response = client.post(
            f"{_letter_url(o, 'finance_letter')}/suggestions/{suggestion.id}/{action}",
            content_type="application/json",
        )
        assert response.status_code == 404, action
        assert response.json() == NOT_FOUND
    assert _states(o) == before


def test_history_of_a_hidden_letter_is_404(client: Client) -> None:
    o = office()
    client.force_login(o.hr)
    response = client.get(f"{_letter_url(o, 'finance_letter')}/events")
    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    client.force_login(o.finance)
    assert client.get(f"{_letter_url(o, 'finance_letter')}/events").status_code == 200


def test_undo_of_a_hidden_event_is_404(client: Client) -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        event = DocumentEvent.objects.get(document=o.letters["finance_letter"])
        count = DocumentEvent.objects.count()
    client.force_login(o.hr)
    response = client.post(
        f"{_letter_url(o, 'finance_letter')}/events/{event.id}/undo",
        content_type="application/json",
    )
    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    with as_actor(o.workspace, SYSTEM):
        assert DocumentEvent.objects.count() == count


def test_archiving_a_person_closes_suggestions_on_hidden_letters(client: Client) -> None:
    """Anne (admin, A off) archives Jan; Jan's open proposal on Finance's letter, which
    she cannot see, goes as well."""
    o = office()
    jan = o.persons["Jan"]
    with as_actor(o.workspace, SYSTEM):
        proposal = DocumentSuggestion.objects.create(
            workspace=o.workspace,
            document=o.letters["finance_letter"],
            action=DocumentSuggestion.Action.ADD_ADDRESSEE,
            person=jan,
        )
    assert "finance_letter" not in o.visible(o.admin.id)
    client.force_login(o.admin)

    response = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/persons/{jan.id}",
        {"display_name": "Jan", "archived": True},
        content_type="application/json",
    )

    assert response.status_code == 200, response.json()
    assert response.json()["archived"] is True
    with as_actor(o.workspace, SYSTEM):
        assert not DocumentSuggestion.objects.filter(id=proposal.id).exists()
