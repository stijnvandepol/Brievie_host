"""An uploaded file to JPEG pages and their text (intake spec S 1.1), in the worker.

PDFium and Pillow run in a child process (`apps.intake.sandbox`); OCR runs Tesseract on
every page (a PDF's own text layer is not used). The text is returned unmasked: the caller masks it
before it is stored. Failures are `PermanentError` with a code `mark_failed` shows.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from django.conf import settings

from apps.intake import sandbox
from apps.intake.files import FileKind
from apps.intake.ocr import OcrEngine, OcrFailedError
from apps.intake.sandbox import IsolationError
from apps.pipeline.errors import PermanentError

# Rendering 30 pages at 200 dpi takes seconds; this bounds a file built to be slow.
RENDER_TIMEOUT_BASE = 30.0
RENDER_TIMEOUT_PER_PAGE = 5.0


@dataclass(frozen=True)
class RenderedPage:
    jpeg: bytes
    text: str


def render_pages(
    data: bytes, kind: FileKind, ocr: OcrEngine, *, clock: Callable[[], float] = time.monotonic
) -> list[RenderedPage]:
    """The file's pages and their text, within `BRIEVIE_FILE_PROCESSING_SECONDS` for the
    whole file (security review of PR #58): rendering and each page's OCR get at most what
    is left, and a file that runs out fails `file_too_slow`."""
    deadline = clock() + settings.BRIEVIE_FILE_PROCESSING_SECONDS
    max_pages = settings.BRIEVIE_MAX_PAGES
    if kind is FileKind.PDF:
        timeout = RENDER_TIMEOUT_BASE + RENDER_TIMEOUT_PER_PAGE * max_pages
    else:
        timeout = RENDER_TIMEOUT_BASE
    timeout = min(timeout, deadline - clock())
    try:
        parsed = sandbox.render(data, kind=kind.value, max_pages=max_pages, timeout=timeout)
    except IsolationError as error:
        raise PermanentError(str(error) or "file_unreadable") from None

    # Every page is read from its picture, also a PDF page with its own text: that layer
    # can hold text the page does not show (security review of PR #58).
    # No page brings more text than a scan from the app may (security review of PR #58).
    limit = settings.BRIEVIE_MAX_PAGE_TEXT_CHARS
    pages: list[RenderedPage] = []
    failed = 0
    for page in parsed:
        left = deadline - clock()
        if left <= 0:
            raise PermanentError("file_too_slow")
        try:
            text = ocr.read(page.jpeg, timeout=min(settings.BRIEVIE_OCR_TIMEOUT_SECONDS, left))
        except OcrFailedError:
            failed += 1
            text = ""
        pages.append(RenderedPage(jpeg=page.jpeg, text=text[:limit]))
    if failed == len(pages):
        raise PermanentError("ocr_failed")
    return pages
