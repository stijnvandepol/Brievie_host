"""An uploaded file to JPEG pages and their text (intake spec S 1.1), in the worker.

PDFium and Pillow run in a child process (`apps.intake.sandbox`); OCR runs Tesseract per
page that has no usable text layer. The text is returned unmasked: the caller masks it
before it is stored. Failures are `PermanentError` with a code `mark_failed` shows.
"""

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


def render_pages(data: bytes, kind: FileKind, ocr: OcrEngine) -> list[RenderedPage]:
    max_pages = settings.BRIEVIE_MAX_PAGES
    if kind is FileKind.PDF:
        timeout = RENDER_TIMEOUT_BASE + RENDER_TIMEOUT_PER_PAGE * max_pages
    else:
        timeout = RENDER_TIMEOUT_BASE
    try:
        parsed = sandbox.render(data, kind=kind.value, max_pages=max_pages, timeout=timeout)
    except IsolationError as error:
        raise PermanentError(str(error) or "file_unreadable") from None

    pages: list[RenderedPage] = []
    needed = failed = 0
    for page in parsed:
        text = page.text
        if page.needs_ocr:
            needed += 1
            try:
                text = ocr.read(page.jpeg)
            except OcrFailedError:
                failed += 1
                text = ""
        pages.append(RenderedPage(jpeg=page.jpeg, text=text))
    if needed and failed == needed:
        raise PermanentError("ocr_failed")
    return pages
