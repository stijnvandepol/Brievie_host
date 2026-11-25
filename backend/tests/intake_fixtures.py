"""Files for the intake tests, written by hand (no fixtures on disk): PDFs with a text
layer or an image-only page, a password-protected PDF, JPEGs with EXIF orientation, PNGs,
and a fake OCR engine."""

import io
import secrets
import struct
import zlib

from PIL import Image

from apps.intake.ocr import OcrFailedError

_FILE_ID = bytes(range(16))


def make_jpeg(width: int, height: int, *, orientation: int | None = None) -> bytes:
    picture = Image.new("RGB", (width, height), (240, 240, 240))
    # A dark left half: rotation is visible in the result.
    picture.paste((20, 20, 20), (0, 0, width // 2, height))
    out = io.BytesIO()
    if orientation is None:
        picture.save(out, "JPEG", quality=80)
    else:
        exif = Image.Exif()
        exif[0x0112] = orientation
        picture.save(out, "JPEG", quality=80, exif=exif.tobytes())
    return out.getvalue()


def make_png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (250, 250, 250)).save(out, "PNG")
    return out.getvalue()


def png_header_only(width: int, height: int) -> bytes:
    """A PNG signature and an IHDR claiming this size, with one tiny IDAT: what a
    decompression bomb's header looks like."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00"))
        + chunk(b"IEND", b"")
    )


def _escape(text: str) -> bytes:
    raw = text.encode("latin-1")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _assemble(objects: list[bytes], trailer_extra: bytes = b"") -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R ".encode()
        + trailer_extra
        + f">>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def _stream(data: bytes, extra: bytes = b"") -> bytes:
    return (
        b"<< /Length " + str(len(data)).encode() + extra + b" >>\nstream\n" + data + b"\nendstream"
    )


def make_pdf(pages: list[str | None]) -> bytes:
    """A str page has a Helvetica text layer; None is an image-only page (a JPEG)."""
    objects: list[bytes] = [b"", b""]  # 1 catalog, 2 pages: filled in below
    font = len(objects) + 1
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for text in pages:
        if text is None:
            jpeg = make_jpeg(400, 300)
            image = len(objects) + 1
            objects.append(
                _stream(
                    jpeg,
                    b" /Type /XObject /Subtype /Image /Width 400 /Height 300"
                    b" /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode",
                )
            )
            content = b"q 400 0 0 300 100 400 cm /Im1 Do Q"
            resources = f"<< /XObject << /Im1 {image} 0 R >> >>".encode()
        else:
            content = b"BT /F1 14 Tf 72 720 Td (" + _escape(text) + b") Tj ET"
            resources = f"<< /Font << /F1 {font} 0 R >> >>".encode()
        stream = len(objects) + 1
        objects.append(_stream(content))
        kids.append(len(objects) + 1)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources "
            + resources
            + f" /Contents {stream} 0 R >>".encode()
        )
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = (
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{kid} 0 R".encode() for kid in kids)
        + f"] /Count {len(kids)} >>".encode()
    )
    return _assemble(objects)


def make_encrypted_pdf() -> bytes:
    """A one-page PDF under the standard security handler (revision 3, 128 bits) that no
    password opens: its /O and /U entries are random bytes, so the empty password fails
    and PDFium asks for one before anything else. No key is derived, so no password hash
    is computed here."""
    permissions = -4
    owner = secrets.token_bytes(32)
    user = secrets.token_bytes(32)
    plain = make_pdf(["Vertrouwelijk"])
    body = plain[: plain.index(b"xref\n")]
    objects = [part.split(b" 0 obj\n", 1)[1] for part in body.split(b"\nendobj\n")[:-1]]
    encrypt = len(objects) + 1
    objects.append(
        b"<< /Filter /Standard /V 2 /R 3 /Length 128 /P "
        + str(permissions).encode()
        + b" /O <"
        + owner.hex().encode()
        + b"> /U <"
        + user.hex().encode()
        + b"> >>"
    )
    trailer = f"/Encrypt {encrypt} 0 R /ID [<{_FILE_ID.hex()}> <{_FILE_ID.hex()}>] ".encode()
    return _assemble(objects, trailer)


class FakeClock:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeOcr:
    """Returns `text` per call, records the calls and the time each was given, fails on
    chosen call numbers (1-based); moves `clock` by `seconds` per call."""

    def __init__(
        self,
        text: str = "Tekst van de scan",
        *,
        fail_on: frozenset[int] = frozenset(),
        clock: FakeClock | None = None,
        seconds: float = 0.0,
    ) -> None:
        self.text = text
        self.fail_on = fail_on
        self.clock = clock
        self.seconds = seconds
        self.calls: list[bytes] = []
        self.timeouts: list[float] = []

    def read(self, jpeg: bytes, *, timeout: float) -> str:
        self.calls.append(jpeg)
        self.timeouts.append(timeout)
        if self.clock is not None:
            self.clock.now += self.seconds
        if len(self.calls) in self.fail_on:
            raise OcrFailedError("ocr_failed")
        return self.text
