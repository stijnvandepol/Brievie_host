"""The parsers that run inside `sandbox.run_isolated`'s child: no Django imports here.

PDFium is used without its form environment (`init_forms` is never called), so a PDF's
JavaScript never runs and nothing is fetched; Pillow decodes only PNG and JPEG and stops
at `MAX_IMAGE_PIXELS`. Each raises `ParseError(code)` with a code a user may see.
"""

import io
import warnings
from dataclasses import dataclass

import pypdfium2
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps

MAX_IMAGE_PIXELS = 40_000_000
RENDER_DPI = 200
MAX_SIDE = 2500
JPEG_QUALITY = 85
# A page whose own text layer has fewer non-space characters is read with OCR.
MIN_LAYER_CHARS = 20


class ParseError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedPage:
    jpeg: bytes
    # The PDF's own text when it has enough, else "" and `needs_ocr`.
    text: str
    needs_ocr: bool


def _open_pdf(data: bytes) -> pypdfium2.PdfDocument:
    try:
        return pypdfium2.PdfDocument(data)
    except pypdfium2.PdfiumError as error:
        if error.err_code == pdfium_c.FPDF_ERR_PASSWORD:
            raise ParseError("file_encrypted") from None
        raise ParseError("file_unreadable") from None


def pdf_page_count(data: bytes) -> int:
    document = _open_pdf(data)
    try:
        return len(document)
    finally:
        document.close()


def _jpeg(image: Image.Image) -> bytes:
    picture = image.convert("RGB")
    picture.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def render_pdf(data: bytes, max_pages: int) -> list[ParsedPage]:
    document = _open_pdf(data)
    try:
        if len(document) == 0:
            raise ParseError("file_unreadable")
        if len(document) > max_pages:
            raise ParseError("too_many_pages")
        pages: list[ParsedPage] = []
        for page in document:
            width, height = page.get_size()
            scale = min(RENDER_DPI / 72, MAX_SIDE / max(width, height, 1))
            bitmap = page.render(scale=scale, may_draw_forms=False)
            text = page.get_textpage().get_text_range()
            layer = sum(1 for char in text if not char.isspace()) >= MIN_LAYER_CHARS
            pages.append(
                ParsedPage(
                    jpeg=_jpeg(bitmap.to_pil()), text=text if layer else "", needs_ocr=not layer
                )
            )
        return pages
    except pypdfium2.PdfiumError:
        raise ParseError("file_unreadable") from None
    finally:
        document.close()


def render_image(data: bytes) -> list[ParsedPage]:
    """A JPEG or PNG as one page: EXIF orientation applied, re-encoded (which also drops
    the EXIF data, such as a phone's location), longest side at most `MAX_SIDE`."""
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    try:
        with Image.open(io.BytesIO(data), formats=["JPEG", "PNG"]) as image:
            upright = ImageOps.exif_transpose(image)
            return [ParsedPage(jpeg=_jpeg(upright), text="", needs_ocr=True)]
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ParseError("image_too_large") from None
    except (OSError, ValueError, SyntaxError):
        raise ParseError("file_unreadable") from None
