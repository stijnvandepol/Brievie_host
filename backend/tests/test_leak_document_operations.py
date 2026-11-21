"""Every document operation answers a hidden letter like an unknown id (S 4.10, plan
Task 13, Review Focus 5).

The operations are taken from the OpenAPI schema: every path with `{document_id}`. A new
one (a later branch's) without a case here fails `test_every_document_operation_has_a_case`,
so it must prove it tells a member nothing about a colleague's letter: the same status
and the same body as for an id that does not exist, and nothing changed.

The duplicate pair operations take a pair id, not a letter id; their own tests cover a
pair with a letter the caller may not read (`test_duplicates_api.py`).
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pytest
from django.test import Client
from django.utils import timezone

from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from brievie.api import api
from tests.visibility_support import Office, as_actor, office

if TYPE_CHECKING:
    from tests.helpers import Response

_HTTP_METHODS: Final = {"get", "put", "post", "delete", "patch"}


@dataclass(frozen=True)
class Ids:
    """The hidden letter's own child ids: an operation on a child is asked with the real
    one, so only the letter's visibility can make the answer differ."""

    workspace_id: uuid.UUID
    suggestion_id: uuid.UUID
    event_id: uuid.UUID


type Case = Callable[[Client, Ids, uuid.UUID], "Response"]


def _url(ids: Ids, document_id: uuid.UUID, rest: str = "") -> str:
    return f"/api/v1/workspaces/{ids.workspace_id}/documents/{document_id}{rest}"


def _post(client: Client, url: str, body: dict[str, object] | None = None) -> "Response":
    return client.post(url, body or {}, content_type="application/json")


def _put(client: Client, url: str, body: dict[str, object]) -> "Response":
    return client.put(url, body, content_type="application/json")


CONFIRM: Final[dict[str, object]] = {
    "sender": "Gemeente",
    "document_type": "",
    "summary": "",
    "summary_by_ai": False,
    "tasks": [],
}

DOCUMENT_OPERATION_CASES: Final[dict[str, Case]] = {
    "getDocument": lambda c, ids, d: c.get(_url(ids, d)),
    "getDocumentAccess": lambda c, ids, d: c.get(_url(ids, d, "/access")),
    "getDocumentPage": lambda c, ids, d: c.get(_url(ids, d, "/pages/1")),
    "retryDocumentProcessing": lambda c, ids, d: _post(c, _url(ids, d, "/retry")),
    "confirmDocument": lambda c, ids, d: _post(c, _url(ids, d, "/confirm"), CONFIRM),
    "reopenDocument": lambda c, ids, d: _post(c, _url(ids, d, "/reopen")),
    "setDocumentAddressees": lambda c, ids, d: _put(
        c, _url(ids, d, "/addressees"), {"person_ids": []}
    ),
    "setDocumentLabels": lambda c, ids, d: _put(c, _url(ids, d, "/labels"), {"label_ids": []}),
    "acceptSuggestion": lambda c, ids, d: _post(
        c, _url(ids, d, f"/suggestions/{ids.suggestion_id}/accept")
    ),
    "dismissSuggestion": lambda c, ids, d: _post(
        c, _url(ids, d, f"/suggestions/{ids.suggestion_id}/dismiss")
    ),
    "listDocumentEvents": lambda c, ids, d: c.get(_url(ids, d, "/events")),
    "undoDocumentEvent": lambda c, ids, d: _post(c, _url(ids, d, f"/events/{ids.event_id}/undo")),
    "trashDocument": lambda c, ids, d: _post(c, _url(ids, d, "/trash")),
    "restoreDocument": lambda c, ids, d: _post(c, _url(ids, d, "/restore")),
    "purgeDocument": lambda c, ids, d: c.delete(f"/api/v1/workspaces/{ids.workspace_id}/trash/{d}"),
    "setDocumentVisibility": lambda c, ids, d: _put(
        c, _url(ids, d, "/visibility"), {"visibility": "restricted", "shared_user_ids": []}
    ),
}


def document_operations_from_schema() -> set[str]:
    """Every operation whose path names one letter by `{document_id}`."""
    schema = api.get_openapi_schema()
    found: set[str] = set()
    for path, item in schema["paths"].items():
        if "{document_id}" not in path:
            continue
        for method, operation in item.items():
            if method in _HTTP_METHODS:
                found.add(str(operation["operationId"]))
    return found


def _ids(o: Office, letter: Document) -> Ids:
    with as_actor(o.workspace, SYSTEM):
        suggestion = DocumentSuggestion.objects.filter(document=letter).first()
        event = DocumentEvent.objects.filter(document=letter).first()
    assert suggestion is not None
    assert event is not None
    return Ids(workspace_id=o.workspace.id, suggestion_id=suggestion.id, event_id=event.id)


def _snapshot(o: Office, letter: Document) -> tuple[object, ...]:
    """Everything an operation could change on the letter, read as the system."""
    with as_actor(o.workspace, SYSTEM):
        document = Document.objects.values(
            "status",
            "updated_at",
            "trashed_at",
            "purge_started_at",
            "addressee_ids",
            "sender",
            "confirmed_at",
            "confirmed_by_id",
        ).get(id=letter.id)
        return (
            document,
            sorted(DocumentEvent.objects.filter(document=letter).values_list("id", flat=True)),
            sorted(DocumentLabel.objects.filter(document=letter).values_list("id", flat=True)),
            sorted(DocumentAddressee.objects.filter(document=letter).values_list("id", flat=True)),
            sorted(DocumentSuggestion.objects.filter(document=letter).values_list("id", "state")),
            sorted(Task.objects.filter(document=letter).values_list("id", "status")),
        )


def _answer(response: "Response") -> tuple[int, object]:
    is_json = response.headers.get("Content-Type", "").startswith("application/json")
    return response.status_code, response.json() if is_json else None


def test_every_document_operation_has_a_case() -> None:
    assert document_operations_from_schema() == set(DOCUMENT_OPERATION_CASES)


@pytest.mark.parametrize("operation", sorted(DOCUMENT_OPERATION_CASES))
@pytest.mark.parametrize("who", ["plain", "hr", "viewer", "admin"])
@pytest.mark.parametrize("in_trash", [False, True])
def test_every_document_operation_answers_a_hidden_letter_like_an_unknown_id(
    operation: str, who: str, in_trash: bool
) -> None:
    o = office()
    letter = o.letters["finance_letter"]
    if in_trash:  # Femke put it in her trash: still nobody else's business.
        with as_actor(o.workspace, SYSTEM):
            Document.objects.filter(id=letter.id).update(
                trashed_at=timezone.now(), trashed_by=o.finance
            )
    assert "finance_letter" not in o.visible(o.users[who].id)
    ids = _ids(o, letter)
    case = DOCUMENT_OPERATION_CASES[operation]
    client = Client()
    client.force_login(o.users[who])
    before = _snapshot(o, letter)

    hidden = _answer(case(client, ids, letter.id))
    unknown = _answer(case(client, ids, uuid.uuid4()))

    assert hidden == unknown
    assert hidden[0] in (403, 404), hidden
    assert _snapshot(o, letter) == before


@pytest.mark.parametrize("operation", ["getDocument", "listDocumentEvents"])
def test_the_owner_reads_it(operation: str) -> None:
    """The control: the same calls work for Femke, so the 404s above are about sight."""
    o = office()
    letter = o.letters["finance_letter"]
    client = Client()
    client.force_login(o.finance)
    response = DOCUMENT_OPERATION_CASES[operation](client, _ids(o, letter), letter.id)
    assert response.status_code == 200
