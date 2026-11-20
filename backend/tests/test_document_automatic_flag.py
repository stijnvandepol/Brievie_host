"""`Document.has_automatic_link`: whether the autopilot put a label or addressee on the
letter that no person took over yet. Stored on the letter so the inbox counts and the
"automatic" list need no lookup in the link tables per letter (DECISIONS 2026-11-20).

Written only by `apps.documents.organise`, whenever a link is added, removed or taken over:
it always equals "an automatic label or addressee link exists".
"""

from apps.audit.services import AuditActor
from apps.documents import organise, visibility
from apps.documents.models import Document, DocumentAddressee, DocumentEvent, DocumentLabel
from apps.labels import services as label_services
from apps.labels.models import Label
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, make_document, make_person

AI = DocumentEvent.Origin.AI
USER = DocumentEvent.Origin.USER


def _flag(workspace: Workspace, document: Document) -> bool:
    with workspace_context(workspace.id, actor=SYSTEM):
        stored = Document.objects.get(id=document.id).has_automatic_link
        links = (
            DocumentLabel.objects.filter(document=document, automatic=True).exists()
            or DocumentAddressee.objects.filter(document=document, automatic=True).exists()
        )
    assert stored == links, "the flag differs from the links"
    return stored


def _label(workspace: Workspace, name: str) -> Label:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Label.objects.create(workspace=workspace, name=name)


def test_a_new_letter_has_no_automatic_link(ws_a: Workspace) -> None:
    assert _flag(ws_a, make_document(ws_a)) is False


def test_an_automatic_label_sets_it_and_a_hand_label_does_not(ws_a: Workspace) -> None:
    by_hand, by_ai = make_document(ws_a), make_document(ws_a)
    label = _label(ws_a, "Kwitantiemap")
    with workspace_context(ws_a.id, actor=SYSTEM):
        organise.add_label(by_hand, label, actor=None, origin=USER)
        organise.add_label(by_ai, label, actor=None, origin=AI)

    assert _flag(ws_a, by_hand) is False
    assert _flag(ws_a, by_ai) is True


def test_an_automatic_addressee_sets_it(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    papa = make_person(ws_a, "Papa")
    with workspace_context(ws_a.id, actor=SYSTEM), visibility.addressee_scope(document):
        organise.add_addressee(document, papa, actor=None, origin=AI)

    assert _flag(ws_a, document) is True


def test_undo_clears_it(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    label = _label(ws_a, "Kwitantiemap")
    with workspace_context(ws_a.id, actor=SYSTEM):
        organise.add_label(document, label, actor=None, origin=AI)
        event = DocumentEvent.objects.get(document=document, kind="label_added")
    admin = admin_of(ws_a)
    with workspace_context(ws_a.id, actor=admin.id):
        locked = Document.objects.select_for_update().get(id=document.id)
        organise.undo_event(document=locked, event_id=event.id, actor=admin)

    assert _flag(ws_a, document) is False


def test_removing_the_label_by_hand_clears_it(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    label = _label(ws_a, "Kwitantiemap")
    with workspace_context(ws_a.id, actor=SYSTEM):
        organise.add_label(document, label, actor=None, origin=AI)
        organise.set_labels(document=document, label_ids=[], actor=None)

    assert _flag(ws_a, document) is False


def test_a_persons_confirm_takes_the_links_over(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    label = _label(ws_a, "Kwitantiemap")
    with workspace_context(ws_a.id, actor=SYSTEM):
        organise.add_label(document, label, actor=None, origin=AI)
        organise.confirm_automatic_links(document)

    assert _flag(ws_a, document) is False


def test_deleting_the_label_clears_it_on_every_letter(ws_a: Workspace) -> None:
    only_this, also_other = make_document(ws_a), make_document(ws_a)
    label, other = _label(ws_a, "Kwitantiemap"), _label(ws_a, "Garantiebewijs")
    with workspace_context(ws_a.id, actor=SYSTEM):
        organise.add_label(only_this, label, actor=None, origin=AI)
        organise.add_label(also_other, label, actor=None, origin=AI)
        organise.add_label(also_other, other, actor=None, origin=AI)
        label_services.delete_label(workspace=ws_a, label_id=label.id, actor=AuditActor.system())

    assert _flag(ws_a, only_this) is False
    assert _flag(ws_a, also_other) is True
