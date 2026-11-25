"""Our own PDF writer (archive plan Task 10, AR10): one JPEG page each, embedded as it is,
checked by parsing our own output (no PDF library)."""

import io
import re

import pytest

from apps.documents.pdf import NotAJpegError, pdf_can_hold, write_pdf
from tests.documents_support import jpeg, jpeg_of_size


def _pdf(pages: list[bytes]) -> bytes:
    out = io.BytesIO()
    assert write_pdf(pages, out) == len(pages)
    return out.getvalue()


def test_pdf_has_one_page_per_jpeg_and_a_valid_cross_reference() -> None:
    pages = [jpeg("een"), jpeg_of_size(2480, 3508), jpeg("drie")]
    data = _pdf(pages)
    assert data.startswith(b"%PDF-1.4\n")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"/Type /Pages /Kids [5 0 R 8 0 R 11 0 R] /Count 3" in data
    assert data.count(b"/Type /Page ") == 3
    # Every page holds its JPEG byte for byte.
    for page in pages:
        assert page in data
    # An A4 scan at 300 dpi becomes a page of its pixels at 144 dpi.
    assert b"/MediaBox [0 0 1240 1754]" in data
    # The cross-reference table points at each object.
    start = int(re.findall(rb"startxref\n(\d+)\n", data)[-1])
    assert data[start:].startswith(b"xref\n0 12\n")
    entries = re.findall(rb"(\d{10}) 00000 n \n", data[start:])
    assert len(entries) == 11
    for number, offset in enumerate(entries, start=1):
        assert data[int(offset) :].startswith(f"{number} 0 obj\n".encode())


def test_no_pages_is_an_empty_but_valid_pdf() -> None:
    data = _pdf([])
    assert b"/Count 0" in data
    assert data.rstrip().endswith(b"%%EOF")


def test_a_page_that_is_no_jpeg_is_refused() -> None:
    assert not pdf_can_hold(b"not a picture")
    assert pdf_can_hold(jpeg())
    with pytest.raises(NotAJpegError):
        write_pdf([jpeg(), b"not a picture"], io.BytesIO())
