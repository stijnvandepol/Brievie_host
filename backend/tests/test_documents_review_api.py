import logging
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from django.db import OperationalError
from django.test import Client
from psycopg import errors as pg_errors

from apps.documents import organise, suggestions
from apps.documents.models import Document, DocumentEvent, DocumentLabel, DocumentSuggestion
from apps.labels.models import Label
from apps.rules.engine import Evaluation
from apps.rules.executor import execute
from apps.rules.models import RuleRun
from apps.tasks.models import Task
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, documents_url, jpeg, make_person, upload
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.rules_support import add_label, make_rule, text_word

if TYPE_CHECKING:
    from tests.helpers import Response

# The preset rules would add their own proposals to these letters.
pytestmark = pytest.mark.usefixtures("without_preset_rules")

TEXT = "Belastingdienst\nDatum 3 november 2026\nU moet € 1.234,56 betalen vóór 1 december 2026."


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def reviewable(client: Client, workspace: Workspace, status: str = "needs_review") -> str:
    document_id = str(upload(client, workspace.id, [jpeg()], texts=[TEXT]).json()["id"])
    with workspace_context(workspace.id):
        Document.objects.filter(id=document_id).update(status=status)
    return document_id


def task(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "pay",
        "description": "Betaal de aanslag",
        "deadline": "2026-12-01",
        "deadline_source": "in_document",
        "deadline_quote": "vóór 1 december 2026",
    }
    base.update(changes)
    return base


def review(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "sender": "Belastingdienst",
        "document_type": "assessment",
        "letter_date": "2026-11-03",
        "summary": "Je moet belasting betalen.",
        "summary_by_ai": True,
        "tasks": [task()],
    }
    base.update(changes)
    return base


def confirm(
    client: Client, workspace_id: object, document_id: object, body: dict[str, object]
) -> "Response":
    return post_json(client, f"/workspaces/{workspace_id}/documents/{document_id}/confirm", body)


def task_count(workspace: Workspace) -> int:
    with workspace_context(workspace.id):
        return Task.objects.count()


def test_confirm_stores_the_review_and_creates_the_task(client_a: Client, ws_a: Workspace) -> None:
    document_id = reviewable(client_a, ws_a)

    r = confirm(client_a, ws_a.id, document_id, review())

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["sender"] == "Belastingdienst"
    assert body["document_type"] == "assessment"
    assert body["letter_date"] == "2026-11-03"
    assert body["summary"] == "Je moet belasting betalen."
    assert body["summary_by_ai"] is True
    (created,) = body["tasks"]
    assert created["type"] == "pay"
    assert created["deadline"] == "2026-12-01"
    assert created["deadline_source"] == "in_document"
    assert created["deadline_quote"] == "vóór 1 december 2026"
    assert created["status"] == "open"
    with workspace_context(ws_a.id):
        document = Document.objects.get()
        assert document.confirmed_by == admin_of(ws_a)
        assert document.confirmed_at is not None
        assert Task.objects.get().document_id == document.id
    assert client_a.get(f"{documents_url(ws_a.id)}/{document_id}").json() == body


def test_no_task_exists_before_confirmation(client_a: Client, ws_a: Workspace) -> None:
    reviewable(client_a, ws_a)

    assert task_count(ws_a) == 0


def test_missing_optional_dates_are_accepted(client_a: Client, ws_a: Workspace) -> None:
    # The iOS client leaves a nil field out instead of sending null.
    document_id = reviewable(client_a, ws_a)
    body = review(tasks=[{"type": "respond", "description": "Bel", "deadline_source": "manual"}])
    del body["letter_date"]

    r = confirm(client_a, ws_a.id, document_id, body)

    assert r.status_code == 200
    assert r.json()["letter_date"] is None
    assert r.json()["tasks"][0]["deadline"] is None
    assert r.json()["tasks"][0]["deadline_quote"] == ""


def test_repeating_the_same_confirm_answers_like_the_first_and_makes_one_task(
    client_a: Client, ws_a: Workspace
) -> None:
    # The app retries after a lost answer.
    document_id = reviewable(client_a, ws_a)
    first = confirm(client_a, ws_a.id, document_id, review())

    again = confirm(client_a, ws_a.id, document_id, review())

    assert first.status_code == 200
    assert again.status_code == 200
    assert again.json() == first.json()
    assert task_count(ws_a) == 1


