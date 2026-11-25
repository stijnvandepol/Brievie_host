"""File checks before anything is stored (intake plan Task 2). No database."""

import hashlib

import pytest

from apps.intake.files import (
    FileKind,
    FileRefusedError,
    check_file,
    download_name,
    safe_original_name,
    sniff,
)
from tests.intake_fixtures import (
    make_encrypted_pdf,
    make_jpeg,
    make_pdf,
    make_png,
    png_header_only,
)

MB = 1024 * 1024


def _refused(data: bytes, *, max_bytes: int = 32 * MB, max_pages: int = 30) -> str:
    with pytest.raises(FileRefusedError) as caught:
        check_file(data, max_bytes=max_bytes, max_pages=max_pages)
    return caught.value.code


def test_each_kind_is_sniffed_from_the_content() -> None:
    assert sniff(make_pdf(["Een brief"])) is FileKind.PDF
    assert sniff(make_jpeg(20, 10)) is FileKind.JPEG
    assert sniff(make_png(20, 10)) is FileKind.PNG


def test_other_content_is_not_supported() -> None:
    assert _refused(b"GIF89a" + bytes(40)) == "file_type_not_supported"
    assert _refused(b"<html><body>brief.pdf</body></html>") == "file_type_not_supported"


def test_empty_file_refused() -> None:
    assert _refused(b"") == "file_type_not_supported"


def test_size_is_checked_first() -> None:
    data = make_pdf(["Een brief"])
    assert _refused(data, max_bytes=len(data) - 1) == "file_too_large"
    assert check_file(data, max_bytes=len(data), max_pages=30).size == len(data)


def test_too_many_pages() -> None:
    assert _refused(make_pdf(["Pagina"] * 31), max_pages=30) == "too_many_pages"
    assert check_file(make_pdf(["Pagina"] * 30), max_bytes=32 * MB, max_pages=30).page_count == 30


def test_encrypted_pdf_refused() -> None:
    assert _refused(make_encrypted_pdf()) == "file_encrypted"


def test_damaged_pdf_unreadable() -> None:
    assert _refused(make_pdf(["Een brief"])[:60]) == "file_unreadable"


def test_damaged_image_unreadable() -> None:
    assert _refused(make_jpeg(20, 10)[:30]) == "file_unreadable"
    assert _refused(make_png(20, 10)[:20]) == "file_unreadable"


def test_huge_image_header_refused_before_decoding() -> None:
    assert _refused(png_header_only(10_000, 5_000)) == "image_too_large"


def test_checked_file_facts() -> None:
    data = make_jpeg(30, 20)
    checked = check_file(data, max_bytes=32 * MB, max_pages=30)
    assert checked.kind is FileKind.JPEG
    assert checked.page_count == 1
    assert checked.sha256 == hashlib.sha256(data).hexdigest()


def test_original_name_base_only() -> None:
    assert safe_original_name("C:\\fakepath\\brief.pdf") == "brief.pdf"
    assert safe_original_name("map/sub/scan.png") == "scan.png"
    assert safe_original_name("rek\x00ening\n.pdf") == "rekening.pdf"
    # 111222333 passes the elfproef: a BSN in a name is masked.
    assert "[BSN]" in safe_original_name("brief 111222333.pdf")
    assert "111222333" not in safe_original_name("brief 111222333.pdf")
    assert len(safe_original_name("a" * 300 + ".pdf")) == 200


@pytest.mark.parametrize(
    "mark", ["\u202e", "\u202a", "\u2066", "\u2069", "\u200e", "\u200f", "\u061c", "\u2028"]
)
def test_original_name_drops_direction_marks(mark: str) -> None:
    # Security review of PR #58: "rekening<RLO>fdp.exe" shows as "rekeningexe.pdf".
    assert safe_original_name(f"rekening{mark}fdp.exe") == "rekeningfdp.exe"


@pytest.mark.parametrize(
    ("name", "kind", "expected"),
    [
        ("aanslag 2026.pdf", FileKind.PDF, "aanslag 2026.pdf"),
        ("brief.exe", FileKind.PDF, "brief.pdf"),
        ("foto.pdf", FileKind.JPEG, "foto.jpg"),
        ("scan", FileKind.PNG, "scan.png"),
        ("factuur.2026.html", FileKind.PDF, "factuur.2026.pdf"),
        ("", FileKind.PDF, "origineel.pdf"),
        (".pdf", FileKind.PDF, "origineel.pdf"),
        ("rekening\u202efdp.exe", FileKind.PDF, "rekeningfdp.pdf"),
    ],
)
def test_the_download_name_takes_its_extension_from_the_content(
    name: str, kind: FileKind, expected: str
) -> None:
    assert download_name(name, kind) == expected
