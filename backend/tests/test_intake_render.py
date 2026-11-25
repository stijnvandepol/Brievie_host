"""Rendering an uploaded file to pages and text (intake plan Task 3). No database."""

import io
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from apps.documents.jpeg import jpeg_size
from apps.intake.files import FileKind
from apps.intake.ocr import TesseractOcr
from apps.intake.render import render_pages
from apps.pipeline.errors import PermanentError
from tests.intake_fixtures import (
    FakeOcr,
    make_encrypted_pdf,
    make_jpeg,
    make_pdf,
    make_png,
)

LAYER = "Gemeente Voorbeeldstad, aanslag afvalstoffenheffing 2026"


def _failure(data: bytes, kind: FileKind) -> str:
    with pytest.raises(PermanentError) as caught:
        render_pages(data, kind, FakeOcr())
    return caught.value.code


def test_a_text_layer_is_never_trusted() -> None:
    """Security review of PR #58, point 4: a PDF's own text can differ from what the page
    shows (white or hidden text, another amount, an instruction to the AI). Every page is
    read from its picture; the layer is not used."""
    ocr = FakeOcr("Wat de pagina laat zien")
    pages = render_pages(make_pdf([LAYER]), FileKind.PDF, ocr)
    assert [page.text for page in pages] == ["Wat de pagina laat zien"]
    assert len(ocr.calls) == 1


def test_image_only_page_is_read_once() -> None:
    ocr = FakeOcr("Gelezen tekst")
    pages = render_pages(make_pdf([None]), FileKind.PDF, ocr)
    assert [page.text for page in pages] == ["Gelezen tekst"]
    assert len(ocr.calls) == 1


def test_mixed_pdf_is_read_from_its_pictures() -> None:
    ocr = FakeOcr("Gelezen tekst")
    pages = render_pages(make_pdf([LAYER, None]), FileKind.PDF, ocr)
    assert [page.text for page in pages] == ["Gelezen tekst", "Gelezen tekst"]
    assert len(ocr.calls) == 2


def test_every_page_is_a_jpeg_within_the_size() -> None:
    pages = render_pages(make_pdf([LAYER, None]), FileKind.PDF, FakeOcr())
    for page in pages:
        size = jpeg_size(page.jpeg)
        assert size is not None
        assert max(size) <= 2500
    # A4 (595 x 842 pt) at 200 dpi.
    assert jpeg_size(pages[0].jpeg) == (1653, 2339)


def test_large_photo_is_scaled_down() -> None:
    pages = render_pages(make_jpeg(3000, 1000), FileKind.JPEG, FakeOcr())
    size = jpeg_size(pages[0].jpeg)
    assert size is not None
    assert max(size) == 2500


def test_jpeg_exif_orientation_applied() -> None:
    pages = render_pages(make_jpeg(400, 200, orientation=6), FileKind.JPEG, FakeOcr())
    size = jpeg_size(pages[0].jpeg)
    assert size is not None
    width, height = size
    assert width < height


def test_exif_is_not_kept() -> None:
    pages = render_pages(make_jpeg(400, 200, orientation=6), FileKind.JPEG, FakeOcr())
    with Image.open(io.BytesIO(pages[0].jpeg)) as image:
        assert not image.getexif()


def test_png_becomes_jpeg() -> None:
    pages = render_pages(make_png(300, 200), FileKind.PNG, FakeOcr("PNG tekst"))
    assert jpeg_size(pages[0].jpeg) == (300, 200)
    assert pages[0].text == "PNG tekst"


def test_refused_files_have_their_codes() -> None:
    assert _failure(make_encrypted_pdf(), FileKind.PDF) == "file_encrypted"
    assert _failure(make_pdf([LAYER])[:80], FileKind.PDF) == "file_unreadable"
    assert _failure(make_pdf(["Pagina"] * 31), FileKind.PDF) == "too_many_pages"
    assert _failure(make_jpeg(30, 20)[:40], FileKind.JPEG) == "file_unreadable"


def test_ocr_failing_on_every_page_fails() -> None:
    with pytest.raises(PermanentError) as caught:
        render_pages(make_pdf([None, None]), FileKind.PDF, FakeOcr(fail_on=frozenset({1, 2})))
    assert caught.value.code == "ocr_failed"


def test_ocr_failing_on_one_page_leaves_it_empty() -> None:
    pages = render_pages(
        make_pdf([None, None]), FileKind.PDF, FakeOcr("Tekst", fail_on=frozenset({1}))
    )
    assert [page.text for page in pages] == ["", "Tekst"]


def test_missing_tesseract_is_ocr_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(PermanentError) as caught:
        render_pages(make_jpeg(300, 200), FileKind.JPEG, TesseractOcr(timeout=5))
    assert caught.value.code == "ocr_failed"


@pytest.mark.tesseract
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")
def test_real_tesseract_reads_dutch() -> None:
    picture = Image.new("RGB", (1600, 300), "white")
    ImageDraw.Draw(picture).text(
        (60, 100), "Betaal vóór 3 december 2026", font=ImageFont.load_default(size=64), fill="black"
    )
    out = io.BytesIO()
    picture.save(out, "PNG")
    pages = render_pages(out.getvalue(), FileKind.PNG, TesseractOcr(timeout=60))
    assert "3 december 2026" in pages[0].text
