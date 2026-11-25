"""An own document is found by its title (archive plan Task 3, AR9): the title joins the
search vector with the sender's weight."""

import logging
import uuid
from datetime import date

import pytest
from django.db import connection

from apps.accounts.models import User
from apps.documents.models import Document
from apps.documents.search import SearchFilters, SearchResult, search_documents
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.archive_support import membership_of, own_document
from tests.my_tasks_support import household
from tests.visibility_support import as_actor, letter, office


def _search(workspace: Workspace, user: User, q: str) -> SearchResult:
    member = membership_of(workspace, user)
    with as_actor(workspace, user.id):
        return search_documents(member, q=q, filters=SearchFilters(), limit=30, offset=0)


def _ids(result: SearchResult) -> list[uuid.UUID]:
    return [hit.document.id for hit in result.hits]


def test_a_title_is_found_by_word_start() -> None:
    h = household()
    document = own_document(h.workspace, title="Garantiebewijs wasmachine", scanned_by=h.anne)
    own_document(h.workspace, title="Paspoort", scanned_by=h.anne)
    assert _ids(_search(h.workspace, h.anne, "garantie")) == [document.id]
    assert _ids(_search(h.workspace, h.anne, "wasmachine")) == [document.id]


def test_title_ranks_above_the_text() -> None:
    h = household()
    in_text = own_document(
        h.workspace,
        title="Polis",
        ocr_text="Deze polis dekt ook de wasmachine in de schuur.",
        scanned_by=h.anne,
    )
    in_title = own_document(h.workspace, title="Wasmachine bon", scanned_by=h.anne)
    assert _ids(_search(h.workspace, h.anne, "wasmachine")) == [in_title.id, in_text.id]


def test_search_filters_still_use_the_visible_documents_seam() -> None:
    o = office()
    hidden = own_document(o.workspace, on=[o.persons["Finance"]], title="Kopieerapparaat aankoop")
    seen = own_document(o.workspace, on=[o.persons["Piet"]], title="Bakfiets aankoop")
    assert _ids(_search(o.workspace, o.plain, "aankoop")) == [seen.id]
    assert _ids(_search(o.workspace, o.plain, "kopieerapparaat")) == []
    assert _ids(_search(o.workspace, o.finance, "kopieerapparaat")) == [hidden.id]


def test_the_search_vector_holds_the_title() -> None:
    h = household()
    document = own_document(h.workspace, title="Diploma middelbare school", scanned_by=h.anne)
    with as_actor(h.workspace, SYSTEM), connection.cursor() as cursor:
        cursor.execute(
            "SELECT search_vector::text FROM documents_document WHERE id = %s", [document.id]
        )
        row = cursor.fetchone()
    assert row is not None
    assert "'diploma':" in row[0]


def test_search_respects_the_new_filters_and_logs_no_term(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = household()
    own = own_document(
        h.workspace, title="Fietsnota", scanned_by=h.anne, letter_date=date(2024, 2, 1)
    )
    post = letter(h.workspace, on=[h.persons["Anne"]])
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=post.id).update(sender="Fietsnota winkel")
    member = membership_of(h.workspace, h.anne)
    caplog.set_level(logging.DEBUG)

    def found(filters: SearchFilters) -> list[uuid.UUID]:
        with as_actor(h.workspace, h.anne.id):
            result = search_documents(member, q="fietsnota", filters=filters, limit=30, offset=0)
        return _ids(result)

    assert set(found(SearchFilters())) == {own.id, post.id}
    assert found(SearchFilters(kind="document")) == [own.id]
    assert found(SearchFilters(kind="letter")) == [post.id]
    assert found(SearchFilters(kind="document", year=2024)) == [own.id]
    assert found(SearchFilters(kind="document", year=2023)) == []
    assert found(SearchFilters(archived=True, kind="document")) == [own.id]
    assert "fietsnota" not in caplog.text.lower()
