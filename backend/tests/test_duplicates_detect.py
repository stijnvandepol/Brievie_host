"""Detection on arrival (plan Task 3): a rescan gets a pair, a reminder and another letter
do not; the pool, the window, the cap, the memory of "no"; the re-check after a confirm."""

import logging
from datetime import date
from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.documents import duplicates, review
from apps.documents.models import Document, DuplicateCandidate, Extraction
from apps.documents.schemas import ConfirmIn
from apps.pipeline import steps
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import pair, trashed
from tests.duplicates_support import (
    LETTER,
    OTHER_LETTER,
    REMINDER,
    Saying,
    arrive,
    facts_answer,
    fresh,
    letter,
    pairs_of,
    rescan,
)
from tests.visibility_support import as_actor, office

pytestmark = pytest.mark.usefixtures("without_preset_rules")


def test_second_scan_gets_a_pair(ws_a: Workspace) -> None:
    first = letter(ws_a, LETTER, status=Document.Status.DONE)

    second = arrive(ws_a, rescan(LETTER))

    [row] = pairs_of(ws_a)
    assert (row.first_id, row.second_id) == tuple(sorted((first.id, second.id)))
    assert row.state == DuplicateCandidate.State.OPEN
    assert row.text_similarity >= Decimal("0.800")
    assert row.number_overlap == Decimal("1.000")
    assert second.status == Document.Status.NEEDS_REVIEW


def test_different_letter_gets_none(ws_a: Workspace) -> None:
    letter(ws_a, LETTER)

    arrive(ws_a, OTHER_LETTER)

    assert pairs_of(ws_a) == []


def test_reminder_gets_none(ws_a: Workspace) -> None:
    first = arrive(
        ws_a,
        LETTER,
        Saying(facts_answer("2026-03-12", "Datum 12 maart 2026", "1234.56", "€ 1.234,56")),
    )
    strong = Saying(facts_answer("2026-04-09", "Datum 9 april 2026", "1310.20", "€ 1.310,20"))

    arrive(ws_a, REMINDER, strong)

    assert pairs_of(ws_a) == []
    # Control: with the reminder's date and amount read weakly, text and numbers alone
    # would make it a pair (the guard is what tells them apart).
    weak = Saying(facts_answer("2026-04-09", "niet in de brief", "1310.20", "niet in de brief"))
    third = arrive(
        ws_a, REMINDER.replace("Klantenservice Energie Noord\n", "Energie Noord\n"), weak
    )
    partners = {
        row.first_id if row.second_id == third.id else row.second_id for row in pairs_of(ws_a)
    }
    assert first.id in partners


@pytest.mark.parametrize(("days", "expected"), [(181, 0), (179, 1)])
def test_window_is_180_days(ws_a: Workspace, days: int, expected: int) -> None:
    letter(ws_a, LETTER, days_ago=days)

    arrive(ws_a, rescan(LETTER))

    assert len(pairs_of(ws_a)) == expected


def test_pool_leaves_out_trash_and_processing(ws_a: Workspace) -> None:
    trashed(letter(ws_a, LETTER))
    letter(ws_a, LETTER, status=Document.Status.PROCESSING)

    arrive(ws_a, rescan(LETTER))

    assert pairs_of(ws_a) == []


def test_private_letter_is_compared() -> None:
    o = office()
    private = o.letters["private_femke"]
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=private.id).update(ocr_text=LETTER)

    second = arrive(o.workspace, rescan(LETTER))

    [row] = pairs_of(o.workspace)
    assert {row.first_id, row.second_id} == {private.id, second.id}


def test_at_most_three_open_pairs(ws_a: Workspace) -> None:
    copies = {every: letter(ws_a, rescan(LETTER, every)) for every in (40, 60, 90, 150, 300)}

    new = arrive(ws_a, LETTER)

    rows = pairs_of(ws_a)
    assert len(rows) == 3
    others = {row.first_id if row.second_id == new.id else row.second_id for row in rows}
    # The three least garbled copies are the most similar.
    assert others == {copies[90].id, copies[150].id, copies[300].id}


def test_existing_pair_never_again(ws_a: Workspace) -> None:
    first = letter(ws_a, LETTER)
    second = letter(ws_a, rescan(LETTER))
    pair(first, second, state=DuplicateCandidate.State.DISMISSED)

    with workspace_context(ws_a.id, actor=SYSTEM):
        assert duplicates.detect(second) == 0

    [row] = pairs_of(ws_a)
    assert row.state == DuplicateCandidate.State.DISMISSED


def test_other_workspace_never_compared(ws_a: Workspace, ws_b: Workspace) -> None:
    letter(ws_b, LETTER)

    arrive(ws_a, rescan(LETTER))

    assert pairs_of(ws_a) == []
    assert pairs_of(ws_b) == []


def test_mark_failed_detects_too(ws_a: Workspace) -> None:
    letter(ws_a, LETTER)
    second = letter(ws_a, rescan(LETTER), status=Document.Status.PROCESSING)

    steps.mark_failed(ws_a.id, second.id, "ai_invalid_output")

    assert fresh(second).status == Document.Status.FAILED
    assert len(pairs_of(ws_a)) == 1


def test_detect_logs_no_content(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    letter(ws_a, LETTER)

    def broken(*args: object, **kwargs: object) -> object:
        raise ValueError("Energie Noord BV jaarafrekening 1.234,56")

    monkeypatch.setattr(duplicates, "decide", broken)
    with caplog.at_level(logging.INFO):
        second = arrive(ws_a, rescan(LETTER))

    assert pairs_of(ws_a) == []
    assert second.status == Document.Status.NEEDS_REVIEW
    assert "ValueError" in caplog.text
    assert str(second.id) in caplog.text
    for word in ("Energie", "Noord", "jaarafrekening", "1.234,56"):
        assert word not in caplog.text


def _confirm(workspace: Workspace, user: User, document: Document, day: str) -> None:
    data = ConfirmIn(
        sender="Energie Noord BV",
        document_type="invoice",
        letter_date=date.fromisoformat(day),
        summary="Jaarafrekening",
        summary_by_ai=False,
        tasks=[],
    )
    with as_actor(workspace, user.id):
        review.confirm_document(workspace=workspace, user=user, document_id=document.id, data=data)


@pytest.mark.parametrize(("day", "state"), [("2026-04-09", "withdrawn"), ("2026-03-12", "open")])
def test_recheck_on_confirm(day: str, state: str) -> None:
    o = office()
    hidden = o.letters["private_femke"]
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=hidden.id).update(
            ocr_text=LETTER,
            letter_date="2026-03-12",
            sender="Energie Noord BV",
            confirmed_at=hidden.created_at,
        )
    new = arrive(o.workspace, rescan(LETTER))
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=new.id).update(scanned_by=o.sorter)
        # Read weakly on arrival: no guard then.
        assert not Extraction.objects.filter(document=new).exists()
    [row] = pairs_of(o.workspace)
    assert row.state == "open"
    # The confirming member does not see the other letter.
    assert "private_femke" not in o.visible(o.sorter.id)

    _confirm(o.workspace, o.sorter, new, day)

    [row] = pairs_of(o.workspace)
    assert row.state == state
    assert fresh(new).status == Document.Status.DONE
