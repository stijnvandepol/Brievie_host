"""The structural check on an uploaded page (no database needed).

A 3-byte "JPEG" (FF D8 FF) was accepted and went to the paid AI service. The server
never decodes a page, so it checks the structure a decoder needs first: the start of
image, a frame header with the picture's size and a whole scan header.
"""

import base64
import re
from pathlib import Path

import pytest

from apps.documents.jpeg import MAX_PIXELS, MAX_SIDE, jpeg_size
from tests.documents_support import jpeg, jpeg_of_size

SEED = (Path(__file__).resolve().parents[2] / ".github" / "scripts" / "restore-seed.py").read_text()


def test_a_real_baseline_jpeg_has_its_size() -> None:
    assert jpeg_size(jpeg()) == (1, 1)
    assert jpeg_size(jpeg("x", size=5000)) == (1, 1)
    assert jpeg_size(jpeg_of_size(2480, 3508)) == (2480, 3508)


def test_the_restore_seed_page_passes() -> None:
    encoded = "".join(re.findall(r'^\s+"([A-Za-z0-9+/=]+)"$', SEED.split("_BASE_JPEG", 1)[1], re.M))
    assert jpeg_size(base64.b64decode(encoded)) == (1, 1)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff\xd8\xff",  # the finding
        b"\xff\xd8\xff\xe0" + bytes(2000),  # a start and nothing a decoder can use
        b"\x89PNG\r\n\x1a\n" + bytes(100),
        jpeg()[:40],  # cut off before the frame header
        jpeg().rpartition(b"\xff\xda")[0],  # no scan
        jpeg().rpartition(b"\xff\xda")[0] + b"\xff\xda\x00\x08\x01",  # a cut scan header
        jpeg_of_size(0, 100),
        jpeg_of_size(100, 0),
    ],
)
def test_what_is_no_usable_jpeg_has_no_size(data: bytes) -> None:
    assert jpeg_size(data) is None


def test_limits() -> None:
    assert MAX_SIDE == 20_000
    assert MAX_PIXELS == 100_000_000


def _frame(width: int, height: int) -> bytes:
    # SOF0: length 11, precision 8, height, width, one component.
    return (
        b"\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00"
    )


def test_a_second_frame_header_is_refused() -> None:
    # Security review 2026-11-20 (data L2): a decoder that reads the first header would
    # allocate for 65000 x 65000 while the check saw only the last one.
    scan = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    data = b"\xff\xd8" + _frame(65000, 65000) + _frame(100, 100) + scan

    assert jpeg_size(data) is None
    assert jpeg_size(b"\xff\xd8" + _frame(100, 100) + scan) == (100, 100)