def test_repeat_with_tasks_in_another_order_and_untrimmed_text_is_the_same(
    client_a: Client, ws_a: Workspace
) -> None:
    document_id = reviewable(client_a, ws_a)
    other = task(type="respond", description="Bel", deadline_source="manual", deadline_quote="")
    confirm(client_a, ws_a.id, document_id, review(tasks=[task(), other]))

    again = review(
        sender=" Belastingdienst ", tasks=[other, task(description="Betaal de aanslag ")]
    )
    r = confirm(client_a, ws_a.id, document_id, again)

    assert r.status_code == 200
    assert task_count(ws_a) == 2


@pytest.mark.parametrize(
    "changed",
    [
        review(sender="CAK"),
        review(summary_by_ai=False),
        review(letter_date=None),
        review(tasks=[]),
        review(tasks=[task(), task()]),
        review(tasks=[task(description="Betaal op tijd")]),
    ],
)
def test_repeat_with_another_review_is_a_conflict_and_changes_nothing(
    client_a: Client, ws_a: Workspace, changed: dict[str, object]
) -> None:
    document_id = reviewable(client_a, ws_a)
    first = confirm(client_a, ws_a.id, document_id, review())

    r = confirm(client_a, ws_a.id, document_id, changed)

    assert r.status_code == 409
    assert r.json()["code"] == "document_not_reviewable"
    assert task_count(ws_a) == 1
    assert client_a.get(f"{documents_url(ws_a.id)}/{document_id}").json() == first.json()


def test_deadline_not_in_its_quote_becomes_a_manual_deadline(
    client_a: Client, ws_a: Workspace
) -> None:
    # The quote is in the letter, but it does not say 2 December.
    document_id = reviewable(client_a, ws_a)

    r = confirm(client_a, ws_a.id, document_id, review(tasks=[task(deadline="2026-12-02")]))

    assert r.status_code == 200
    (created,) = r.json()["tasks"]
    assert created["deadline"] == "2026-12-02"
    assert created["deadline_source"] == "manual"
    assert created["deadline_quote"] == ""


@pytest.mark.parametrize(
    "body",
    [
        review(sender="Belasting\x00dienst"),
        review(summary="Je moet\x00 betalen."),
        review(tasks=[task(description="Betaal\x00")]),
        review(tasks=[task(deadline_quote="vóór 1 december 2026\x00")]),
        review(document_type="\x00"),
    ],
)
def test_nul_character_is_refused_not_a_server_error(
    client_a: Client, ws_a: Workspace, body: dict[str, object]
) -> None:
    document_id = reviewable(client_a, ws_a)

    r = confirm(client_a, ws_a.id, document_id, body)

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert set(r.json()) == {"code", "message"}
    assert task_count(ws_a) == 0


def test_confirm_while_processing_is_a_conflict(client_a: Client, ws_a: Workspace) -> None:
    document_id = reviewable(client_a, ws_a, status="processing")

    r = confirm(client_a, ws_a.id, document_id, review())

    assert r.status_code == 409
    assert task_count(ws_a) == 0


def test_failed_document_can_be_reviewed_by_hand(client_a: Client, ws_a: Workspace) -> None:
    document_id = reviewable(client_a, ws_a, status="failed")
    manual = task(deadline_source="manual", deadline_quote="")

    r = confirm(client_a, ws_a.id, document_id, review(summary_by_ai=False, tasks=[manual]))

    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["failure_code"] == ""


def test_manual_deadline_has_no_quote(client_a: Client, ws_a: Workspace) -> None:
    document_id = reviewable(client_a, ws_a)
    manual = task(deadline_source="manual", deadline_quote="vóór 1 december 2026")

    r = confirm(client_a, ws_a.id, document_id, review(tasks=[manual]))

    assert r.json()["tasks"][0]["deadline_quote"] == ""


