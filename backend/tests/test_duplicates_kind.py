"""Duplicates are looked for within a kind (archive spec AR5): a letter and an own document
are never a pair; two scans of one own document are."""

import pytest

from apps.documents.models import Document
from apps.pipeline.steps import run_pipeline
from apps.workspaces.models import Workspace
from tests.duplicates_support import LETTER, TODAY, fresh, letter, pairs_of, rescan

pytestmark = pytest.mark.usefixtures("without_preset_rules")


def _arrive_own(workspace: Workspace, text: str) -> Document:
    document = letter(
        workspace,
        text,
        status=Document.Status.PROCESSING,
        kind=Document.Kind.DOCUMENT,
        title="Jaarafrekening",
        document_type="other",
    )
    run_pipeline(workspace.id, document.id, provider_factory=lambda _: None, today=lambda: TODAY)
    return fresh(document)


def test_duplicates_only_between_the_same_kind(ws_a: Workspace) -> None:
    letter(ws_a, LETTER, status=Document.Status.DONE)

    own = _arrive_own(ws_a, rescan(LETTER))

    assert own.status == Document.Status.DONE
    assert pairs_of(ws_a) == []


def test_two_identical_documents_get_the_dubbel_block(ws_a: Workspace) -> None:
    first = letter(
        ws_a,
        LETTER,
        status=Document.Status.DONE,
        kind=Document.Kind.DOCUMENT,
        title="Jaarafrekening",
        document_type="other",
    )

    second = _arrive_own(ws_a, rescan(LETTER))

    [row] = pairs_of(ws_a)
    assert (row.first_id, row.second_id) == tuple(sorted((first.id, second.id)))


def test_a_letter_does_not_pair_with_an_earlier_own_document(ws_a: Workspace) -> None:
    letter(
        ws_a,
        LETTER,
        status=Document.Status.DONE,
        kind=Document.Kind.DOCUMENT,
        title="Jaarafrekening",
        document_type="other",
    )
    arrived = letter(ws_a, rescan(LETTER), status=Document.Status.PROCESSING)
    run_pipeline(ws_a.id, arrived.id, provider_factory=lambda _: None, today=lambda: TODAY)

    assert pairs_of(ws_a) == []
