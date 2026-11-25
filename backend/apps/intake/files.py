"""Checks of an uploaded file before anything is stored (intake spec S 1.2). Pure: no
database. The kind comes from the content, never from the name or the browser's type;
size first, then kind, then pages or pixels. Images are measured from their header only
(no decoder in the request); a PDF is opened by PDFium in a child process with a time
limit (`sandbox.run_isolated`)."""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from apps.common.privacy import mask_bsns
from apps.documents.jpeg import jpeg_size
from apps.intake import parsers
from apps.intake.parsers import MAX_IMAGE_PIXELS
from apps.intake.sandbox import IsolationError, run_isolated

ORIGINAL_NAME_MAX = 200
PAGE_COUNT_TIMEOUT = 5.0
_PNG = b"\x89PNG\r\n\x1a\n"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class FileKind(StrEnum):
    # Equal to `Document.OriginalKind`.
    PDF = "pdf"
    JPEG = "jpeg"
    PNG = "png"


class FileRefusedError(Exception):
    """`code`: file_type_not_supported, file_too_large, too_many_pages, file_encrypted,
    file_unreadable or image_too_large."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CheckedFile:
    kind: FileKind
    page_count: int
    size: int
    sha256: str


def sniff(data: bytes) -> FileKind:
    if data.startswith(b"%PDF-"):
        return FileKind.PDF
    if data.startswith(b"\xff\xd8\xff"):
        return FileKind.JPEG
    if data.startswith(_PNG):
        return FileKind.PNG
    raise FileRefusedError("file_type_not_supported")


def png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the IHDR chunk, which must come first."""
    if len(data) < 24 or not data.startswith(_PNG) or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return (width, height) if width > 0 and height > 0 else None


def check_file(data: bytes, *, max_bytes: int, max_pages: int) -> CheckedFile:
    if len(data) > max_bytes:
        raise FileRefusedError("file_too_large")
    kind = sniff(data)
    if kind is FileKind.PDF:
        pages = pdf_page_count(data, timeout=PAGE_COUNT_TIMEOUT)
        if pages == 0:
            raise FileRefusedError("file_unreadable")
        if pages > max_pages:
            raise FileRefusedError("too_many_pages")
    else:
        # A JPEG frame beyond `jpeg.MAX_PIXELS` (100 MP) is already None: unreadable.
        size = png_size(data) if kind is FileKind.PNG else jpeg_size(data)
        if size is None:
            raise FileRefusedError("file_unreadable")
        if size[0] * size[1] > MAX_IMAGE_PIXELS:
            raise FileRefusedError("image_too_large")
        pages = 1
    return CheckedFile(
        kind=kind, page_count=pages, size=len(data), sha256=hashlib.sha256(data).hexdigest()
    )


def pdf_page_count(data: bytes, *, timeout: float = PAGE_COUNT_TIMEOUT) -> int:
    """PDFium counts the pages in a child process, killed after `timeout` seconds."""
    try:
        return run_isolated(parsers.pdf_page_count, data, timeout=timeout)
    except IsolationError as error:
        code = str(error)
        raise FileRefusedError(
            code if code in {"file_encrypted", "file_unreadable"} else "file_unreadable"
        ) from None


def safe_original_name(name: str) -> str:
    """The base name a person recognises: no folders (a browser may send
    `C:\\fakepath\\brief.pdf`), no control characters, BSN-masked, at most 200 characters."""
    base = re.split(r"[/\\]", name)[-1]
    return mask_bsns(_CONTROL.sub("", base).strip())[:ORIGINAL_NAME_MAX]