@pytest.mark.parametrize(
    "bad",
    [
        task(deadline_quote=""),
        task(deadline_quote="   "),
        task(deadline=None),
        task(deadline_quote="vóór 2 december 2026"),  # not in the document
        task(deadline_source="calculated"),
        task(type="dansen"),
        task(description=""),
        task(description="x" * 501),
    ],
)
def test_invalid_task_is_refused(client_a: Client, ws_a: Workspace, bad: dict[str, object]) -> None:
    document_id = reviewable(client_a, ws_a)

    r = confirm(client_a, ws_a.id, document_id, review(tasks=[bad]))

    assert r.status_code == 422
    assert set(r.json()) == {"code", "message"}
    assert task_count(ws_a) == 0
    with workspace_context(ws_a.id):
        assert Document.objects.get().status == "needs_review"


def test_invalid_document_type_and_too_many_tasks_are_refused(
    client_a: Client, ws_a: Workspace
) -> None:
    document_id = reviewable(client_a, ws_a)

    assert (
        confirm(client_a, ws_a.id, document_id, review(document_type="gedicht")).status_code == 422
    )
    many = [task() for _ in range(21)]
    assert confirm(client_a, ws_a.id, document_id, review(tasks=many)).status_code == 422
    assert task_count(ws_a) == 0


def test_empty_document_type_and_no_tasks_are_fine(client_a: Client, ws_a: Workspace) -> None:
    document_id = reviewable(client_a, ws_a)

    r = confirm(client_a, ws_a.id, document_id, review(document_type="", tasks=[]))

    assert r.status_code == 200
    assert r.json()["tasks"] == []


def test_bsn_in_confirmed_text_is_masked(client_a: Client, ws_a: Workspace) -> None:
    document_id = reviewable(client_a, ws_a)
    manual = task(description="Bel over 111222333", deadline_source="manual", deadline_quote="")
    body = review(summary="BSN 111222333 staat erin", tasks=[manual])

    r = confirm(client_a, ws_a.id, document_id, body)

    assert "111222333" not in r.content.decode()


def test_viewer_cannot_confirm(client: Client, client_a: Client, ws_a: Workspace) -> None:
    document_id = reviewable(client_a, ws_a)
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)

    r = confirm(client, ws_a.id, document_id, review())

    assert r.status_code == 403
    assert task_count(ws_a) == 0


