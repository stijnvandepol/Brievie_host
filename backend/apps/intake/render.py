"""An uploaded file to JPEG pages and their text (intake spec S 1.1), in the worker.

PDFium and Pillow run in a child process (`sandbox.run_isolated`); OCR runs Tesseract per
page that has no usable text layer. The text is returned unmasked: the caller masks it
before it is stored. Failures are `PermanentError` with a code `mark_failed` shows.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from django.conf import settings

from apps.intake import parsers
from apps.intake.files import FileKind
from apps.intake.ocr import OcrEngine, OcrFailedError
from apps.intake.sandbox import IsolationError, run_isolated
from apps.pipeline.errors import PermanentError

# Rendering 30 pages at 200 dpi takes seconds; this bounds a file built to be slow.
RENDER_TIMEOUT_BASE = 30.0
RENDER_TIMEOUT_PER_PAGE = 5.0
_KNOWN = frozenset({"file_unreadable", "file_encrypted", "too_many_pages", "image_too_large"})


@dataclass(frozen=True)
class RenderedPage:
    jpeg: bytes
    text: str


def render_pages(data: bytes, kind: FileKind, ocr: OcrEngine) -> list[RenderedPage]:
    max_pages = settings.BRIEVIE_MAX_PAGES
    target: Callable[[bytes], list[parsers.ParsedPage]]
    if kind is FileKind.PDF:
        target = partial(parsers.render_pdf, max_pages=max_pages)
        timeout = RENDER_TIMEOUT_BASE + RENDER_TIMEOUT_PER_PAGE * max_pages
    else:
        target = parsers.render_image
        timeout = RENDER_TIMEOUT_BASE
    try:
        parsed = run_isolated(target, data, timeout=timeout)
    except IsolationError as error:
        code = str(error)
        raise PermanentError(code if code in _KNOWN else "file_unreadable") from None

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
