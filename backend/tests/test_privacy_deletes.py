"""Writes the user cannot see (plan Task 10, PB T9): deletes, "in use", archiving and a
repeated upload reach letters the caller does not see, as the system, after the
authorisation as the caller.

The office is in state E off: Anne (admin, admins_read_all off, not the sorter) sees none
of its letters; Piet sees `own_piet` and `scanned_in_review` only.
"""

import hashlib

import pytest
from django.db import connection
from django.test import Client

from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentPage,
    DocumentSuggestion,
)
from apps.labels.models import Label
from apps.people.models import Person
from apps.rules.models import Rule, RuleRun
from apps.workspaces.context import SYSTEM
from tests.documents_support import jpeg, upload
from tests.helpers import put
from tests.visibility_support import Office, as_actor, office


@pytest.fixture
def o(db: None) -> Office:
    return office()


def _as(o: Office, user_name: str) -> Client:
    client = Client()
    client.force_login(o.users[user_name])
    return client


def _commit_check() -> None:
    """What the commit would check: every deferred foreign key, now."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _hidden_from(o: Office, user_name: str, letter: str) -> Document:
    document = o.letters[letter]
    assert letter not in o.visible(o.users[user_name].id)
    return document


def test_delete_label_used_on_someone_elses_letter_commits(o: Office) -> None:
    hidden = _hidden_from(o, "admin", "finance_letter")
    with as_actor(o.workspace, SYSTEM):
        label = Label.objects.create(workspace=o.workspace, name="Belasting")
        DocumentLabel.objects.create(workspace=o.workspace, document=hidden, label=label)

    r = _as(o, "admin").delete(f"/api/v1/workspaces/{o.workspace.id}/labels/{label.id}")

    assert r.status_code == 204
    _commit_check()
    with as_actor(o.workspace, SYSTEM):
        assert not DocumentLabel.objects.filter(label_id=label.id).exists()
        event = DocumentEvent.objects.get(
            document=hidden, kind=DocumentEvent.Kind.LABEL_REMOVED, label_name="Belasting"
        )
        assert event.label_id is None


def test_delete_rule_that_ran_on_a_hidden_letter_commits(o: Office) -> None:
    hidden = _hidden_from(o, "admin", "finance_letter")
    with as_actor(o.workspace, SYSTEM):
        rule = Rule.objects.get(workspace=o.workspace, runs__document=hidden)
        DocumentEvent.objects.create(
            workspace=o.workspace,
            document=hidden,
            kind=DocumentEvent.Kind.LABEL_ADDED,
            origin=DocumentEvent.Origin.RULE,
            rule=rule,
            rule_name=rule.name,
        )

    r = _as(o, "admin").delete(f"/api/v1/workspaces/{o.workspace.id}/rules/{rule.id}")

    assert r.status_code == 204
    _commit_check()
    with as_actor(o.workspace, SYSTEM):
        assert not RuleRun.objects.filter(rule_id=rule.id).exists()
        event = DocumentEvent.objects.get(document=hidden, rule_name=rule.name)
        assert event.rule_id is None


def test_delete_person_on_a_hidden_letter_is_409_in_use_for_an_admin(o: Office) -> None:
    _hidden_from(o, "admin", "person_without_account")
    jan = o.persons["Jan"]

    r = _as(o, "admin").delete(f"/api/v1/workspaces/{o.workspace.id}/persons/{jan.id}")

    assert r.status_code == 409
    assert r.json()["code"] == "person_in_use"
    _commit_check()
    with as_actor(o.workspace, SYSTEM):
        assert Person.objects.filter(id=jan.id).exists()


def test_member_cannot_delete_any_person(o: Office) -> None:
    with as_actor(o.workspace, SYSTEM):
        unused = Person.objects.create(workspace=o.workspace, display_name="Oom")
    client = _as(o, "plain")
    base = f"/api/v1/workspaces/{o.workspace.id}/persons"

    answers = [client.delete(f"{base}/{p.id}") for p in (unused, o.persons["Jan"])]

    assert [r.status_code for r in answers] == [403, 403]
    assert answers[0].json() == answers[1].json()
    assert answers[0].json()["code"] == "forbidden"
    with as_actor(o.workspace, SYSTEM):
        assert Person.objects.filter(id=unused.id).exists()


def test_delete_unused_person_still_works_for_an_admin(o: Office) -> None:
    with as_actor(o.workspace, SYSTEM):
        unused = Person.objects.create(workspace=o.workspace, display_name="Oom")

    r = _as(o, "admin").delete(f"/api/v1/workspaces/{o.workspace.id}/persons/{unused.id}")

    assert r.status_code == 204
    _commit_check()
    with as_actor(o.workspace, SYSTEM):
        assert not Person.objects.filter(id=unused.id).exists()


def test_archiving_a_person_closes_suggestions_on_hidden_letters(o: Office) -> None:
    hidden = _hidden_from(o, "admin", "hr_letter")
    jan = o.persons["Jan"]
    with as_actor(o.workspace, SYSTEM):
        suggestion = DocumentSuggestion.objects.create(
            workspace=o.workspace,
            document=hidden,
            action=DocumentSuggestion.Action.ADD_ADDRESSEE,
            person=jan,
        )

    r = put(
        _as(o, "admin"),
        f"/workspaces/{o.workspace.id}/persons/{jan.id}",
        {"display_name": "Jan", "archived": True},
    )

    assert r.status_code == 200
    with as_actor(o.workspace, SYSTEM):
        assert not DocumentSuggestion.objects.filter(id=suggestion.id).exists()
        # Archiving closes proposals only; Jan stays on the letter he is on.
        assert DocumentAddressee.objects.filter(person=jan).exists()


def _same_pages(document: Document, page: bytes) -> None:
    with as_actor(document.workspace, SYSTEM):
        DocumentPage.objects.filter(document=document).update(
            sha256=hashlib.sha256(page).hexdigest()
        )


def test_repeated_upload_of_own_letter_still_returns_it(o: Office) -> None:
    letter = o.letters["scanned_in_review"]
    page = jpeg()
    _same_pages(letter, page)

    r = upload(_as(o, "plain"), o.workspace.id, [page], client_id=letter.client_id)

    assert r.status_code == 200
    assert r.json()["id"] == str(letter.id)


def test_repeated_upload_by_the_scanner_after_hand_over_is_409_without_data(o: Office) -> None:
    letter = o.letters["scanned_in_review"]
    page = jpeg()
    _same_pages(letter, page)
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=letter.id).update(status="done", sender="Gemeente")
    _hidden_from(o, "plain", "scanned_in_review")

    r = upload(_as(o, "plain"), o.workspace.id, [page], client_id=letter.client_id)

    assert r.status_code == 409
    assert set(r.json()) == {"code", "message"}
    assert r.json()["code"] == "document_conflict"
    assert str(letter.id) not in r.content.decode()
    assert "Gemeente" not in r.content.decode()


def test_repeated_upload_of_someone_elses_letter_is_409(o: Office) -> None:
    letter = _hidden_from(o, "plain", "finance_letter")
    page = jpeg()
    _same_pages(letter, page)

    r = upload(_as(o, "plain"), o.workspace.id, [page], client_id=letter.client_id)

    assert r.status_code == 409
    assert r.json()["code"] == "document_conflict"
    assert str(letter.id) not in r.content.decode()