def test_document_of_another_workspace_cannot_be_confirmed(
    client: Client, client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    document_id = reviewable(client_a, ws_a)
    client.force_login(admin_of(ws_b))

    through_a = confirm(client, ws_a.id, document_id, review())
    through_b = confirm(client, ws_b.id, document_id, review())

    assert through_a.status_code == 404
    assert through_a.json()["code"] == "workspace_not_found"
    assert through_b.status_code == 404
    assert through_b.json()["code"] == "document_not_found"
    assert task_count(ws_a) == 0


def test_unknown_document_is_404(client_a: Client, ws_a: Workspace) -> None:
    r = confirm(client_a, ws_a.id, uuid.uuid4(), review())

    assert r.status_code == 404


def organised_document(client: Client, workspace: Workspace) -> tuple[str, Label, Label, Label]:
    """A reviewable document with the label "Zorg" on it and open suggestions for
    "Bank" and "Auto"."""
    document_id = reviewable(client, workspace)
    with workspace_context(workspace.id):
        zorg, bank, auto = (
            Label.objects.get(workspace=workspace, system_key=key)
            for key in ("zorg", "bank", "auto")
        )
        document = Document.objects.get(id=document_id)
        organise.add_label(document, zorg, actor=None, origin="user")
        for target in (bank, auto):
            suggestions.propose(
                document,
                action=DocumentSuggestion.Action.ADD_LABEL,
                label=target,
                origin="ai",
                confidence=Decimal("0.700"),
            )
    return document_id, zorg, bank, auto


def suggestion_states(workspace: Workspace, document_id: str) -> dict[uuid.UUID | None, str]:
    with workspace_context(workspace.id):
        return {
            s.label_id or s.person_id: s.state
            for s in DocumentSuggestion.objects.filter(document_id=document_id)
        }


def label_events(workspace: Workspace, document_id: str) -> list[tuple[str, str, str]]:
    with workspace_context(workspace.id):
        return [
            (e.kind, e.origin, e.label_name)
            for e in DocumentEvent.objects.filter(
                document_id=document_id, kind__startswith="label"
            ).order_by("created_at", "id")
        ]


def test_confirm_with_flag_sets_exact_lists_and_closes_suggestions(
    client_a: Client, ws_a: Workspace
) -> None:
    document_id, zorg, bank, auto = organised_document(client_a, ws_a)
    anna = make_person(ws_a, "Anna")
    body = review(sets_organisation=True, label_ids=[str(bank.id)], addressee_ids=[str(anna.id)])

    r = confirm(client_a, ws_a.id, document_id, body)

    assert r.status_code == 200
    assert r.json()["label_ids"] == [str(bank.id)]
    assert r.json()["addressee_ids"] == [str(anna.id)]
    assert r.json()["suggestions"] == []
    # Chosen -> accepted, left out -> dismissed, removed by hand -> dismissed.
    assert suggestion_states(ws_a, document_id) == {
        bank.id: "accepted",
        auto.id: "dismissed",
        zorg.id: "dismissed",
    }
    assert label_events(ws_a, document_id) == [
        ("label_added", "user", "Zorg"),
        ("label_removed", "user", "Zorg"),
        ("label_added", "suggestion", "Bank"),
    ]
    with workspace_context(ws_a.id):
        stored = Document.objects.get(id=document_id).confirmed_organisation
    assert stored == {"label_ids": [str(bank.id)], "addressee_ids": [str(anna.id)]}


def test_confirm_with_flag_and_unknown_label_changes_nothing(
    client_a: Client, ws_a: Workspace
) -> None:
    document_id, _, bank, _ = organised_document(client_a, ws_a)
    body = review(sets_organisation=True, label_ids=[str(bank.id), str(uuid.uuid4())])

    r = confirm(client_a, ws_a.id, document_id, body)

    assert r.status_code == 404
    assert r.json()["code"] == "label_not_found"
    assert task_count(ws_a) == 0
    with workspace_context(ws_a.id):
        assert Document.objects.get(id=document_id).status == "needs_review"
    assert suggestion_states(ws_a, document_id)[bank.id] == "open"


def test_confirm_without_flag_leaves_labels_and_suggestions(
    client_a: Client, ws_a: Workspace
) -> None:
    # The 1b app does not know the new fields.
    document_id, zorg, bank, auto = organised_document(client_a, ws_a)

    r = confirm(client_a, ws_a.id, document_id, review())

    assert r.status_code == 200
    assert r.json()["label_ids"] == [str(zorg.id)]
    assert r.json()["open_suggestion_count"] == 2
    assert suggestion_states(ws_a, document_id) == {bank.id: "open", auto.id: "open"}
    with workspace_context(ws_a.id):
        assert Document.objects.get(id=document_id).confirmed_organisation is None
    # Repeating it is still the same confirm.
    assert confirm(client_a, ws_a.id, document_id, review()).status_code == 200


def test_repeated_confirm_with_same_lists_is_200(client_a: Client, ws_a: Workspace) -> None:
    document_id, zorg, bank, auto = organised_document(client_a, ws_a)
    first = review(sets_organisation=True, label_ids=[str(bank.id), str(zorg.id)])
    confirm(client_a, ws_a.id, document_id, first)
    # A rule run after the confirm may add a label; the repeat is compared with the
    # snapshot, not with the current labels.
    with workspace_context(ws_a.id):
        organise.add_label(Document.objects.get(id=document_id), auto, actor=None, origin="rule")

    again = review(sets_organisation=True, label_ids=[str(zorg.id), str(bank.id), str(bank.id)])
    r = confirm(client_a, ws_a.id, document_id, again)

    assert r.status_code == 200
    assert set(r.json()["label_ids"]) == {str(zorg.id), str(bank.id), str(auto.id)}
    assert task_count(ws_a) == 1


@pytest.mark.parametrize("variant", ["other_labels", "without_flag", "flag_after_plain"])
def test_repeated_confirm_with_other_lists_is_409(
    client_a: Client, ws_a: Workspace, variant: str
) -> None:
    document_id, zorg, bank, _ = organised_document(client_a, ws_a)
    with_flag = review(sets_organisation=True, label_ids=[str(zorg.id)])
    first, second = {
        "other_labels": (with_flag, review(sets_organisation=True, label_ids=[str(bank.id)])),
        "without_flag": (with_flag, review()),
        "flag_after_plain": (review(), review(sets_organisation=True, label_ids=[str(zorg.id)])),
    }[variant]
    assert confirm(client_a, ws_a.id, document_id, first).status_code == 200

    r = confirm(client_a, ws_a.id, document_id, second)

    assert r.status_code == 409
    assert r.json()["code"] == "document_not_reviewable"


def test_a_failure_in_the_rules_keeps_the_confirm(
    client_a: Client,
    ws_a: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    document_id, _, bank, auto = organised_document(client_a, ws_a)
    make_rule(ws_a, text_word("Belastingdienst"), actions=[add_label(auto)], mode="auto")

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Belastingdienst geheim")

    monkeypatch.setattr("apps.rules.triggers.evaluate", broken)
    body = review(sets_organisation=True, label_ids=[str(bank.id)], addressee_ids=[])

    with caplog.at_level(logging.ERROR):
        r = confirm(client_a, ws_a.id, document_id, body)

    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["label_ids"] == [str(bank.id)]
    with workspace_context(ws_a.id):
        document = Document.objects.get(id=document_id)
        assert document.status == "done"
        assert Task.objects.filter(document=document).count() == 1
    assert "RuntimeError" in caplog.text
    assert document_id in caplog.text
    assert "geheim" not in caplog.text


def _labels_on(workspace: Workspace, document_id: str) -> set[uuid.UUID]:
    with workspace_context(workspace.id):
        return set(
            DocumentLabel.objects.filter(document_id=document_id).values_list("label_id", flat=True)
        )


def test_a_rule_failing_after_it_wrote_leaves_none_of_its_writes(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    document_id, _, bank, _ = organised_document(client_a, ws_a)
    with workspace_context(ws_a.id):
        wonen = Label.objects.get(workspace=ws_a, system_key="wonen")
    make_rule(ws_a, text_word("Belastingdienst"), actions=[add_label(wonen)], mode="auto")
    written: list[bool] = []

    def apply_then_fail(document: Document, evaluation: Evaluation, *, trigger: str) -> None:
        execute(document, evaluation, trigger=trigger)
        written.append(DocumentLabel.objects.filter(document=document, label=wonen).exists())
        raise RuntimeError("after writing")

    monkeypatch.setattr("apps.rules.triggers.execute", apply_then_fail)
    body = review(sets_organisation=True, label_ids=[str(bank.id)], addressee_ids=[])

    r = confirm(client_a, ws_a.id, document_id, body)

    assert written == [True]
    assert r.status_code == 200
    assert r.json()["label_ids"] == [str(bank.id)]
    assert _labels_on(ws_a, document_id) == {bank.id}
    with workspace_context(ws_a.id):
        assert Document.objects.get(id=document_id).status == "done"
        assert Task.objects.filter(document_id=document_id).count() == 1
        assert not RuleRun.objects.filter(document_id=document_id).exists()


@pytest.mark.parametrize("cause", [pg_errors.DeadlockDetected, pg_errors.SerializationFailure])
def test_a_deadlock_in_the_rules_fails_the_whole_confirm_with_try_again(
    client_a: Client,
    ws_a: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    cause: type[Exception],
) -> None:
    document_id, zorg, bank, auto = organised_document(client_a, ws_a)
    make_rule(ws_a, text_word("Belastingdienst"), actions=[add_label(auto)], mode="auto")

    def conflict(*args: object, **kwargs: object) -> None:
        try:
            raise cause("conflict")
        except cause as exc:
            raise OperationalError("conflict") from exc

    monkeypatch.setattr("apps.rules.triggers.evaluate", conflict)
    body = review(sets_organisation=True, label_ids=[str(bank.id)], addressee_ids=[])

    r = confirm(client_a, ws_a.id, document_id, body)

    assert r.status_code == 409
    assert r.json()["code"] == "try_again"
    assert _labels_on(ws_a, document_id) == {zorg.id}
    with workspace_context(ws_a.id):
        assert Document.objects.get(id=document_id).status == "needs_review"
        assert not Task.objects.filter(document_id=document_id).exists()
