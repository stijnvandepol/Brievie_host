"""Audit rows name letters by id only (S 4.10, plan Task 19).

The workspace log is read by its admins, who in own post may not read every letter. So
no row may carry a letter's sender or summary, or the name of a label (labels show what
post is about): a document, label or rule is an id. `apps.audit.services.record` refuses
a name on such a target; these tests run the audited actions of `office()` and look.
"""

import json

from django.test import Client

from apps.audit.models import WorkspaceAuditEvent
from apps.documents.models import Document, DocumentLabel
from apps.labels.models import Label
from apps.rules.models import Rule
from apps.workspaces.context import NOBODY, SYSTEM
from tests.documents_support import documents_url
from tests.visibility_support import Office, as_actor, office

SENDER = "Kikkerpoel Incasso"
SUMMARY = "Aanslag Sneeuwpop"
LABEL = "Zwaluwstaart"
ID_ONLY = ("document", "label", "rule")


def _office_with_content() -> tuple[Office, Label]:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=o.letters["finance_letter"].id).update(
            sender=SENDER, summary=SUMMARY
        )
        label = Label.objects.create(workspace=o.workspace, name=LABEL)
        for name in ("finance_letter", "own_piet"):
            DocumentLabel.objects.create(
                workspace=o.workspace, document=o.letters[name], label=label
            )
    return o, label


def _run_audited_actions(o: Office, label: Label, client: Client) -> None:
    letter = f"{documents_url(o.workspace.id)}/{o.letters['finance_letter'].id}"
    client.force_login(o.finance)
    assert client.post(f"{letter}/trash", content_type="application/json").status_code == 200
    assert client.post(f"{letter}/restore", content_type="application/json").status_code == 200

    # Piet deletes a label that is also on Finance's letter, which he cannot see.
    client.force_login(o.plain)
    response = client.delete(f"/api/v1/workspaces/{o.workspace.id}/labels/{label.id}")
    assert response.status_code == 204

    # Anne (admin, A off) deletes the office's rule, whose runs are on letters she
    # cannot see.
    with as_actor(o.workspace, SYSTEM):
        rule = Rule.objects.get(workspace=o.workspace, name="Regel")
    client.force_login(o.admin)
    response = client.delete(f"/api/v1/workspaces/{o.workspace.id}/rules/{rule.id}")
    assert response.status_code == 204


def _rows(o: Office) -> list[WorkspaceAuditEvent]:
    with as_actor(o.workspace, NOBODY):
        return list(WorkspaceAuditEvent.objects.filter(workspace=o.workspace))


def test_document_targets_carry_the_id_only(client: Client) -> None:
    o, label = _office_with_content()
    _run_audited_actions(o, label, client)
    rows = _rows(o)
    kinds = {row.target_kind for row in rows}
    assert set(ID_ONLY) <= kinds
    for row in rows:
        if row.target_kind in ID_ONLY:
            assert row.target_name == "", (row.code, row.target_kind)
            assert row.target_id is not None


def test_no_audit_value_holds_a_sender_summary_or_label_name(client: Client) -> None:
    o, label = _office_with_content()
    _run_audited_actions(o, label, client)
    rows = _rows(o)
    assert rows
    for row in rows:
        text = json.dumps(row.values) + row.target_name + row.actor_name
        for secret in (SENDER, SUMMARY, LABEL, "Kikkerpoel", "Sneeuwpop"):
            assert secret not in text, (row.code, secret)

    # Anne reads the log: the same rows, so nothing in the answer either.
    client.force_login(o.admin)
    response = client.get(f"/api/v1/workspaces/{o.workspace.id}/audit-events")
    assert response.status_code == 200
    body = response.content.decode()
    for secret in ("Kikkerpoel", "Sneeuwpop", LABEL):
        assert secret not in body


def test_workspace_audit_log_is_admin_only(client: Client) -> None:
    o = office()
    for user in (o.plain, o.finance, o.sorter, o.viewer):
        client.force_login(user)
        response = client.get(f"/api/v1/workspaces/{o.workspace.id}/audit-events")
        assert response.status_code == 403, user.name
        assert response.json()["code"] == "forbidden"
    client.force_login(o.admin)
    assert client.get(f"/api/v1/workspaces/{o.workspace.id}/audit-events").status_code == 200
