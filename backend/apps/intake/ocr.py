"""Reading text from a page image with Tesseract (intake spec S 1.1), behind an interface
with a fake for tests (SPEC 19). The binary runs as its own process: no shell, the page
on stdin, a time limit per page, one thread. Nothing of the page or its text is logged."""

import os
import subprocess
from typing import Protocol

from django.conf import settings

LANGUAGES = "nld+eng"


class OcrFailedError(Exception):
    """Tesseract is missing, timed out or failed on this page."""


class OcrEngine(Protocol):
    def read(self, jpeg: bytes, *, timeout: float) -> str:
        """The page's text; at most `timeout` seconds (also capped by the engine's own)."""
        ...


class TesseractOcr:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    def read(self, jpeg: bytes, *, timeout: float) -> str:
        try:
            result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
                ["tesseract", "stdin", "stdout", "-l", LANGUAGES],  # noqa: S607
                input=jpeg,
                capture_output=True,
                timeout=min(self.timeout, timeout),
                check=False,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "OMP_THREAD_LIMIT": "1"},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise OcrFailedError("ocr_failed") from None
        if result.returncode != 0:
            raise OcrFailedError("ocr_failed")
        return result.stdout.decode("utf-8", errors="replace")


def ocr_engine() -> OcrEngine:
    return TesseractOcr(timeout=settings.BRIEVIE_OCR_TIMEOUT_SECONDS)
