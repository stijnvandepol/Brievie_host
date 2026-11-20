"""The search vector lives in its own table (`DocumentSearch`, one row per letter).

PostgreSQL keeps it: a trigger on the letter writes the row when the letter is stored and
when its sender, summary or text changes, from the stored (already BSN-masked) fields. The
row follows its letter: the same visibility, and it goes when the letter goes.
"""

import uuid

from django.contrib.postgres.search import SearchQuery
from django.db import connection

from apps.documents.models import Document, DocumentSearch
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.visibility_support import as_actor, office


def _letter(workspace: Workspace, **fields: str) -> Document:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Document.objects.create(
            workspace=workspace, client_id=uuid.uuid4(), page_count=1, status="done", **fields
        )


def _matches(workspace: Workspace, document: Document, word: str) -> bool:
    with workspace_context(workspace.id, actor=SYSTEM):
        return DocumentSearch.objects.filter(
            document=document, search_vector=SearchQuery(word, config="dutch")
        ).exists()


def test_a_new_letter_gets_its_search_row(ws_a: Workspace) -> None:
    document = _letter(ws_a, sender="Waterschap", summary="Aanslag", ocr_text="Kwijtschelding")

    with workspace_context(ws_a.id, actor=SYSTEM):
        row = DocumentSearch.objects.get(document=document)
    assert row.workspace_id == ws_a.id
    for word in ("waterschap", "aanslag", "kwijtschelding"):
        assert _matches(ws_a, document, word), word


def test_a_changed_sender_summary_or_text_updates_the_row(ws_a: Workspace) -> None:
    document = _letter(ws_a, sender="Waterschap")

    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(
            sender="Belastingdienst", summary="Teruggave", ocr_text="Voorlopige aanslag"
        )

    assert not _matches(ws_a, document, "waterschap")
    for word in ("belastingdienst", "teruggave", "voorlopige"):
        assert _matches(ws_a, document, word), word
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert DocumentSearch.objects.filter(document=document).count() == 1


def test_the_row_goes_with_its_letter(ws_a: Workspace) -> None:
    document = _letter(ws_a, sender="Waterschap")

    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).delete()
        assert not DocumentSearch.objects.filter(document_id=document.id).exists()


def test_the_letter_table_has_no_search_vector_any_more(db: None) -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'documents_document'"
        )
        columns = {row[0] for row in c.fetchall()}
    assert "search_vector" not in columns
    assert "ocr_text" in columns


def test_a_member_sees_only_the_search_rows_of_letters_they_see(db: None) -> None:
    o = office()
    for user in o.users.values():
        sees = {o.letters[name].id for name in o.visible(user.id)}
        with as_actor(o.workspace, user.id):
            rows = set(DocumentSearch.objects.values_list("document_id", flat=True))
        assert rows == sees, user.name
