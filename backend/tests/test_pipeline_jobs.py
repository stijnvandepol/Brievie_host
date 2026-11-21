import uuid
from datetime import timedelta
from uuid import UUID

import pytest
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate.contrib.django import app as procrastinate_app
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.documents.models import Document
from apps.pipeline import jobs
from apps.pipeline.errors import PermanentError, PipelineError, RetryableError
from apps.pipeline.steps import mark_failed, run_pipeline
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace


def make_document(workspace: Workspace, **fields: object) -> Document:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Document.objects.create(
            workspace=workspace, client_id=uuid.uuid4(), page_count=1, ocr_text="tekst", **fields
        )


def status_of(workspace: Workspace, document: Document) -> tuple[str, str]:
    with workspace_context(workspace.id, actor=SYSTEM):
        fresh = Document.objects.get(id=document.id)
    return fresh.status, fresh.failure_code


def raising(error: Exception) -> jobs.Runner:
    def run(workspace_id: UUID, document_id: UUID) -> None:
        raise error

    return run


def test_deferring_stores_a_job_with_only_the_two_ids(ws_a: Workspace) -> None:
    document = make_document(ws_a)

    jobs.defer_processing(workspace_id=ws_a.id, document_id=document.id)

    job = ProcrastinateJob.objects.get()
    assert job.task_name == "process_document"
    assert job.queue_name == "pipeline"
    assert job.args == {"workspace_id": str(ws_a.id), "document_id": str(document.id)}


def test_placeholder_pipeline_moves_the_document_to_needs_review(ws_a: Workspace) -> None:
    document = make_document(ws_a)

    jobs.run_job(ws_a.id, document.id, attempts=0)

    assert status_of(ws_a, document) == ("needs_review", "")


def test_job_with_another_workspace_id_leaves_the_document_untouched(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    document = make_document(ws_a)

    jobs.run_job(ws_b.id, document.id, attempts=0)

    assert status_of(ws_a, document) == ("processing", "")


def test_job_for_a_missing_document_does_nothing(ws_a: Workspace) -> None:
    jobs.run_job(ws_a.id, uuid.uuid4(), attempts=0)


@pytest.mark.parametrize("status", ["needs_review", "done", "failed"])
def test_job_for_a_document_that_is_not_processing_changes_nothing(
    ws_a: Workspace, status: str
) -> None:
    document = make_document(ws_a, status=status, failure_code="x" if status == "failed" else "")

    run_pipeline(ws_a.id, document.id)

    assert status_of(ws_a, document)[0] == status


def test_retryable_error_is_raised_again_before_the_last_attempt(ws_a: Workspace) -> None:
    document = make_document(ws_a)

    with pytest.raises(RetryableError):
        jobs.run_job(
            ws_a.id, document.id, attempts=0, run=raising(RetryableError("ai_unavailable"))
        )

    assert status_of(ws_a, document) == ("processing", "")


def test_retryable_error_on_the_last_attempt_marks_the_document_failed(ws_a: Workspace) -> None:
    document = make_document(ws_a)

    jobs.run_job(
        ws_a.id,
        document.id,
        attempts=jobs.MAX_ATTEMPTS - 1,
        run=raising(RetryableError("ai_unavailable")),
    )

    assert status_of(ws_a, document) == ("failed", "ai_unavailable")


def test_permanent_error_marks_the_document_failed_at_once(ws_a: Workspace) -> None:
    document = make_document(ws_a)

    jobs.run_job(ws_a.id, document.id, attempts=0, run=raising(PermanentError("ai_key_rejected")))

    assert status_of(ws_a, document) == ("failed", "ai_key_rejected")


def test_unexpected_error_marks_the_document_failed_without_logging_its_text(
    ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    document = make_document(ws_a)

    jobs.run_job(ws_a.id, document.id, attempts=0, run=raising(KeyError("inhoud van de brief")))

    assert status_of(ws_a, document) == ("failed", "internal_error")
    assert "inhoud van de brief" not in caplog.text
    assert "KeyError" in caplog.text


def test_mark_failed_only_touches_a_processing_document(ws_a: Workspace) -> None:
    document = make_document(ws_a, status="done")

    mark_failed(ws_a.id, document.id, "ai_unavailable")

    assert status_of(ws_a, document) == ("done", "")


def test_errors_carry_their_code() -> None:
    assert PipelineError("x").code == "x"
    assert issubclass(RetryableError, PipelineError)
    assert issubclass(PermanentError, PipelineError)


def test_only_retryable_errors_are_retried_by_the_queue() -> None:
    retry = jobs.process_document.retry_strategy
    assert isinstance(retry, RetryStrategy)
    assert retry.max_attempts == jobs.MAX_ATTEMPTS
    assert list(retry.retry_exceptions or []) == [RetryableError]


def test_stalled_jobs_are_retried_periodically() -> None:
    periodic = {
        entry.task.name: entry.cron
        for entry in procrastinate_app.periodic_registry.periodic_tasks.values()
    }
    assert periodic["retry_stalled_jobs"] == "*/5 * * * *"


def age(workspace: Workspace, document: Document, minutes: int) -> None:
    with workspace_context(workspace.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(
            updated_at=timezone.now() - timedelta(minutes=minutes)
        )


def test_abandoned_processing_document_is_queued_again(ws_a: Workspace, ws_b: Workspace) -> None:
    # Final review: marking a document failed can itself fail (database restart); then no
    # job is left and nothing would ever move the document again.
    abandoned = make_document(ws_a)
    age(ws_a, abandoned, minutes=20)
    fresh = make_document(ws_b)
    finished = make_document(ws_b, status="done")
    age(ws_b, finished, minutes=20)

    assert jobs.requeue_abandoned_documents() == 1

    job = ProcrastinateJob.objects.get()
    assert job.args == {"workspace_id": str(ws_a.id), "document_id": str(abandoned.id)}
    assert status_of(ws_b, fresh) == ("processing", "")
    assert status_of(ws_b, finished) == ("done", "")


def test_document_with_a_waiting_job_is_not_queued_twice(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    jobs.defer_processing(workspace_id=ws_a.id, document_id=document.id)
    age(ws_a, document, minutes=20)

    assert jobs.requeue_abandoned_documents() == 0
    assert ProcrastinateJob.objects.count() == 1


def test_abandoned_documents_are_checked_periodically() -> None:
    periodic = {
        entry.task.name: entry.cron
        for entry in procrastinate_app.periodic_registry.periodic_tasks.values()
    }
    assert periodic["requeue_abandoned_documents"] == "*/5 * * * *"
