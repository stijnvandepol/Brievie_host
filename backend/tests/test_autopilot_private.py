"""The autopilot never widens the readers of a private letter (security review of PR #38,
finding 1): on a letter that is private, or that the autopilot makes private with the
Zorg label in the same run, an AI or name-match addressee stays a proposal."""

import uuid

import pytest

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.documents import visibility
from apps.documents.models import Document, DocumentAddressee, DocumentSuggestion
from apps.people.models import Person
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.factories import UserFactory
from tests.rules_support import make_rule, text_word
from tests.test_autopilot_organise import LETTER, Picking, run, switch_on
from tests.visibility_support import readers

pytestmark = pytest.mark.usefixtures("without_preset_rules")


def _jan(workspace: Workspace) -> Person:
    """Jan de Vries, whom the letter names, with an account: as addressee he would read it."""
    jan = UserFactory.create(name="Jan")
    Membership.objects.create(user=jan, workspace=workspace, role=Membership.Role.MEMBER)
    with workspace_context(workspace.id, actor=SYSTEM):
        return Person.objects.create(
            workspace=workspace, display_name="Jan de Vries", name_variants=[], user=jan
        )


def _scanner(workspace: Workspace) -> User:
    bob = UserFactory.create(name="Bob")
    Membership.objects.create(user=bob, workspace=workspace, role=Membership.Role.MEMBER)
    return bob


def _processing(workspace: Workspace, scanned_by: User) -> Document:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Document.objects.create(
            workspace=workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            ocr_text=LETTER,
            status=Document.Status.PROCESSING,
            scanned_by=scanned_by,
        )


def _jan_stays_a_proposal(workspace: Workspace, document: Document, jan: Person) -> None:
    with workspace_context(workspace.id, actor=SYSTEM):
        assert Document.objects.get(id=document.id).visibility == "restricted"
        assert not DocumentAddressee.objects.filter(document=document, person=jan).exists()
        (row,) = DocumentSuggestion.objects.filter(document=document, person=jan)
    assert row.state == "open"
    assert readers(document) == {("Bob", "share")}


def test_a_rule_makes_it_private_and_the_name_match_stays_a_proposal(ws_a: Workspace) -> None:
    switch_on(ws_a)
    jan = _jan(ws_a)
    make_rule(ws_a, text_word("huisarts"), actions=[("make_private", None, None)], mode="auto")
    document = _processing(ws_a, _scanner(ws_a))
    run(ws_a, document, Picking())
    _jan_stays_a_proposal(ws_a, document, jan)


def test_processing_a_private_letter_again_adds_no_reader(ws_a: Workspace) -> None:
    switch_on(ws_a)
    jan = _jan(ws_a)
    document = _processing(ws_a, _scanner(ws_a))
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert (
            visibility.make_private(document, actor=AuditActor.system(), origin="user")
            == "made_private"
        )
    run(ws_a, document, Picking())
    _jan_stays_a_proposal(ws_a, document, jan)


def test_a_zorg_label_the_autopilot_applies_counts_before_the_addressee(
    ws_a: Workspace,
) -> None:
    switch_on(ws_a)
    jan = _jan(ws_a)
    document = _processing(ws_a, _scanner(ws_a))
    run(ws_a, document, Picking(labels={"Zorg": 0.95}))
    _jan_stays_a_proposal(ws_a, document, jan)


def test_a_letter_that_is_not_private_still_gets_its_addressee(ws_a: Workspace) -> None:
    switch_on(ws_a)
    jan = _jan(ws_a)
    document = _processing(ws_a, _scanner(ws_a))
    run(ws_a, document, Picking())
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert DocumentAddressee.objects.filter(document=document, person=jan).exists()
