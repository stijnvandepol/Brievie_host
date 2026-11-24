"""Archive plan Task 1: a document's kind, title, end date and retention date, and the
types of own documents (spec S 4)."""

from collections.abc import Iterator

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.documents.models import (
    DOCUMENT_TYPES,
    LETTER_TYPES,
    REMIND_DAYS,
    SENSITIVE_TYPES,
    Document,
)
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.documents_support import make_document
from tests.visibility_support import as_actor

OLD_TYPES = {
    "invoice",
    "assessment",
    "decision",
    "bill",
    "reminder",
    "demand",
    "policy",
    "annual_statement",
    "payslip",
    "inspection",
    "contract",
    "receipt",
    "information",
    "other",
}


def test_new_document_is_a_letter_with_no_title_or_dates(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    with as_actor(ws_a, SYSTEM):
        stored = Document.objects.get(id=document.id)
    assert stored.kind == "letter"
    assert stored.title == ""
    assert stored.expires_on is None
    assert stored.retain_until is None
    assert stored.remind_days == 30


def test_document_types_are_a_subset_of_the_model_types() -> None:
    assert set(DOCUMENT_TYPES) <= set(Document.Type.values)
    assert set(LETTER_TYPES) == OLD_TYPES
    assert {"identity", "diploma", "warranty", "tax_return", "medical"} <= set(DOCUMENT_TYPES)
    assert "invoice" not in DOCUMENT_TYPES


def test_sensitive_types_are_document_types() -> None:
    assert set(SENSITIVE_TYPES) == {"identity", "medical", "payslip", "tax_return"}
    assert set(SENSITIVE_TYPES) <= set(DOCUMENT_TYPES)


def test_remind_days_are_the_four_choices() -> None:
    assert REMIND_DAYS == (7, 30, 60, 90)


def _migrate(targets: list[tuple[str, str]]) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)


def _leaves() -> list[tuple[str, str]]:
    executor = MigrationExecutor(connection)
    leaves: list[tuple[str, str]] = executor.loader.graph.leaf_nodes()
    return leaves


@pytest.fixture
def migrated_forward_afterwards() -> Iterator[None]:
    yield
    _migrate(_leaves())


@pytest.mark.django_db(transaction=True)
def test_existing_rows_become_letters(migrated_forward_afterwards: None) -> None:
    from apps.workspaces.services import create_workspace
    from tests.factories import UserFactory

    workspace = create_workspace(name="Oud", kind="home", admin=UserFactory.create())
    document = make_document(workspace)

    _migrate([("documents", "0024_letter_status")])
    _migrate(_leaves())

    with as_actor(workspace, SYSTEM):
        stored = Document.objects.get(id=document.id)
    assert (stored.kind, stored.title, stored.remind_days) == ("letter", "", 30)


def test_a_review_may_not_give_a_letter_an_own_documents_type(ws_a: Workspace) -> None:
    from apps.common.errors import ApiError
    from apps.documents import review
    from apps.documents.schemas import ConfirmIn

    document = make_document(ws_a, status="needs_review")
    data = ConfirmIn(
        sender="Gemeente", document_type="diploma", summary="", summary_by_ai=False, tasks=[]
    )
    with pytest.raises(ApiError) as caught:
        review._validate(data, document)
    assert (caught.value.status, caught.value.code) == (422, "validation_error")
