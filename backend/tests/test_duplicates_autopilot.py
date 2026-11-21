"""The autopilot's rail for duplicates (plan Task 3): an open pair, or a duplicate check
that failed, stops the AI's confirm; labels may still be applied."""

import pytest

from apps.documents import duplicates
from apps.documents.models import Document, DocumentLabel, Extraction
from apps.labels.models import Label
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.duplicates_support import fresh, letter, pairs_of
from tests.test_autopilot_confirm import TEXT, Answering, run, switch_on

pytestmark = pytest.mark.usefixtures("without_preset_rules")

UNRELATED = (
    "Gemeente Assen\nAfdeling Burgerzaken\nUw aanvraag voor een parkeervergunning is "
    "ontvangen. Wij behandelen uw aanvraag binnen acht weken."
)


def arrive(workspace: Workspace, provider: Answering) -> Document:
    document = letter(workspace, TEXT, status=Document.Status.PROCESSING)
    run(workspace, document, provider)
    return fresh(document)


def tasks_of(workspace: Workspace, document: Document) -> int:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Task.objects.filter(document=document).count()


def test_open_pair_blocks_auto_confirm(ws_a: Workspace) -> None:
    switch_on(ws_a)
    letter(ws_a, TEXT)

    second = arrive(ws_a, Answering())

    assert len(pairs_of(ws_a)) == 1
    assert second.status == Document.Status.NEEDS_REVIEW
    assert second.confirmed_by_ai is False
    assert tasks_of(ws_a, second) == 0


def test_open_pair_still_allows_auto_organise(ws_a: Workspace) -> None:
    switch_on(ws_a, organise=True)
    with workspace_context(ws_a.id, actor=SYSTEM):
        label = Label.objects.create(workspace=ws_a, name="Belasting")
    letter(ws_a, TEXT)

    second = arrive(ws_a, Answering(labels={"Belasting": 0.9}))

    assert second.status == Document.Status.NEEDS_REVIEW
    with workspace_context(ws_a.id, actor=SYSTEM):
        link = DocumentLabel.objects.get(document=second, label=label)
    assert link.automatic is True


def test_failed_detection_blocks_auto_confirm(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    switch_on(ws_a)

    def broken(document: Document) -> int:
        raise RuntimeError("kapot")

    monkeypatch.setattr(duplicates, "detect", broken)

    second = arrive(ws_a, Answering())

    assert pairs_of(ws_a) == []
    assert second.status == Document.Status.NEEDS_REVIEW
    assert second.confirmed_by_ai is False
    assert tasks_of(ws_a, second) == 0
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Extraction.objects.filter(document=second).exists()


def test_no_pair_confirms_as_before(ws_a: Workspace) -> None:
    switch_on(ws_a)
    letter(ws_a, UNRELATED)

    second = arrive(ws_a, Answering())

    assert pairs_of(ws_a) == []
    assert second.status == Document.Status.DONE
    assert second.confirmed_by_ai is True
    assert tasks_of(ws_a, second) == 1


def test_pair_with_trashed_other_does_not_block(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    switch_on(ws_a)
    first = letter(ws_a, TEXT)
    detect = duplicates.detect

    def then_trash(document: Document) -> int:
        made = detect(document)
        Document.objects.filter(id=first.id).update(trashed_at=document.created_at)
        return made

    monkeypatch.setattr(duplicates, "detect", then_trash)

    second = arrive(ws_a, Answering())

    assert len(pairs_of(ws_a)) == 1
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert duplicates.has_open_pair(second.id) is False
        assert duplicates.has_open_pair(first.id) is True
    assert second.status == Document.Status.DONE
    assert second.confirmed_by_ai is True
