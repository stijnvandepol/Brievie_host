"""The parsers that run in `apps.intake.sandbox`'s child, as a script:
`python -I parsers.py <operation> <cpu seconds> <memory bytes> <answer bytes> [max pages]`
with the file on stdin and one JSON object on stdout. No Django and nothing of this project
is imported here: the child starts with no settings, keys or database.

PDFium is used without its form environment (`init_forms` is never called), so a PDF's
JavaScript never runs and nothing is fetched; Pillow decodes only PNG and JPEG and stops
at `MAX_IMAGE_PIXELS`. Each raises `ParseError(code)` with a code a user may see.
"""

import base64
import io
import json
import sys
import warnings
from dataclasses import dataclass

import pypdfium2
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps

MAX_IMAGE_PIXELS = 40_000_000
RENDER_DPI = 200
MAX_SIDE = 2500
JPEG_QUALITY = 85


class ParseError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedPage:
    """A page as a picture only: its text is read from that picture by OCR. A PDF's own
    text layer is never read, as it can say something the page does not show (security
    review of PR #58)."""

    jpeg: bytes


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
            pages.append(ParsedPage(jpeg=_jpeg(bitmap.to_pil())))
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
            return [ParsedPage(jpeg=_jpeg(upright))]
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ParseError("image_too_large") from None
    except (OSError, ValueError, SyntaxError):
        raise ParseError("file_unreadable") from None


def set_limits(*, cpu_seconds: int, memory_bytes: int, answer_bytes: int) -> None:
    """The child's own limits, set before it reads the file: address space, CPU time and
    the size of any file it writes (its answer). macOS refuses `RLIMIT_AS`; the parent's
    timeout and answer limit still hold there."""
    try:
        import resource
    except ImportError:  # pragma: no cover - not on Linux or macOS
        return
    for limit, value in (
        (resource.RLIMIT_AS, memory_bytes),
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, answer_bytes),
    ):
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            continue


def _page(page: ParsedPage) -> dict[str, object]:
    return {"jpeg": base64.b64encode(page.jpeg).decode("ascii")}


def _answer(operation: str, data: bytes, max_pages: int) -> dict[str, object]:
    try:
        if operation == "count":
            return {"ok": True, "pages": pdf_page_count(data)}
        if operation == "pdf":
            return {"ok": True, "pages": [_page(page) for page in render_pdf(data, max_pages)]}
        if operation == "image":
            return {"ok": True, "pages": [_page(page) for page in render_image(data)]}
    except ParseError as error:
        return {"ok": False, "code": error.code}
    raise ValueError("unknown operation")


def main(arguments: list[str]) -> int:
    operation, cpu, memory, answer_bytes, *rest = arguments
    set_limits(cpu_seconds=int(cpu), memory_bytes=int(memory), answer_bytes=int(answer_bytes))
    data = sys.stdin.buffer.read()
    answer = _answer(operation, data, int(rest[0]) if rest else 1)
    sys.stdout.write(json.dumps(answer))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
