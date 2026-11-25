"""The original of an uploaded file: its fields, its place and the job's time limit
(intake plan Task 1)."""

import uuid
from datetime import timedelta

from django.conf import settings

from apps.documents.intake import original_aad, original_path, page_aad
from apps.documents.models import Document
from apps.pipeline.jobs import ABANDONED_AFTER


def test_an_app_scan_has_no_original() -> None:
    document = Document(page_count=1)
    assert document.original_kind == ""
    assert document.original_sha256 == ""
    assert document.original_size is None
    assert document.original_name == ""


def test_original_kinds_equal_the_file_kinds() -> None:
    assert sorted(Document.OriginalKind.values) == ["", "jpeg", "pdf", "png"]


def test_original_has_its_own_place_and_aad() -> None:
    workspace_id, document_id = uuid.uuid4(), uuid.uuid4()
    assert original_path(workspace_id, document_id) == f"{workspace_id}/{document_id}/original"
    expected = f"{workspace_id}/{document_id}/original"
    assert original_aad(workspace_id, document_id) == expected.encode()
    assert original_aad(workspace_id, document_id) != page_aad(workspace_id, document_id, 1)


def test_abandoned_after_exceeds_a_files_time_budget() -> None:
    # The budget bounds rendering and OCR of one file (security review of PR #58).
    assert timedelta(seconds=settings.BRIEVIE_FILE_PROCESSING_SECONDS) < ABANDONED_AFTER
    assert settings.BRIEVIE_FILE_PROCESSING_SECONDS < (
        settings.BRIEVIE_MAX_PAGES * settings.BRIEVIE_OCR_TIMEOUT_SECONDS
    )
