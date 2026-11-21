"""Every change path leaves a letter in the trash alone: the endpoints that change a
letter answer as for a missing one, the pipeline and the rules skip it, and an upload of
the same scan is refused instead of bringing it back."""

import uuid

import pytest
from django.test import Client
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.documents.models import Document, Extraction
from apps.labels.models import Label
from apps.pipeline import jobs as pipeline_jobs
from apps.pipeline.steps import run_pipeline
from apps.rules import jobs as rule_jobs
from apps.rules import services as rule_services
from apps.rules.schemas import RuleIn
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, jpeg, trashed, upload
from tests.helpers import post_json
from tests.rules_support import add_label, make_rule, text_word
from tests.rules_support import make_document as rules_document
from tests.test_pipeline_steps import FakeProvider, answer
from tests.test_pipeline_steps import make_document as pipeline_document
from tests.visibility_support import office, restrict_raw

pytestmark = pytest.mark.usefixtures("without_preset_rules")

TEXT = "Belastingdienst\nU moet betalen vóór 1 december 2026. Uw APK verloopt."


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def fresh(document: Document) -> Document:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return Document.objects.get(id=document.id)


def set_fields(document: Document, **fields: object) -> None:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(**fields)


def review_body() -> dict[str, object]:
    return {
        "sender": "Belastingdienst",
        "document_type": "assessment",
        "letter_date": None,
        "summary": "Betalen.",
        "summary_by_ai": False,
        "tasks": [],
    }


def _call(client: Client, ws: Workspace, document: Document, call: str) -> object:
    base = f"/workspaces/{ws.id}/documents/{document.id}"
    other = uuid.uuid4()
    if call == "confirmDocument":
        return post_json(client, f"{base}/confirm", review_body())
    if call == "reopenDocument":
        return post_json(client, f"{base}/reopen")
    if call == "retryDocumentProcessing":
        return post_json(client, f"{base}/retry")
    if call == "setDocumentLabels":
        return client.put(f"/api/v1{base}/labels", {"label_ids": []}, "application/json")
    if call == "setDocumentAddressees":
        return client.put(f"/api/v1{base}/addressees", {"person_ids": []}, "application/json")
    if call == "acceptSuggestion":
        return post_json(client, f"{base}/suggestions/{other}/accept")
    if call == "dismissSuggestion":
        return post_json(client, f"{base}/suggestions/{other}/dismiss")
    assert call == "undoDocumentEvent"
    return post_json(client, f"{base}/events/{other}/undo")


# The state in which the change would otherwise go ahead (or fail on its own grounds).
_STATE: dict[str, dict[str, object]] = {
    "confirmDocument": {"status": "needs_review"},
    "reopenDocument": {"status": "done", "confirmed_by_ai": True},
    "retryDocumentProcessing": {"status": "failed", "failure_code": "internal_error"},
}


@pytest.mark.parametrize(
    "call",
    [
        "confirmDocument",
        "reopenDocument",
        "retryDocumentProcessing",
        "setDocumentLabels",
        "setDocumentAddressees",
        "acceptSuggestion",
        "dismissSuggestion",
        "undoDocumentEvent",
    ],
)
def test_change_of_trashed_letter_is_not_found(
    client_a: Client, ws_a: Workspace, call: str
) -> None:
    document = rules_document(ws_a, TEXT, status="done")
    set_fields(document, **_STATE.get(call, {}))
    before = trashed(document)

    r = _call(client_a, ws_a, document, call)

    assert r.status_code == 404, r.content  # type: ignore[attr-defined]
    assert r.json()["code"] == "document_not_found"  # type: ignore[attr-defined]
    after = fresh(document)
    assert after.updated_at == before.updated_at
    assert after.status == before.status


def test_complete_and_reopen_task_of_trashed_letter_404(client_a: Client, ws_a: Workspace) -> None:
    document = trashed(rules_document(ws_a, TEXT, status="done"))
    with workspace_context(ws_a.id, actor=SYSTEM):
        task = Task.objects.create(workspace=ws_a, document=document, type="pay", description="x")

    for action in ("complete", "reopen"):
        r = post_json(client_a, f"/workspaces/{ws_a.id}/tasks/{task.id}/{action}")
        assert r.status_code == 404, action
        assert r.json()["code"] == "task_not_found"
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Task.objects.get(id=task.id).status == "open"


def test_pipeline_skips_trashed_letter_without_ai_call(ws_a: Workspace) -> None:
    document = trashed(pipeline_document(ws_a))

    def no_provider(workspace_id: uuid.UUID) -> None:
        pytest.fail("The pipeline asked for an AI provider for a letter in the trash.")

    run_pipeline(ws_a.id, document.id, provider_factory=no_provider)

    assert fresh(document).status == "processing"


def test_pipeline_drops_result_when_trashed_during_ai_call(ws_a: Workspace) -> None:
    document = pipeline_document(ws_a)
    provider = FakeProvider(answer())

    def trash_meanwhile() -> None:
        trashed(document)

    provider.during_call = trash_meanwhile

    run_pipeline(ws_a.id, document.id, provider_factory=lambda _: provider)

    assert len(provider.texts) == 1
    after = fresh(document)
    assert after.status == "processing"
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert not Extraction.objects.filter(document_id=document.id).exists()


