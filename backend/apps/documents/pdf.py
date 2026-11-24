"""A minimal PDF writer for scanned pages (archive spec S 9, AR10): one JPEG per page,
embedded as it is (`DCTDecode`), no new dependency and no decoding.

Pure and streamed: pages come in one at a time and are written to `out` straight away; the
writer keeps only the offsets for the cross-reference table. A page is as large as its
picture at 144 dots per inch. There is no text layer yet (the export's JSON holds the
text). Version 1.4, readable by every viewer.
"""

from collections.abc import Iterable
from typing import IO, Final

from apps.documents.jpeg import jpeg_frame

# Points per pixel: a scan is shown at 144 dpi (72 points an inch).
_POINTS_PER_PIXEL: Final = 72 / 144
_COLOUR_SPACES: Final = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}
# The first objects: the catalog and the page tree; each page adds three.
_CATALOG: Final = 1
_PAGES: Final = 2


class NotAJpegError(ValueError):
    """A page is no JPEG a PDF can embed as it is."""


class _Writer:
    def __init__(self, out: IO[bytes]) -> None:
        self.out = out
        self.position = 0
        self.offsets: dict[int, int] = {}

    def write(self, data: bytes) -> None:
        self.out.write(data)
        self.position += len(data)

    def start(self, number: int) -> None:
        self.offsets[number] = self.position
        self.write(f"{number} 0 obj\n".encode())

    def object(self, number: int, body: str) -> None:
        self.start(number)
        self.write(body.encode() + b"\nendobj\n")

    def stream(self, number: int, dictionary: str, data: bytes) -> None:
        self.start(number)
        self.write(f"<< {dictionary} /Length {len(data)} >>\nstream\n".encode())
        self.write(data)
        self.write(b"\nendstream\nendobj\n")


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def pdf_can_hold(data: bytes) -> bool:
    """Whether `data` is a JPEG a PDF embeds as it is."""
    frame = jpeg_frame(data)
    return frame is not None and frame[2] in _COLOUR_SPACES


def write_pdf(pages: Iterable[bytes], out: IO[bytes]) -> int:
    """Write a PDF of `pages` (JPEG bytes, in order) to `out`; the number of pages. Raises
    `NotAJpegError` for a page that is no whole JPEG with 1, 3 or 4 colour components."""
    writer = _Writer(out)
    # The binary comment tells tools the file is binary.
    writer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    kids: list[int] = []
    for index, data in enumerate(pages):
        frame = jpeg_frame(data)
        if frame is None or frame[2] not in _COLOUR_SPACES:
            raise NotAJpegError(f"Page {index + 1} is no JPEG a PDF can hold.")
        width, height, components = frame
        image, content, page = (3 + index * 3, 4 + index * 3, 5 + index * 3)
        writer.stream(
            image,
            f"/Type /XObject /Subtype /Image /Width {width} /Height {height}"
            f" /ColorSpace {_COLOUR_SPACES[components]} /BitsPerComponent 8"
            " /Filter /DCTDecode",
            data,
        )
        page_width = _number(width * _POINTS_PER_PIXEL)
        page_height = _number(height * _POINTS_PER_PIXEL)
        writer.stream(content, "", f"q {page_width} 0 0 {page_height} 0 0 cm /Im0 Do Q".encode())
        writer.object(
            page,
            f"<< /Type /Page /Parent {_PAGES} 0 R /MediaBox [0 0 {page_width} {page_height}]"
            f" /Resources << /XObject << /Im0 {image} 0 R >> >> /Contents {content} 0 R >>",
        )
        kids.append(page)
    writer.object(
        _PAGES,
        f"<< /Type /Pages /Kids [{' '.join(f'{kid} 0 R' for kid in kids)}] /Count {len(kids)} >>",
    )
    writer.object(_CATALOG, f"<< /Type /Catalog /Pages {_PAGES} 0 R >>")
    size = max(writer.offsets) + 1
    start = writer.position
    lines = ["xref", f"0 {size}", "0000000000 65535 f "]
    lines += [f"{writer.offsets[number]:010d} 00000 n " for number in range(1, size)]
    trailer = f"trailer\n<< /Size {size} /Root {_CATALOG} 0 R >>\nstartxref\n{start}\n%%EOF\n"
    writer.write(("\n".join(lines) + "\n" + trailer).encode())
    return len(kids)