def test_requeue_skips_trashed(ws_a: Workspace) -> None:
    document = pipeline_document(ws_a)
    trashed(document)
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(
            updated_at=document.updated_at - pipeline_jobs.ABANDONED_AFTER * 2
        )

    assert pipeline_jobs.requeue_abandoned_documents() == 0
    assert not ProcrastinateJob.objects.exists()


def test_has_live_job_is_public(ws_a: Workspace) -> None:
    document = pipeline_document(ws_a)
    assert pipeline_jobs.has_live_job(document.id) is False
    pipeline_jobs.defer_processing(workspace_id=ws_a.id, document_id=document.id)
    assert pipeline_jobs.has_live_job(document.id) is True


def test_rule_preview_and_run_leave_out_trashed(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        label = Label.objects.create(workspace=ws_a, name="Keuring")
    live = rules_document(ws_a, TEXT, status="done")
    gone = trashed(rules_document(ws_a, TEXT, status="done"))
    admin = admin_of(ws_a)
    data = RuleIn.model_validate(
        {
            "name": "APK",
            "conditions": [{"field": "text", "op": "contains_word", "value": "APK"}],
            "actions": [{"type": "add_label", "label_id": str(label.id)}],
        }
    )

    with workspace_context(ws_a.id, actor=admin.id):
        out = rule_services.test_rule(workspace=ws_a, data=data)
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(label)], mode="auto")
    rule_jobs.apply_rule_batch(ws_a.id, rule.id, user_id=admin.id, after=None)

    assert (out.total_count, out.matched_count) == (1, 1)
    assert [d.id for d in out.documents] == [live.id]
    with workspace_context(ws_a.id, actor=SYSTEM):
        labelled = set(
            Document.objects.filter(label_links__label=label).values_list("id", flat=True)
        )
    assert labelled == {live.id}
    assert gone.id not in labelled


def test_confirm_repeat_of_trashed_done_letter_404(client_a: Client, ws_a: Workspace) -> None:
    document = rules_document(ws_a, TEXT)
    url = f"/workspaces/{ws_a.id}/documents/{document.id}/confirm"
    first = post_json(client_a, url, review_body())
    assert first.status_code == 200, first.content
    assert post_json(client_a, url, review_body()).status_code == 200
    trashed(document)

    r = post_json(client_a, url, review_body())

    assert r.status_code == 404
    assert r.json()["code"] == "document_not_found"


def _documents(ws: Workspace) -> int:
    with workspace_context(ws.id, actor=SYSTEM):
        return Document.objects.count()


def test_upload_same_client_id_of_trashed_letter_409(client_a: Client, ws_a: Workspace) -> None:
    client_id = uuid.uuid4()
    pages = [jpeg("een")]
    first = upload(client_a, ws_a.id, pages, client_id=client_id)
    assert first.status_code == 201, first.content
    with workspace_context(ws_a.id, actor=SYSTEM):
        trashed(Document.objects.get(id=first.json()["id"]))

    same = upload(client_a, ws_a.id, pages, client_id=client_id)
    other = upload(client_a, ws_a.id, [jpeg("twee")], client_id=client_id)

    assert same.status_code == 409
    assert same.json()["code"] == "document_trashed"
    assert "prullenbak" in same.json()["message"]
    assert other.status_code == 409
    assert other.json()["code"] == "document_conflict"
    assert _documents(ws_a) == 1


def test_upload_of_letter_whose_purge_started_gives_no_restore_advice(
    client_a: Client, ws_a: Workspace
) -> None:
    client_id = uuid.uuid4()
    pages = [jpeg("een")]
    first = upload(client_a, ws_a.id, pages, client_id=client_id)
    assert first.status_code == 201, first.content
    with workspace_context(ws_a.id, actor=SYSTEM):
        trashed(Document.objects.get(id=first.json()["id"]), purge_started=True)

    again = upload(client_a, ws_a.id, pages, client_id=client_id)

    assert again.status_code == 409
    assert again.json()["code"] == "document_trashed"
    assert "voorgoed gewist" in again.json()["message"]
    assert "terug" not in again.json()["message"]
    assert _documents(ws_a) == 1


def test_upload_of_trashed_letter_uploader_cannot_see_409_conflict(client: Client) -> None:
    o = office()
    client.force_login(o.plain)
    client_id = uuid.uuid4()
    pages = [jpeg("piet")]
    first = upload(client, o.workspace.id, pages, client_id=client_id)
    assert first.status_code == 201, first.content
    with workspace_context(o.workspace.id, actor=SYSTEM):
        document = Document.objects.get(id=first.json()["id"])
    restrict_raw(document, [o.finance])
    trashed(document, by=o.finance)

    again = upload(client, o.workspace.id, pages, client_id=client_id)

    assert again.status_code == 409
    assert again.json()["code"] == "document_conflict"


def test_upload_after_purge_is_new_letter(client_a: Client, ws_a: Workspace) -> None:
    client_id = uuid.uuid4()
    pages = [jpeg("een")]
    first = upload(client_a, ws_a.id, pages, client_id=client_id)
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=first.json()["id"]).delete()

    again = upload(client_a, ws_a.id, pages, client_id=client_id)

    assert again.status_code == 201, again.content
    assert again.json()["id"] != first.json()["id"]
    assert _documents(ws_a) == 1
